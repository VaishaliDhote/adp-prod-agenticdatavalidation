# ============================================================================
# Deliverable 1 - Parser subpackage exports.
# ============================================================================
"""
ingestion.parsers
=================
Format-specific file parsers and the registry that resolves them.
"""
from __future__ import annotations

from ingestion.parsers.base import (
    BaseFileParser,
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from ingestion.parsers.registry import (
    resolve_parser,
    resolve_parser_for_path,
    supported_formats,
)

__all__ = [
    "BaseFileParser",
    "FileParseError",
    "IngestionError",
    "ParserNotImplementedError",
    "resolve_parser",
    "resolve_parser_for_path",
    "supported_formats",
]
