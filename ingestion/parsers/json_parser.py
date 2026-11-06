# ============================================================================
# Deliverable 1 - JSON parser (FULL implementation). REUSABLE.
# Ingests .json files into the canonical model, mirroring the CSV/Excel parsers'
# column stats + type/format detection. Flattens JSON into one or more tabular
# sheets so downstream Profiling / Rule-Identification / IR layers consume the
# same CanonicalFile -> sheets -> columns shape regardless of source format.
# ============================================================================
"""
ingestion.parsers.json_parser
=============================
Production JSON parser.

JSON payloads come in several shapes; this parser normalizes each to the same
tabular canonical representation the Excel/CSV parsers produce:

* a top-level **list of objects** -> a single sheet ``Sheet1`` (one row per
  object), nested objects flattened to dotted columns (``a.b.c``);
* a top-level **object whose values are all lists of objects** -> one sheet per
  key (mirrors an Excel workbook's multiple sheets);
* any other top-level **object** (flat or nested) -> a single sheet ``Sheet1``
  with one row;
* a top-level **list of scalars** -> a single-column sheet ``Sheet1``.

All values are coerced to strings (nulls preserved) so the inherited
``_build_column`` type/format detection behaves exactly as it does for CSV.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile
from ingestion.parsers.base import FileParseError
from ingestion.parsers.excel_parser import ExcelParser

logger = logging.getLogger(__name__)

# Encodings tried in order; utf-8-sig strips a BOM if present.
_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


class JsonParser(ExcelParser):
    """Full parser for ``.json`` files (one or more flattened sheets).

    Inherits :meth:`ExcelParser._build_sheet` / ``_build_column`` so a JSON
    frame yields identical canonical columns to an Excel sheet or CSV.
    """

    supported_formats = (FileFormat.JSON,)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse a JSON file into a :class:`CanonicalFile`."""
        text, encoding = self._read_text(path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FileParseError(f"Failed to parse JSON '{path.name}': {exc}") from exc

        # Build the ordered (sheet_name -> DataFrame) mapping from the payload.
        frames = self._frames_from_payload(data)
        if not frames:
            raise FileParseError(
                f"JSON '{path.name}' contains no tabular data to ingest."
            )

        canonical_file = CanonicalFile(
            name=path.name,
            path=str(path),
            file_format=FileFormat.JSON,
            io_role=io_role,
            size_bytes=self._file_size(path),
            metadata={"sheet_count": len(frames), "encoding": encoding},
        )

        for order_index, (sheet_name, df) in enumerate(frames.items()):
            sheet = self._build_sheet(df, sheet_name, order_index, canonical_file.uuid)
            canonical_file.sheets.append(sheet)

        logger.info(
            "Parsed JSON file '%s' (encoding=%s, %d sheet(s), role=%s)",
            path.name, encoding, len(frames), io_role.value,
        )
        return canonical_file

    # -- payload -> frames --------------------------------------------------
    def _frames_from_payload(self, data: Any) -> dict[str, pd.DataFrame]:
        """Normalize an arbitrary JSON payload into ordered named frames."""
        # Case 1: top-level list.
        if isinstance(data, list):
            return {"Sheet1": self._frame_from_records(data)}

        # Case 2/3: top-level object.
        if isinstance(data, dict):
            # Multi-sheet: every value is a list of objects -> one sheet per key.
            if data and all(self._is_record_list(v) for v in data.values()):
                return {
                    str(key): self._frame_from_records(value)
                    for key, value in data.items()
                }
            # Single record (flat or nested) -> one row.
            return {"Sheet1": self._frame_from_records([data])}

        # Case 4: a bare scalar -> single cell.
        return {"Sheet1": self._stringify(pd.DataFrame({"value": [data]}))}

    def _frame_from_records(self, records: list[Any]) -> pd.DataFrame:
        """Build a flattened string DataFrame from a list of records/scalars."""
        if not records:
            return pd.DataFrame()
        # A list of scalars becomes a single ``value`` column.
        if all(not isinstance(r, (dict, list)) for r in records):
            return self._stringify(pd.DataFrame({"value": list(records)}))
        # Otherwise flatten nested objects to dotted columns.
        df = pd.json_normalize(records, sep=".")
        return self._stringify(df)

    @staticmethod
    def _is_record_list(value: Any) -> bool:
        """True when ``value`` is a non-empty list whose items are all objects."""
        return (
            isinstance(value, list)
            and len(value) > 0
            and all(isinstance(item, dict) for item in value)
        )

    @staticmethod
    def _stringify(df: pd.DataFrame) -> pd.DataFrame:
        """Coerce every cell to ``str`` while preserving nulls (mirrors CSV)."""
        def _cell(v: Any) -> Any:
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
            # Serialize nested containers deterministically rather than repr().
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False, sort_keys=True)
            return str(v)

        if df.empty:
            return df
        return df.apply(lambda s: s.map(_cell))

    # -- io -----------------------------------------------------------------
    @staticmethod
    def _read_text(path: Path) -> tuple[str, str]:
        """Read the file as text, trying a sequence of encodings."""
        last_exc: Exception | None = None
        for encoding in _ENCODINGS:
            try:
                return path.read_text(encoding=encoding), encoding
            except (UnicodeDecodeError, UnicodeError) as exc:
                last_exc = exc
                continue
            except OSError as exc:
                raise FileParseError(f"Failed to read JSON '{path.name}': {exc}") from exc
        try:
            return path.read_text(encoding="latin-1", errors="replace"), "latin-1"
        except OSError as exc:  # pragma: no cover - covered by loop above
            raise FileParseError(
                f"Failed to read JSON '{path.name}': {exc}"
            ) from last_exc or exc
