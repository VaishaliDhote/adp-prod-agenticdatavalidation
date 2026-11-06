# ============================================================================
# Deliverable 1 - PDF parser (SKELETON - implementation pending after POC).
# Conforms to BaseFileParser so it can be registered and swapped in later.
# Intended approach: extract tables with a PDF table library (pdfplumber /
# camelot), map each extracted table to a sheet, preserve column order, then
# reuse type/format detection. Requires adding a PDF dependency to requirements.
# ============================================================================
"""
ingestion.parsers.pdf_parser
============================
Skeleton PDF table parser. See module banner for the intended approach.
"""
from __future__ import annotations

from pathlib import Path

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile
from ingestion.parsers.base import BaseFileParser, ParserNotImplementedError


class PdfParser(BaseFileParser):
    """Skeleton parser for .pdf files (tabular extraction)."""

    supported_formats = (FileFormat.PDF,)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse a PDF file into a :class:`CanonicalFile`.

        Implementation pending (post-POC). Planned steps:
          1. Extract tables page-by-page (pdfplumber ``extract_tables`` or
             camelot) into DataFrames.
          2. Map each extracted table to a ``CanonicalSheet`` (preserving
             page/table order and column order).
          3. Reuse ``ingestion.type_detection`` for type/format/stats.
        """
        # TODO(post-poc): add pdfplumber/camelot to requirements and implement.
        raise ParserNotImplementedError(FileFormat.PDF)
