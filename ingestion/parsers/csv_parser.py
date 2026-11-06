# ============================================================================
# Deliverable 1 - CSV parser (FULL implementation). REUSABLE.
# Ingests .csv files into the canonical model as a single synthetic sheet
# ("Sheet1"), mirroring the Excel parser's column stats + type/format
# detection (tasks 8-12). Handles delimiter sniffing (comma/semicolon/tab/pipe),
# encoding detection (utf-8 -> utf-8-sig -> cp1252 -> latin-1), and headerless
# files (positional column names col_1, col_2, ...).
# ============================================================================
"""
ingestion.parsers.csv_parser
============================
Production CSV parser.

German payroll exports (and many EU CSVs) are semicolon-delimited and often
ship without a header row, so this parser sniffs the delimiter and header
presence rather than assuming comma-with-header. On success it produces a
single :class:`CanonicalSheet` named ``Sheet1`` whose columns carry the same
statistics and detected types/formats the Excel parser produces.
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

import pandas as pd

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile
from ingestion.parsers.base import FileParseError
from ingestion.parsers.excel_parser import ExcelParser

logger = logging.getLogger(__name__)

# Encodings tried in order; utf-8-sig strips a BOM if present.
_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
# Candidate delimiters for the sniffer (and the fallback scan).
_DELIMITERS: str = ";,\t|"
_SNIFF_BYTES: int = 65536


class CsvParser(ExcelParser):
    """Full parser for ``.csv`` files (single synthetic sheet).

    Inherits :meth:`ExcelParser._build_sheet` / ``_build_column`` so a CSV's
    single frame yields identical canonical columns to an Excel sheet.
    """

    supported_formats = (FileFormat.CSV,)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse a CSV file into a :class:`CanonicalFile`."""
        text, encoding = self._read_text(path)
        delimiter = self._sniff_delimiter(text)
        has_header = self._sniff_header(text, delimiter)

        try:
            df = pd.read_csv(
                io.StringIO(text),
                sep=delimiter,
                header=0 if has_header else None,
                dtype=str,
                keep_default_na=True,
                skip_blank_lines=True,
                engine="python",  # tolerant of ragged rows / odd quoting
            )
        except Exception as exc:  # noqa: BLE001 - normalize to domain error
            raise FileParseError(f"Failed to parse CSV '{path.name}': {exc}") from exc

        if not has_header:
            df.columns = [f"col_{i + 1}" for i in range(df.shape[1])]
        else:
            df.columns = [str(c) for c in df.columns]

        canonical_file = CanonicalFile(
            name=path.name,
            path=str(path),
            file_format=FileFormat.CSV,
            io_role=io_role,
            size_bytes=self._file_size(path),
            metadata={
                "sheet_count": 1,
                "delimiter": delimiter,
                "encoding": encoding,
                "has_header": has_header,
            },
        )

        sheet = self._build_sheet(df, "Sheet1", 0, canonical_file.uuid)
        canonical_file.sheets.append(sheet)

        logger.info(
            "Parsed CSV file '%s' (delimiter=%r, encoding=%s, header=%s, %d col(s), role=%s)",
            path.name, delimiter, encoding, has_header, df.shape[1], io_role.value,
        )
        return canonical_file

    # -- internal helpers ---------------------------------------------------
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
                raise FileParseError(f"Failed to read CSV '{path.name}': {exc}") from exc
        # Final fallback: latin-1 decodes any byte sequence.
        try:
            return path.read_text(encoding="latin-1", errors="replace"), "latin-1"
        except OSError as exc:  # pragma: no cover - covered by loop above
            raise FileParseError(f"Failed to read CSV '{path.name}': {exc}") from last_exc or exc

    @staticmethod
    def _sniff_delimiter(text: str) -> str:
        """Detect the delimiter via csv.Sniffer, falling back to a frequency scan."""
        sample = text[:_SNIFF_BYTES]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=_DELIMITERS)
            if dialect.delimiter:
                return dialect.delimiter
        except csv.Error:
            pass
        # Fallback: pick the candidate with the highest count on the first line.
        lines = sample.splitlines()
        first_line = lines[0] if lines else ""
        counts = {d: first_line.count(d) for d in _DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","

    @staticmethod
    def _sniff_header(text: str, delimiter: str) -> bool:
        """Best-effort header detection via csv.Sniffer.has_header."""
        sample = text[:_SNIFF_BYTES]
        try:
            return csv.Sniffer().has_header(sample)
        except csv.Error:
            return True  # default to assuming a header when unsure
