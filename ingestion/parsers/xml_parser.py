# ============================================================================
# Deliverable 1 - XML parser (SKELETON - implementation pending after POC).
# Conforms to BaseFileParser so it can be registered and swapped in later.
# Intended approach: parse with a safe XML library (defusedxml/lxml), flatten
# repeating record elements into tabular rows, treat each distinct record type
# (or root child collection) as a sheet, then reuse type/format detection.
# Requires adding a dependency (e.g. lxml / xmltodict) to requirements.txt.
# ============================================================================
"""
ingestion.parsers.xml_parser
============================
Skeleton XML parser. See module banner for the intended implementation plan.
"""
from __future__ import annotations

from pathlib import Path

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile
from ingestion.parsers.base import BaseFileParser, ParserNotImplementedError


class XmlParser(BaseFileParser):
    """Skeleton parser for .xml files."""

    supported_formats = (FileFormat.XML,)

    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse an XML file into a :class:`CanonicalFile`.

        Implementation pending (post-POC). Planned steps:
          1. Safely parse the document (defusedxml to avoid XXE).
          2. Identify repeating record elements -> rows; map each record
             collection to a ``CanonicalSheet``.
          3. Flatten element/attribute leaves into columns (preserving order).
          4. Reuse ``ingestion.type_detection`` for type/format/stats.
        """
        # TODO(post-poc): add lxml/xmltodict + defusedxml to requirements and
        #                 implement per the plan above.
        raise ParserNotImplementedError(FileFormat.XML)
