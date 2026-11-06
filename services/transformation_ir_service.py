# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation service.
# REUSABLE. Thin orchestration over the Deliverable 5 analyzer + the Deliverable
# 6 builder/compiler/validator. Given a canonical model (or a session to ingest),
# it produces the Transformation IR and/or the exact ``transformation-rules``
# (20-key) + ``dataset-transformation-rules`` (11-key) JSON plus a deterministic
# block report. Consumed by the /transformation-ir HTTP routes and by tests.
# ============================================================================
"""
services.transformation_ir_service
==================================
Orchestrates Sprint 6 transformation IR construction and transformation-JSON
generation.

Two-stage, framework-agnostic (mirrors the Deliverable 4
:class:`~services.validation_ir_service.ValidationIRService`):

* :meth:`build_ir` - canonical model -> Deliverable 5 analysis -> Transformation IR.
* :meth:`generate` - build the IR, compile it to exact ``transformation-rules``
  and ``dataset-transformation-rules`` JSON, and validate the generated block
  (tasks 20-22).

Session variants ingest a session's input + expected-output files via the
Deliverable 1 :class:`~ingestion.ingestion_service.IngestionService` first (through
the Deliverable 5 analysis service). Failures are normalized to
:class:`TransformationIRError` so the API layer can map them to the unified error
contract.
"""
from __future__ import annotations

import logging
from typing import Optional

from canonical.models import CanonicalAnalysisModel
from services.transformation_analysis_service import (
    TransformationAnalysisError,
    TransformationAnalysisService,
)
from transformation_analysis.models import TransformationAnalysisResult
from transformation_ir.builder import TransformationIRBuilder
from transformation_ir.compiler import TransformationRuleCompiler
from transformation_ir.models import TransformationGenerationContext, TransformationIR
from transformation_ir.validation import (
    TransformationBlockReport,
    validate_transformation_block,
)

logger = logging.getLogger(__name__)


class TransformationIRError(RuntimeError):
    """Raised when transformation IR construction or JSON generation fails."""


class GenerationResult:
    """Bundle of everything :meth:`TransformationIRService.generate` produces.

    Attributes:
        analysis: The Deliverable 5 transformation analysis result.
        ir: The Transformation IR (tasks 1-10).
        rows: The generated ``transformation-rules`` JSON array (tasks 11-19).
        dataset_rows: The generated ``dataset-transformation-rules`` JSON array.
        report: The block validation report (tasks 20-22).
    """

    __slots__ = ("analysis", "ir", "rows", "dataset_rows", "report")

    def __init__(
        self,
        analysis: TransformationAnalysisResult,
        ir: TransformationIR,
        rows: list[dict],
        dataset_rows: list[dict],
        report: TransformationBlockReport,
    ) -> None:
        self.analysis = analysis
        self.ir = ir
        self.rows = rows
        self.dataset_rows = dataset_rows
        self.report = report


class TransformationIRService:
    """Orchestrate IR construction + exact transformation-JSON generation."""

    def __init__(
        self,
        analysis_service: Optional[TransformationAnalysisService] = None,
        builder: Optional[TransformationIRBuilder] = None,
        compiler: Optional[TransformationRuleCompiler] = None,
    ) -> None:
        self._analysis = analysis_service or TransformationAnalysisService()
        self._builder = builder or TransformationIRBuilder()
        self._compiler = compiler or TransformationRuleCompiler()

    # -- IR construction ----------------------------------------------------
    def build_ir(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[TransformationGenerationContext] = None,
    ) -> TransformationIR:
        """Build the Transformation IR from a prebuilt canonical model (tasks 1-10)."""
        analysis = self._analyze(model)
        return self._build(analysis, context)

    def build_ir_from_session(
        self,
        session_id: str,
        context: Optional[TransformationGenerationContext] = None,
    ) -> TransformationIR:
        """Ingest a session, analyze it, then build the Transformation IR."""
        analysis = self._analyze_session(session_id)
        return self._build(analysis, context)

    # -- full generation ----------------------------------------------------
    def generate(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[TransformationGenerationContext] = None,
    ) -> GenerationResult:
        """Build IR, compile to JSON, and validate the block (tasks 1-22)."""
        analysis = self._analyze(model)
        ir = self._build(analysis, context)
        return self._compile_and_validate(analysis, ir)

    def generate_from_session(
        self,
        session_id: str,
        context: Optional[TransformationGenerationContext] = None,
    ) -> GenerationResult:
        """Ingest a session then run the full generate pipeline."""
        analysis = self._analyze_session(session_id)
        ir = self._build(analysis, context)
        return self._compile_and_validate(analysis, ir)

    # -- internals ----------------------------------------------------------
    def _analyze(
        self, model: CanonicalAnalysisModel
    ) -> TransformationAnalysisResult:
        try:
            return self._analysis.analyze_model(model)
        except TransformationAnalysisError as e:
            raise TransformationIRError(str(e)) from e

    def _analyze_session(self, session_id: str) -> TransformationAnalysisResult:
        # Ingestion errors propagate unchanged so the route can map them to
        # their specific codes; analysis failures are normalized.
        try:
            return self._analysis.analyze_session(session_id)
        except TransformationAnalysisError as e:
            raise TransformationIRError(str(e)) from e

    def _build(
        self,
        analysis: TransformationAnalysisResult,
        context: Optional[TransformationGenerationContext],
    ) -> TransformationIR:
        try:
            return self._builder.build(analysis, context)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[transformation-ir] IR build failed")
            raise TransformationIRError(str(e)) from e

    def _compile_and_validate(
        self, analysis: TransformationAnalysisResult, ir: TransformationIR
    ) -> GenerationResult:
        try:
            rows = self._compiler.compile(ir)
            dataset_rows = self._compiler.compile_datasets(ir)
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[transformation-ir] Compilation failed")
            raise TransformationIRError(str(e)) from e

        known = self._known_column_uuids(analysis)
        report = validate_transformation_block(
            rows, dataset_rows, known_column_uuids=known
        )

        logger.info(
            "[transformation-ir] generated rows=%d dataset_rows=%d valid=%s "
            "rejected=%d issues=%d",
            len(rows),
            len(dataset_rows),
            report.is_valid,
            ir.rejected_count(),
            len(report.issues),
        )
        return GenerationResult(
            analysis=analysis,
            ir=ir,
            rows=rows,
            dataset_rows=dataset_rows,
            report=report,
        )

    @staticmethod
    def _known_column_uuids(analysis: TransformationAnalysisResult) -> set[str]:
        """Collect every valid column UUID for reference checking (UUID checks).

        Includes source and target column UUIDs from every inventory profile
        plus dataset-rule field/target UUIDs so ``sourceColumnName`` /
        ``dependsOn`` / ``aggrigation.field`` references all resolve.
        """
        uuids: set[str] = set()
        for profile in analysis.inventory.columns:
            if profile.target_column.column_uuid:
                uuids.add(profile.target_column.column_uuid)
            for src in profile.source_columns:
                if src.column_uuid:
                    uuids.add(src.column_uuid)
        for req in analysis.inventory.dataset_rules:
            if req.field_uuid:
                uuids.add(req.field_uuid)
            if req.dataset_uuid:
                uuids.add(req.dataset_uuid)
            field = req.parameters.get("field")
            if isinstance(field, str) and field:
                uuids.add(field)
        return uuids


__all__ = [
    "TransformationIRService",
    "TransformationIRError",
    "GenerationResult",
]
