# ============================================================================
# Deliverable 1 - Ingestion orchestrator service (tasks 1, 3, 4). REUSABLE.
# Ingests MULTIPLE input and output files, each with MULTIPLE sheets, in a
# single workflow; resolves the correct parser per file; builds the unified
# canonical model with deterministic source-to-target relationships.
# Consumed via core.dependencies.get_ingestion_service() (no HTTP route yet).
# ============================================================================
"""
ingestion.ingestion_service
===========================
End-to-end Sprint 1 orchestration: input/output files -> canonical model.

The service is deliberately I/O-light and framework-agnostic: it takes explicit
file paths (or a session directory layout under ``AppPaths``) and returns a
:class:`CanonicalAnalysisModel`. It uses structured ``logging`` (never
``print``) and raises ``IngestionError`` subclasses on failure, which the API
layer can map to the unified error contract.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from canonical.enums import FileFormat, IoRole
from canonical.models import CanonicalAnalysisModel, CanonicalFile
from core.paths import AppPaths
from ingestion.canonical_builder import CanonicalBuilder
from ingestion.parsers.base import IngestionError, ParserNotImplementedError
from ingestion.parsers.registry import resolve_parser_for_path

logger = logging.getLogger(__name__)


class IngestionService:
    """Orchestrate multi-file, multi-sheet ingestion into the canonical model."""

    def __init__(
        self,
        input_base_dir: Path = AppPaths.INPUT_BASE_DIR,
        output_base_dir: Path = AppPaths.OUTPUT_BASE_DIR,
        builder: Optional[CanonicalBuilder] = None,
    ) -> None:
        self._input_base_dir = input_base_dir
        self._output_base_dir = output_base_dir
        self._builder = builder or CanonicalBuilder()

    # -- primary entry points ----------------------------------------------
    def ingest_files(
        self,
        input_paths: Iterable[Path],
        output_paths: Iterable[Path],
        session_id: Optional[str] = None,
    ) -> CanonicalAnalysisModel:
        """Ingest explicit input/output file paths into the canonical model.

        Args:
            input_paths: Paths to known input files.
            output_paths: Paths to expected output files.
            session_id: Optional owning session identifier.

        Returns:
            The unified :class:`CanonicalAnalysisModel`.

        Raises:
            IngestionError: If a file cannot be parsed.
        """
        input_files = self._parse_all(input_paths, IoRole.INPUT)
        output_files = self._parse_all(output_paths, IoRole.OUTPUT)
        return self._builder.build(input_files, output_files, session_id=session_id)

    def ingest_session(self, session_id: str) -> CanonicalAnalysisModel:
        """Ingest all files for a session from the standard directory layout.

        Scans ``<input_base>/<session_id>`` and ``<output_base>/<session_id>``
        for supported files. Unsupported/skeleton formats are skipped with a
        warning so the Excel POC path is not blocked.
        """
        input_dir = self._input_base_dir / session_id
        output_dir = self._output_base_dir / session_id
        input_paths = self._discover_supported(input_dir)
        output_paths = self._discover_supported(output_dir)
        logger.info(
            "Session '%s': discovered %d input + %d output file(s)",
            session_id,
            len(input_paths),
            len(output_paths),
        )
        return self.ingest_files(input_paths, output_paths, session_id=session_id)

    # -- internals ----------------------------------------------------------
    def _parse_all(self, paths: Iterable[Path], io_role: IoRole) -> list[CanonicalFile]:
        """Parse each path with its resolved parser into a CanonicalFile."""
        parsed: list[CanonicalFile] = []
        for path in paths:
            path = Path(path)
            if not path.exists():
                raise IngestionError(f"File not found: {path}")
            try:
                parser = resolve_parser_for_path(path)
                parsed.append(parser.parse(path, io_role))
            except ParserNotImplementedError:
                # Skeleton format encountered explicitly; re-raise so callers
                # ingesting a specific unsupported file get a clear signal.
                logger.warning("Skipping unsupported-format file (pending): %s", path.name)
                raise
        return parsed

    def _discover_supported(self, directory: Path) -> list[Path]:
        """Return supported files in ``directory`` sorted for deterministic order.

        Only formats with a working parser (Excel for the POC) are returned;
        skeleton formats are logged and skipped so session ingestion proceeds.
        """
        if not directory.exists():
            return []
        implemented = {
            FileFormat.XLSX,
            FileFormat.XLS,
            FileFormat.CSV,
            FileFormat.JSON,
        }
        supported: list[Path] = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            try:
                fmt = FileFormat.from_extension(path.suffix)
            except ValueError:
                logger.warning("Skipping unrecognized file type: %s", path.name)
                continue
            if fmt in implemented:
                supported.append(path)
            else:
                logger.warning(
                    "Skipping '%s': parser for '%s' pending post-POC.", path.name, fmt.value
                )
        return supported
