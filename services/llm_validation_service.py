# ============================================================================
# Deliverable 3/4 - LLM-driven validation generation service.
#
# Orchestrates the LLM validation DETECTOR (replaces the deterministic
# Deliverable 3 analyzer) + the existing Deliverable 4 IR builder / compiler /
# validator. Given a session (or a prebuilt canonical model) it:
#   1. ingests the input files into the canonical model,
#   2. asks the LLM which validations each column needs (prints the raw reply),
#   3. builds the Validation IR and compiles the exact ``validation-rules`` JSON,
#   4. validates the generated block.
#
# The transformation deliverables are intentionally NOT run here - this service
# fixes ONLY the validations cycle (per the current scope). An offline path
# (``reply=...``) lets the parse -> IR -> JSON wiring be exercised without the
# LLM/network.
# ============================================================================
"""
services.llm_validation_service
===============================
LLM-driven ``validation-rules`` generation (validations only).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from canonical.models import CanonicalAnalysisModel
from ingestion.ingestion_service import IngestionService
from validation_analysis.llm_detector import LLMValidationDetector
from validation_analysis.models import ValidationAnalysisResult
from validation_ir.builder import ValidationIRBuilder
from validation_ir.compiler import ValidationRuleCompiler
from validation_ir.models import ValidationGenerationContext, ValidationIR
from validation_ir.validation import (
    ValidationBlockReport,
    validate_validation_block,
)

logger = logging.getLogger(__name__)


class LLMValidationError(RuntimeError):
    """Raised when LLM-driven validation generation fails."""


class LLMValidationResult:
    """Everything the LLM validations flow produces.

    Attributes:
        raw_reply: The raw LLM reply text (already printed by the detector).
        analysis: The adapted :class:`ValidationAnalysisResult`.
        ir: The compiled :class:`ValidationIR`.
        rows: The exact ``validation-rules`` JSON array.
        report: The block validation report.
    """

    __slots__ = ("raw_reply", "analysis", "ir", "rows", "report")

    def __init__(
        self,
        raw_reply: str,
        analysis: ValidationAnalysisResult,
        ir: ValidationIR,
        rows: list[dict[str, Any]],
        report: ValidationBlockReport,
    ) -> None:
        self.raw_reply = raw_reply
        self.analysis = analysis
        self.ir = ir
        self.rows = rows
        self.report = report


class LLMValidationService:
    """Generate ``validation-rules`` JSON from LLM detection (validations only)."""

    def __init__(
        self,
        ingestion_service: Optional[IngestionService] = None,
        detector: Optional[LLMValidationDetector] = None,
        builder: Optional[ValidationIRBuilder] = None,
        compiler: Optional[ValidationRuleCompiler] = None,
    ) -> None:
        self._ingestion = ingestion_service or IngestionService()
        self._detector = detector or LLMValidationDetector()
        self._builder = builder or ValidationIRBuilder()
        self._compiler = compiler or ValidationRuleCompiler()

    # -- full async generation from a session -------------------------------
    async def generate_from_session(
        self,
        session_id: str,
        *,
        context: Optional[ValidationGenerationContext] = None,
        max_tokens: int = 16000,
        timeout: int = 600,
        print_reply: bool = True,
    ) -> LLMValidationResult:
        """Ingest a session, run LLM detection, and compile validation-rules JSON."""
        model = self._ingestion.ingest_session(session_id)
        model.session_id = model.session_id or session_id
        return await self.generate(
            model,
            context=context,
            max_tokens=max_tokens,
            timeout=timeout,
            print_reply=print_reply,
        )

    # -- full async generation from a prebuilt model ------------------------
    async def generate(
        self,
        model: CanonicalAnalysisModel,
        *,
        context: Optional[ValidationGenerationContext] = None,
        max_tokens: int = 16000,
        timeout: int = 600,
        print_reply: bool = True,
    ) -> LLMValidationResult:
        """Run LLM detection over a canonical model and compile validation-rules JSON."""
        ctx = context or ValidationGenerationContext()
        try:
            reply, analysis = await self._detector.detect(
                model,
                bu=ctx.bu,
                platform=ctx.platform,
                data_category=ctx.data_category,
                session_id=model.session_id or "",
                max_tokens=max_tokens,
                timeout=timeout,
                print_reply=print_reply,
            )
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[llm-validation] detection failed")
            raise LLMValidationError(str(e)) from e

        return self._compile(reply, analysis, ctx, model)

    # -- offline path: compile from an already-captured LLM reply -----------
    def generate_from_reply(
        self,
        reply: str,
        model: CanonicalAnalysisModel,
        *,
        context: Optional[ValidationGenerationContext] = None,
    ) -> LLMValidationResult:
        """Compile validation-rules JSON from a pre-captured LLM reply (no network).

        Useful for testing the parse -> IR -> JSON wiring, or replaying a reply
        captured in an LLM-capable environment.
        """
        ctx = context or ValidationGenerationContext()
        try:
            analysis = self._detector.build_result_from_reply(reply, model)
        except Exception as e:  # noqa: BLE001
            logger.exception("[llm-validation] reply parse failed")
            raise LLMValidationError(str(e)) from e
        return self._compile(reply, analysis, ctx, model)

    # -- internals ----------------------------------------------------------
    def _compile(
        self,
        reply: str,
        analysis: ValidationAnalysisResult,
        ctx: ValidationGenerationContext,
        model: CanonicalAnalysisModel,
    ) -> LLMValidationResult:
        try:
            ir = self._builder.build(analysis, ctx)
            rows = self._compiler.compile(ir)
        except Exception as e:  # noqa: BLE001
            logger.exception("[llm-validation] IR build/compile failed")
            raise LLMValidationError(str(e)) from e

        known = self._known_column_uuids(analysis)
        report = validate_validation_block(rows, known_column_uuids=known)
        logger.info(
            "[llm-validation] rows=%d valid=%s rejected=%d issues=%d",
            len(rows),
            report.is_valid,
            ir.rejected_count(),
            len(report.issues),
        )
        return LLMValidationResult(
            raw_reply=reply,
            analysis=analysis,
            ir=ir,
            rows=rows,
            report=report,
        )

    @staticmethod
    def _known_column_uuids(analysis: ValidationAnalysisResult) -> set[str]:
        uuids: set[str] = set()
        for structure in analysis.expected_structures:
            for col in structure.expected_columns:
                uuids.add(col.source_column_uuid)
        for profile in analysis.inventory.columns:
            uuids.add(profile.source_column_uuid)
        return uuids


__all__ = ["LLMValidationService", "LLMValidationError", "LLMValidationResult"]
