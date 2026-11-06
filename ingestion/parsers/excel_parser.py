# ============================================================================
# Deliverable 1 - Excel parser (FULL implementation). REUSABLE.
# Ingests .xlsx/.xls workbooks into the canonical model: preserves sheet order
# (task 7), column names/order/positions (tasks 8-9), row/cell data (task 10),
# per-file/sheet metadata (tasks 4-6), and detected data types/formats
# (tasks 11-12). This is the only fully implemented parser for the POC.
# ============================================================================
"""
ingestion.parsers.excel_parser
==============================
Production Excel (.xlsx/.xls) parser.

Uses pandas to read every sheet in original order and derive per-column
statistics and detected types/formats. openpyxl is used indirectly by pandas
as the .xlsx engine; sheet order is taken from ``pd.ExcelFile.sheet_names``,
which preserves the workbook's native ordering.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
from pandas.api import types as ptypes

from canonical.enums import DetectedFormat, FileFormat, IoRole
from canonical.models import (
    CanonicalColumn,
    CanonicalFile,
    CanonicalSheet,
    ColumnStats,
)
from ingestion.parsers.base import BaseFileParser, FileParseError
from ingestion import type_detection

logger = logging.getLogger(__name__)

# Capture the COMPLETE distinct value set during ingestion only when a column's
# cardinality is at or below this threshold. These low-cardinality columns are
# the list-of-values / dropdown candidates, and storing every distinct value
# lets downstream validation analysis emit an exhaustive ``allowedValues`` set
# instead of a 5-row sample. Higher-cardinality columns skip this (kept light).
_MAX_DISTINCT_CAPTURE = 50

# Strip currency/percent/grouping symbols before parsing string numerics.
_NUMERIC_STRIP = re.compile(r"[,$£€%\s]")

# Above this distinct-value count, skip structural-pattern inference (the column
# is high-cardinality free data where a single shape is unlikely / not useful).
_MAX_PATTERN_DISTINCT = 10000
# A single character-class run longer than this is treated as free text, not a
# structured code, so pattern inference backs off (avoids over-fitting prose).
_MAX_PATTERN_RUN = 40
# Separators emitted bare (not regex-escaped) so inferred patterns read like the
# corpus (e.g. '^[0-9]{7}-[0-9]{2,3}$'). All are regex-safe outside a class.
_SAFE_LITERALS = frozenset("-/_: ")


def _numeric_bounds(non_null: "pd.Series") -> tuple[float | None, float | None]:
    """Return (min, max) numeric bounds over a whole column, best-effort.

    Handles native numeric dtypes directly and attempts to parse object/string
    columns that hold currency/percent/comma-formatted numbers. Returns
    ``(None, None)`` when the column is not predominantly numeric.
    """
    if non_null.empty:
        return None, None
    if ptypes.is_numeric_dtype(non_null) and not ptypes.is_bool_dtype(non_null):
        try:
            return float(non_null.min()), float(non_null.max())
        except (TypeError, ValueError):
            return None, None

    parsed: list[float] = []
    for v in non_null.tolist():
        token = _NUMERIC_STRIP.sub("", str(v))
        if token in ("", "-", "+", "."):
            continue
        try:
            parsed.append(float(token))
        except (TypeError, ValueError):
            # Not a numeric column - bail out rather than report partial bounds.
            return None, None
    if not parsed:
        return None, None
    return min(parsed), max(parsed)


def _numeric_precision(non_null: "pd.Series") -> tuple[int | None, int | None]:
    """Return (max_integer_digits, max_decimal_digits) over the WHOLE column.

    Measures the widest integer part and the deepest fractional part across
    every value, so a numeric ``dataFormat`` mask (e.g. ``xxxxx.xx``) reflects
    the full column. ``(None, None)`` when the column is not numeric.
    """
    if non_null.empty:
        return None, None
    max_int = 0
    max_dec = 0
    saw_any = False
    for v in non_null.tolist():
        token = _NUMERIC_STRIP.sub("", str(v))
        if token in ("", "-", "+", "."):
            continue
        token = token.lstrip("+-")
        try:
            float(token)
        except (TypeError, ValueError):
            return None, None
        saw_any = True
        int_part, _, dec_part = token.partition(".")
        max_int = max(max_int, len(int_part.lstrip("0")) or 1)
        max_dec = max(max_dec, len(dec_part))
    if not saw_any:
        return None, None
    return max_int, max_dec


def _tokenize_runs(value: str) -> list[tuple[str, int]]:
    """Collapse a string into consecutive (char-class, run-length) tokens.

    Digit runs -> ``('D', n)``; letter runs -> ``('A', n)``; any other single
    character is a literal token ``(ch, 1)`` (separators like ``-`` ``/`` ``.``).
    """
    out: list[tuple[str, int]] = []
    for ch in value:
        if ch.isdigit():
            kind = "D"
        elif ch.isalpha():
            kind = "A"
        else:
            out.append((ch, 1))
            continue
        if out and out[-1][0] == kind:
            out[-1] = (kind, out[-1][1] + 1)
        else:
            out.append((kind, 1))
    return out


def _infer_structural_pattern(non_null: "pd.Series") -> str | None:
    """Derive an anchored regex the WHOLE column conforms to, or ``None``.

    Every distinct value is reduced to a class-run signature; the pattern is
    emitted only when all values share one signature (same ordering of digit /
    letter / literal runs). Run lengths are merged into ``{min,max}`` quantifiers
    so the regex reflects the observed range exactly - e.g. a column of
    ``1234567-89`` / ``7654321-123`` yields ``^[0-9]{7}-[0-9]{2,3}$``.
    """
    if non_null.empty:
        return None
    vals = non_null.astype(str).str.strip()
    vals = vals[vals != ""]
    if vals.empty:
        return None
    distinct = vals.unique().tolist()
    if len(distinct) > _MAX_PATTERN_DISTINCT:
        return None

    signature: tuple[str, ...] | None = None
    literals: list[str] = []
    bounds: list[list[int]] = []  # [min,max] per token; literals carry [0,0]

    for value in distinct:
        tokens = _tokenize_runs(value)
        sig = tuple(kind for kind, _ in tokens)
        if signature is None:
            signature = sig
            for kind, length in tokens:
                if kind in ("D", "A"):
                    literals.append("")
                    bounds.append([length, length])
                else:
                    literals.append(kind)
                    bounds.append([0, 0])
        else:
            if sig != signature:
                return None  # columns' values disagree -> no single pattern
            for i, (kind, length) in enumerate(tokens):
                if kind in ("D", "A"):
                    bounds[i][0] = min(bounds[i][0], length)
                    bounds[i][1] = max(bounds[i][1], length)

    if signature is None:
        return None
    # Reject trivial / free-text shapes: a lone letter run is just prose.
    if signature == ("A",):
        return None
    # Reject over-long runs (descriptions, free text) to avoid over-fitting.
    for kind, (_, hi) in zip(signature, [(lo, hi) for lo, hi in bounds]):
        if kind in ("D", "A") and hi > _MAX_PATTERN_RUN:
            return None

    parts = ["^"]
    for kind, lit, (lo, hi) in zip(signature, literals, bounds):
        if kind == "D":
            cls = "[0-9]"
        elif kind == "A":
            cls = "[A-Za-z]"
        else:
            # Keep common separators bare (matches corpus '^[0-9]{7}-[0-9]{2,3}$')
            # and only regex-escape characters that actually need it.
            parts.append(lit if lit in _SAFE_LITERALS else re.escape(lit))
            continue
        parts.append(cls + (f"{{{lo}}}" if lo == hi else f"{{{lo},{hi}}}"))
    parts.append("$")
    return "".join(parts)


class ExcelParser(BaseFileParser):
    """Full parser for Excel workbooks (.xlsx/.xls)."""

    supported_formats = (FileFormat.XLSX, FileFormat.XLS)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse an Excel workbook into a :class:`CanonicalFile`."""
        try:
            excel_file = pd.ExcelFile(path)  # engine auto-selected by pandas
        except Exception as exc:  # noqa: BLE001 - normalize to domain error
            raise FileParseError(f"Failed to open Excel file '{path.name}': {exc}") from exc

        file_format = FileFormat.from_extension(path.suffix)
        canonical_file = CanonicalFile(
            name=path.name,
            path=str(path),
            file_format=file_format,
            io_role=io_role,
            size_bytes=self._file_size(path),
            metadata={"sheet_count": len(excel_file.sheet_names)},
        )

        # sheet_names preserves the workbook's native ordering (task 7).
        for order_index, sheet_name in enumerate(excel_file.sheet_names):
            try:
                df = excel_file.parse(sheet_name)
            except Exception as exc:  # noqa: BLE001
                raise FileParseError(
                    f"Failed to parse sheet '{sheet_name}' in '{path.name}': {exc}"
                ) from exc

            sheet = self._build_sheet(df, sheet_name, order_index, canonical_file.uuid)
            canonical_file.sheets.append(sheet)

        logger.info(
            "Parsed Excel file '%s' (%d sheet(s), role=%s)",
            path.name,
            len(canonical_file.sheets),
            io_role.value,
        )
        return canonical_file

    # -- internal helpers ---------------------------------------------------
    def _build_sheet(
        self,
        df: pd.DataFrame,
        sheet_name: str,
        order_index: int,
        file_uuid: str,
    ) -> CanonicalSheet:
        """Build a :class:`CanonicalSheet` from a sheet DataFrame."""
        sheet = CanonicalSheet(
            name=str(sheet_name),
            order_index=order_index,
            row_count=int(len(df)),
            file_uuid=file_uuid,
        )

        # Column order/position is the DataFrame column order (tasks 8-9).
        for position, col_name in enumerate(df.columns):
            series = df[col_name]
            sheet.columns.append(
                self._build_column(series, str(col_name), position, sheet.uuid)
            )
        return sheet

    def _build_column(
        self,
        series: pd.Series,
        col_name: str,
        position: int,
        sheet_uuid: str,
    ) -> CanonicalColumn:
        """Build a :class:`CanonicalColumn` with stats + detected type/format."""
        non_null = series.dropna()
        str_lengths = non_null.astype(str).map(len) if not non_null.empty else None

        unique_count = int(series.nunique(dropna=True))
        # For low-cardinality columns capture the FULL distinct value set so the
        # analyzer can build a complete list-of-values constraint; otherwise omit.
        distinct_values = None
        if 0 < unique_count <= _MAX_DISTINCT_CAPTURE and not non_null.empty:
            distinct_values = non_null.drop_duplicates().tolist()

        # Capture whole-column numeric bounds so value-range validation does not
        # rely on a 5-row sample. Best-effort: non-numeric columns stay None.
        min_numeric, max_numeric = _numeric_bounds(non_null)
        # Whole-column numeric precision -> numeric dataFormat mask (xxxxx.xx).
        max_integer_digits, max_decimal_digits = _numeric_precision(non_null)
        # Whole-column structural regex (e.g. fixed/var-length codes), when the
        # column has NO recognised higher-level format (date/email/uuid/number).
        detected_format = type_detection.detect_format(series)
        inferred_pattern = None
        if detected_format in (DetectedFormat.NONE, DetectedFormat.UNKNOWN):
            inferred_pattern = _infer_structural_pattern(non_null)

        stats = ColumnStats(
            non_null_count=int(series.notna().sum()),
            null_count=int(series.isna().sum()),
            unique_count=unique_count,
            min_length=int(str_lengths.min()) if str_lengths is not None else None,
            max_length=int(str_lengths.max()) if str_lengths is not None else None,
            min_numeric=min_numeric,
            max_numeric=max_numeric,
            max_integer_digits=max_integer_digits,
            max_decimal_digits=max_decimal_digits,
            inferred_pattern=inferred_pattern,
            distinct_values=distinct_values,
        )

        return CanonicalColumn(
            name=col_name,
            position=position,
            data_type=type_detection.detect_data_type(series),
            detected_format=detected_format,
            raw_dtype=str(series.dtype),
            stats=stats,
            sample_values=type_detection.sample_values(series),
            sheet_uuid=sheet_uuid,
        )
