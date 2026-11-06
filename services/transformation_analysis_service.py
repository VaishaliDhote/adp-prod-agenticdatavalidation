# ============================================================================
# Deliverable 5 - Transformation Rule Identification service (task 1). REUSABLE.
# Thin orchestration layer over transformation_analysis.TransformationAnalyzer
# that (a) analyzes an already-built canonical model, or (b) ingests a session's
# input + output files via the Deliverable 1 IngestionService and then analyzes
# the differences. Consumed by the /transformation-analysis HTTP routes + tests.
# ============================================================================
"""
services.transformation_analysis_service
========================================
Orchestrates Sprint 5 transformation rule identification.

The service is framework-agnostic and I/O-light: it either accepts a prebuilt
:class:`~canonical.models.CanonicalAnalysisModel` (which must contain both the
input and the expected-output files plus their relationships) or delegates
ingestion to the Deliverable 1
:class:`~ingestion.ingestion_service.IngestionService`, then runs
:class:`~transformation_analysis.analyzer.TransformationAnalyzer`. It uses
structured logging and raises :class:`TransformationAnalysisError` on failure so
the API layer can map it to the unified error contract.
"""
from __future__ import annotations

import logging
from typing import Optional

from canonical.models import CanonicalAnalysisModel
from ingestion.ingestion_service import IngestionService
from transformation_analysis.analyzer import TransformationAnalyzer
from transformation_analysis.models import TransformationAnalysisResult

logger = logging.getLogger(__name__)


class TransformationAnalysisError(RuntimeError):
    """Raised when transformation rule identification cannot be completed."""


class TransformationAnalysisService:
    """Orchestrate transformation rule identification over the canonical model."""

    def __init__(
        self,
        ingestion_service: Optional[IngestionService] = None,
        analyzer: Optional[TransformationAnalyzer] = None,
    ) -> None:
        self._ingestion = ingestion_service or IngestionService()
        self._analyzer = analyzer or TransformationAnalyzer()

    # -- primary entry points ----------------------------------------------
    def analyze_model(
        self, model: CanonicalAnalysisModel
    ) -> TransformationAnalysisResult:
        """Run transformation rule identification over a prebuilt canonical model.

        Args:
            model: A Deliverable 1 canonical analysis model containing input +
                expected-output files and their source-to-target relationships.

        Returns:
            The :class:`TransformationAnalysisResult`.

        Raises:
            TransformationAnalysisError: If analysis fails unexpectedly.
        """
        try:
            result = self._analyzer.analyze(model)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[transformation-analysis] Analysis failed")
            raise TransformationAnalysisError(str(e)) from e

        logger.info(
            "[transformation-analysis] targets=%d requirements=%d supported=%d unsupported=%d",
            result.summary.target_column_count,
            result.summary.requirement_count,
            result.summary.supported_count,
            result.summary.unsupported_count,
        )
        return result

    def analyze_session(self, session_id: str) -> TransformationAnalysisResult:
        """Ingest a session's input + output files, then run identification.

        Args:
            session_id: Session whose input files live under
                ``<input_base>/<session_id>`` and expected-output files under
                ``<output_base>/<session_id>``.

        Returns:
            The :class:`TransformationAnalysisResult`.

        Raises:
            TransformationAnalysisError: If ingestion or analysis fails.
                Ingestion errors from the Deliverable 1 layer propagate
                unchanged so the route can map them to their specific codes.
        """
        model = self._ingestion.ingest_session(session_id)
        return self.analyze_model(model)


__all__ = ["TransformationAnalysisService", "TransformationAnalysisError"]
