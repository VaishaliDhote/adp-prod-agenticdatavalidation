# ============================================================================
# Deliverable 1 - TXT parser (SKELETON - implementation pending after POC).
# Conforms to BaseFileParser so it can be registered and swapped in later.
# Intended approach: detect delimiter (tab/fixed-width/whitespace), read via
# pandas.read_csv/read_fwf into a single synthetic sheet, then reuse the shared
# type/format detection. Delimiter/layout heuristics are the main open work.
# ============================================================================
"""
ingestion.parsers.text_parser
=============================
Skeleton delimited/fixed-width TXT parser. See banner for the planned approach.
"""
from __future__ import annotations

from pathlib import Path

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile
from ingestion.parsers.base import BaseFileParser, ParserNotImplementedError


class TextParser(BaseFileParser):
    """Skeleton parser for .txt files (delimited or fixed-width)."""

    supported_formats = (FileFormat.TXT,)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse a TXT file into a :class:`CanonicalFile`.

        Implementation pending (post-POC). Planned steps:
          1. Detect layout: delimited (tab/pipe/comma) vs fixed-width.
          2. Read via ``pd.read_csv(sep=...)`` or ``pd.read_fwf(...)``.
          3. Wrap in a single ``CanonicalSheet`` named "Sheet1".
          4. Reuse ``ingestion.type_detection`` for type/format/stats.
        """
        # TODO(post-poc): implement delimiter/fixed-width detection + parsing.
        raise ParserNotImplementedError(FileFormat.TXT)
