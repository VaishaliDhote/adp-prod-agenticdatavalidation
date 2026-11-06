# ============================================================================
# Deliverable 1 - File ingestion parser layer. Errors + abstract base.
# REUSABLE. Defines the parser contract every format-specific parser conforms
# to, plus the dedicated exception types raised by ingestion.
# ============================================================================
"""
ingestion.parsers.base
======================
Abstract parser contract for format-specific file parsers.

Every parser converts a single file on disk into a :class:`CanonicalFile`
(sheets -> columns -> stats), independent of the file format. The Excel parser
implements this fully; the CSV/XML/TXT/PDF parsers ship as skeletons that raise
:class:`ParserNotImplementedError` until implemented after the POC.
"""
from __future__ import annotations

import abc
from pathlib import Path

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalFile


class IngestionError(Exception):
    """Base class for all ingestion-layer errors."""


class FileParseError(IngestionError):
    """Raised when a file cannot be read or parsed into the canonical model."""


class ParserNotImplementedError(IngestionError):
    """Raised by skeleton parsers whose format support is pending post-POC.

    Carries the target format so callers can surface a precise message and the
    orchestrator can map it to a stable error code.
    """

    def __init__(self, file_format: FileFormat, message: str | None = None) -> None:
        self.file_format = file_format
        super().__init__(
            message
            or f"Parser for '{file_format.value}' is not implemented yet "
            f"(pending post-POC implementation)."
        )


class BaseFileParser(abc.ABC):
    """Abstract base class for all format-specific file parsers.

    Subclasses declare the formats they handle via :attr:`supported_formats`
    and implement :meth:`parse` to produce a fully populated
    :class:`CanonicalFile`, including ordered sheets, ordered columns with
    positions, per-column statistics, and detected data types/formats.
    """

    #: Formats this parser is able to handle.
    supported_formats: tuple[FileFormat, ...] = ()

    @abc.abstractmethod
    def parse(self, path: Path, io_role: IoRole) -> CanonicalFile:
        """Parse ``path`` into a :class:`CanonicalFile`.

        Args:
            path: Path to the file on disk.
            io_role: Whether the file is a known input or expected output.

        Returns:
            A fully populated canonical file model.

        Raises:
            FileParseError: If the file cannot be read/parsed.
            ParserNotImplementedError: If the format is not yet implemented.
        """
        raise NotImplementedError

    # -- Shared helpers -----------------------------------------------------
    @staticmethod
    def _file_size(path: Path) -> int | None:
        """Return the file size in bytes, or ``None`` if unavailable (task 5)."""
        try:
            return path.stat().st_size
        except OSError:
            return None
