# ============================================================================
# Deliverable 1 - Parser registry. REUSABLE.
# Central mapping from FileFormat -> parser instance. This is the single place
# to enable a new format once its skeleton parser is implemented after the POC.
# ============================================================================
"""
ingestion.parsers.registry
==========================
Resolve the correct :class:`BaseFileParser` for a given file format or path.
"""
from __future__ import annotations

from pathlib import Path

from canonical.enums import FileFormat
from ingestion.parsers.base import BaseFileParser
from ingestion.parsers.csv_parser import CsvParser
from ingestion.parsers.excel_parser import ExcelParser
from ingestion.parsers.json_parser import JsonParser
from ingestion.parsers.pdf_parser import PdfParser
from ingestion.parsers.text_parser import TextParser
from ingestion.parsers.xml_parser import XmlParser

# Instantiate once; parsers are stateless and safe to share.
_EXCEL = ExcelParser()
_CSV = CsvParser()
_JSON = JsonParser()
_XML = XmlParser()
_TEXT = TextParser()
_PDF = PdfParser()

#: Format -> parser instance. Excel, CSV and JSON are fully implemented; the
#: rest are skeletons that raise ParserNotImplementedError until completed.
_REGISTRY: dict[FileFormat, BaseFileParser] = {
    FileFormat.XLSX: _EXCEL,
    FileFormat.XLS: _EXCEL,
    FileFormat.CSV: _CSV,
    FileFormat.JSON: _JSON,
    FileFormat.XML: _XML,
    FileFormat.TXT: _TEXT,
    FileFormat.PDF: _PDF,
}


def resolve_parser(file_format: FileFormat) -> BaseFileParser:
    """Return the parser registered for ``file_format``.

    Raises:
        ValueError: If no parser is registered for the format.
    """
    parser = _REGISTRY.get(file_format)
    if parser is None:
        raise ValueError(f"No parser registered for format '{file_format.value}'")
    return parser


def resolve_parser_for_path(path: Path) -> BaseFileParser:
    """Resolve a parser from a file path's extension.

    Raises:
        ValueError: If the extension is unsupported or unregistered.
    """
    file_format = FileFormat.from_extension(path.suffix)
    return resolve_parser(file_format)


def supported_formats() -> list[str]:
    """Return the list of registered format tokens (for diagnostics)."""
    return [fmt.value for fmt in _REGISTRY]
