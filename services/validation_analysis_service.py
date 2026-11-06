# ============================================================================
# Deliverable 3 - Validation Rule Identification service (task 1). REUSABLE.
# Thin orchestration layer over validation_analysis.ValidationAnalyzer that
# (a) analyzes an already-built canonical model, or (b) ingests a session's
# input files via the Deliverable 1 IngestionService and then analyzes them.
# Consumed by the /validation-analysis HTTP routes and by tests.
# ============================================================================
"""
services.validation_analysis_service
====================================
Orchestrates Sprint 3 validation rule identification.

The service is framework-agnostic and I/O-light: it either accepts a prebuilt
:class:`~canonical.models.CanonicalAnalysisModel` or delegates ingestion to the
Deliverable 1 :class:`~ingestion.ingestion_service.IngestionService`, then runs
:class:`~validation_analysis.analyzer.ValidationAnalyzer`. It uses structured
logging and raises :class:`ValidationAnalysisError` on failure so the API layer
can map it to the unified error contract.
"""
from __future__ import annotations

import logging
from typing import Optional

from canonical.models import CanonicalAnalysisModel
from ingestion.ingestion_service import IngestionService
from validation_analysis.analyzer import ValidationAnalyzer
from validation_analysis.models import ValidationAnalysisResult

logger = logging.getLogger(__name__)


class ValidationAnalysisError(RuntimeError):
    """Raised when validation rule identification cannot be completed."""


class ValidationAnalysisService:
    """Orchestrate validation rule identification over the canonical model."""

    def __init__(
        self,
        ingestion_service: Optional[IngestionService] = None,
        analyzer: Optional[ValidationAnalyzer] = None,
    ) -> None:
        self._ingestion = ingestion_service or IngestionService()
        self._analyzer = analyzer or ValidationAnalyzer()

    # -- primary entry points ----------------------------------------------
    def analyze_model(
        self, model: CanonicalAnalysisModel
    ) -> ValidationAnalysisResult:
        """Run validation rule identification over a prebuilt canonical model.

        Args:
            model: A Deliverable 1 canonical analysis model.

        Returns:
            The :class:`ValidationAnalysisResult`.

        Raises:
            ValidationAnalysisError: If analysis fails unexpectedly.
        """
        try:
            result = self._analyzer.analyze(model)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[validation-analysis] Analysis failed")
            raise ValidationAnalysisError(str(e)) from e

        logger.info(
            "[validation-analysis] columns=%d requirements=%d supported=%d unsupported=%d",
            result.summary.column_count,
            result.summary.requirement_count,
            result.summary.supported_count,
            result.summary.unsupported_count,
        )
        return result

    def analyze_session(self, session_id: str) -> ValidationAnalysisResult:
        """Ingest a session's input files, then run validation identification.

        Args:
            session_id: Session whose input files live under the standard
                ``<input_base>/<session_id>`` layout.

        Returns:
            The :class:`ValidationAnalysisResult`.

        Raises:
            ValidationAnalysisError: If ingestion or analysis fails. Ingestion
                errors from the Deliverable 1 layer propagate unchanged so the
                route can map them to their specific error codes.
        """
        model = self._ingestion.ingest_session(session_id)
        return self.analyze_model(model)


__all__ = ["ValidationAnalysisService", "ValidationAnalysisError"]
