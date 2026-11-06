# ============================================================================
# Deliverable 1 - Canonical model builder (task 22). REUSABLE.
# Assembles parsed CanonicalFiles + detected relationships into the unified
# CanonicalAnalysisModel. Keeps assembly logic separate from ingestion I/O so
# it can be unit-tested with in-memory canonical files.
# ============================================================================
"""
ingestion.canonical_builder
===========================
Assemble the unified :class:`CanonicalAnalysisModel` (Sprint 1, task 22).
"""
from __future__ import annotations

import logging
from typing import Optional

from canonical.models import CanonicalAnalysisModel, CanonicalFile
from ingestion.relationship_detector import RelationshipDetector

logger = logging.getLogger(__name__)


class CanonicalBuilder:
    """Build the top-level canonical analysis model from parsed files."""

    def __init__(self, relationship_detector: Optional[RelationshipDetector] = None) -> None:
        self._relationship_detector = relationship_detector or RelationshipDetector()

    def build(
        self,
        input_files: list[CanonicalFile],
        output_files: list[CanonicalFile],
        session_id: Optional[str] = None,
    ) -> CanonicalAnalysisModel:
        """Combine parsed files + detected relationships into one model.

        Args:
            input_files: Parsed canonical input files.
            output_files: Parsed canonical output files.
            session_id: Optional owning session identifier.

        Returns:
            A populated :class:`CanonicalAnalysisModel`.
        """
        relationships = self._relationship_detector.detect(input_files, output_files)
        model = CanonicalAnalysisModel(
            session_id=session_id,
            input_files=input_files,
            output_files=output_files,
            relationships=relationships,
        )
        logger.info(
            "Built canonical model (session=%s): %d input file(s), %d output file(s), "
            "%d relationship(s)",
            session_id,
            len(input_files),
            len(output_files),
            len(relationships),
        )
        return model
