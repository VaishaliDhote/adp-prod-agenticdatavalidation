# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation service. REUSABLE.
# Thin orchestration over the Deliverable 3 analyzer + the Deliverable 4
# builder/compiler/validator. Given a canonical model (or a session to ingest),
# it produces the Validation IR and/or the exact validation-rules JSON plus a
# deterministic validation report. Consumed by the /validation-ir HTTP routes
# and by tests.
# ============================================================================
"""
services.validation_ir_service
===============================
Orchestrates Sprint 4 validation IR construction and validation-JSON generation.

Two-stage, framework-agnostic:

* :meth:`build_ir` - canonical model -> Deliverable 3 analysis -> Validation IR.
* :meth:`generate` - build the IR, compile it to exact ``validation-rules`` JSON,
  and validate the generated block (tasks 16-19).

Session variants ingest a session's input files via the Deliverable 1
:class:`~ingestion.ingestion_service.IngestionService` first. Failures are
normalized to :class:`ValidationIRError` so the API layer can map them to the
unified error contract.
"""
from __future__ import annotations

import logging
from typing import Optional

from canonical.models import CanonicalAnalysisModel
from validation_analysis.analyzer import ValidationAnalyzer
from validation_analysis.models import ValidationAnalysisResult
from validation_ir.builder import ValidationIRBuilder
from validation_ir.compiler import ValidationRuleCompiler
from validation_ir.models import ValidationGenerationContext, ValidationIR
from validation_ir.validation import (
    ValidationBlockReport,
    validate_validation_block,
)
from services.validation_analysis_service import (
    ValidationAnalysisError,
    ValidationAnalysisService,
)

logger = logging.getLogger(__name__)


class ValidationIRError(RuntimeError):
    """Raised when validation IR construction or JSON generation fails."""


class GenerationResult:
    """Bundle of everything :meth:`ValidationIRService.generate` produces.

    Attributes:
        analysis: The Deliverable 3 analysis result.
        ir: The Validation IR (tasks 1-9).
        rows: The generated ``validation-rules`` JSON array (tasks 10-15).
        report: The block validation report (tasks 16-19).
    """

    __slots__ = ("analysis", "ir", "rows", "report")

    def __init__(
        self,
        analysis: ValidationAnalysisResult,
        ir: ValidationIR,
        rows: list[dict],
        report: ValidationBlockReport,
    ) -> None:
        self.analysis = analysis
        self.ir = ir
        self.rows = rows
        self.report = report


class ValidationIRService:
    """Orchestrate IR construction + exact validation-JSON generation."""

    def __init__(
        self,
        analysis_service: Optional[ValidationAnalysisService] = None,
        builder: Optional[ValidationIRBuilder] = None,
        compiler: Optional[ValidationRuleCompiler] = None,
    ) -> None:
        self._analysis = analysis_service or ValidationAnalysisService()
        self._builder = builder or ValidationIRBuilder()
        self._compiler = compiler or ValidationRuleCompiler()

    # -- IR construction ----------------------------------------------------
    def build_ir(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[ValidationGenerationContext] = None,
    ) -> ValidationIR:
        """Build the Validation IR from a prebuilt canonical model (tasks 1-9)."""
        analysis = self._analyze(model)
        return self._build(analysis, context)

    def build_ir_from_session(
        self,
        session_id: str,
        context: Optional[ValidationGenerationContext] = None,
    ) -> ValidationIR:
        """Ingest a session, analyze it, then build the Validation IR."""
        analysis = self._analysis.analyze_session(session_id)
        return self._build(analysis, context)

    # -- full generation ----------------------------------------------------
    def generate(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[ValidationGenerationContext] = None,
        *,
        require_identity: bool = False,
    ) -> GenerationResult:
        """Build IR, compile to JSON, and validate the block (tasks 1-19).

        Args:
            model: The canonical analysis model to generate rules for.
            context: Identity/audit context stamped onto every row. When
                ``require_identity`` is True this MUST carry the real injected
                identity triple (not the standalone placeholders).
            require_identity: Phase 0 guard. When True, generation fails fast
                unless ``context`` has a real ``bu``/``platform``/``dataCategory``
                injected, so a truth-file config is never mis-stamped with the
                placeholder identity.
        """
        self._check_identity(context, require_identity)
        analysis = self._analyze(model)
        ir = self._build(analysis, context)
        return self._compile_and_validate(analysis, ir)

    def generate_from_session(
        self,
        session_id: str,
        context: Optional[ValidationGenerationContext] = None,
        *,
        require_identity: bool = False,
    ) -> GenerationResult:
        """Ingest a session then run the full generate pipeline."""
        self._check_identity(context, require_identity)
        analysis = self._analysis.analyze_session(session_id)
        ir = self._build(analysis, context)
        return self._compile_and_validate(analysis, ir)

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _check_identity(
        context: Optional[ValidationGenerationContext], require_identity: bool
    ) -> None:
        """Enforce real identity-triple injection when requested (Phase 0).

        Raises:
            ValidationIRError: when ``require_identity`` is True and the context
                is missing or still carries the placeholder identity triple.
        """
        if not require_identity:
            return
        if context is None:
            raise ValidationIRError(
                "require_identity=True but no ValidationGenerationContext was "
                "provided; inject the real bu/platform/dataCategory triple."
            )
        try:
            context.require_identity()
        except ValueError as e:
            raise ValidationIRError(str(e)) from e

    def _analyze(self, model: CanonicalAnalysisModel) -> ValidationAnalysisResult:
        try:
            return self._analysis.analyze_model(model)
        except ValidationAnalysisError as e:
            raise ValidationIRError(str(e)) from e

    def _build(
        self,
        analysis: ValidationAnalysisResult,
        context: Optional[ValidationGenerationContext],
    ) -> ValidationIR:
        try:
            return self._builder.build(analysis, context)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[validation-ir] IR build failed")
            raise ValidationIRError(str(e)) from e

    def _compile_and_validate(
        self, analysis: ValidationAnalysisResult, ir: ValidationIR
    ) -> GenerationResult:
        try:
            rows = self._compiler.compile(ir)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[validation-ir] Compilation failed")
            raise ValidationIRError(str(e)) from e

        known = self._known_column_uuids(analysis)
        report = validate_validation_block(rows, known_column_uuids=known)

        logger.info(
            "[validation-ir] generated rows=%d valid=%s rejected=%d issues=%d",
            len(rows),
            report.is_valid,
            ir.rejected_count(),
            len(report.issues),
        )
        return GenerationResult(analysis=analysis, ir=ir, rows=rows, report=report)

    @staticmethod
    def _known_column_uuids(analysis: ValidationAnalysisResult) -> set[str]:
        """Collect every valid source-column UUID for reference checking (task 19)."""
        uuids: set[str] = set()
        for structure in analysis.expected_structures:
            for col in structure.expected_columns:
                uuids.add(col.source_column_uuid)
        for profile in analysis.inventory.columns:
            uuids.add(profile.source_column_uuid)
        return uuids


__all__ = ["ValidationIRService", "ValidationIRError", "GenerationResult"]
