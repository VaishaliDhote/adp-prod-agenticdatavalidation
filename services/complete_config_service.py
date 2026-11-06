# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation service. REUSABLE.
# Thin orchestration over Deliverables 1-6 + the Deliverable 7 assembler and
# validator. Given a canonical model (or a session to ingest), it generates the
# validation + transformation blocks (Deliverables 4 & 6), assembles the complete
# exact ``ConfigurationContract`` (deterministic JSON compiler), and validates the
# full document (tasks 17-21). Consumed by the /complete-config HTTP routes and
# by tests. Replaces the legacy YAML free-rewrite path for D7.
# ============================================================================
"""
services.complete_config_service
================================
Orchestrates Sprint 7 complete-configuration assembly.

Two-stage, framework-agnostic (mirrors the Deliverable 4 / 6 IR services):

* :meth:`assemble` - canonical model + already-generated blocks -> complete
  ``ConfigurationContract`` + validation report (the pure deterministic step).
* :meth:`generate` - run Deliverables 4 & 6 to produce the blocks first, then
  assemble + validate end-to-end.

Session variants ingest a session's input + expected-output files via the
Deliverable 1 ingestion service (through the Deliverable 4/6 analysis services)
first. Failures are normalized to :class:`CompleteConfigError` so the API layer
can map them to the unified error contract.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from canonical.models import CanonicalAnalysisModel
from complete_config.assembler import CompleteConfigAssembler
from complete_config.models import CompleteConfigContext
from complete_config.validation import (
    CompleteConfigReport,
    validate_complete_config,
)
from contract.models import ConfigurationContract
from ingestion.ingestion_service import IngestionService
from services.transformation_ir_service import (
    GenerationResult as TransformationGenerationResult,
    TransformationIRError,
    TransformationIRService,
)
from services.validation_ir_service import (
    GenerationResult as ValidationGenerationResult,
    ValidationIRError,
    ValidationIRService,
)

logger = logging.getLogger(__name__)


def merge_validation_rule_rows(
    deterministic_rows: Optional[list[dict[str, Any]]],
    llm_rows: Optional[list[dict[str, Any]]],
    *,
    prefer: str = "llm",
) -> list[dict[str, Any]]:
    """Merge deterministic + LLM ``validation-rules`` rows into one list.

    Each compiled ``validation-rules`` row is ONE row per column, keyed by its
    ``sourceColumn`` column UUID. The two sources are merged on that key:

    * columns present in only one source are kept as-is;
    * columns present in both are resolved by ``prefer`` - ``"llm"`` (default)
      keeps the LLM row, ``"deterministic"`` keeps the deterministic row.

    Deterministic ordering is preserved first; any LLM-only columns are appended
    in their original order. Rows missing a ``sourceColumn`` cannot be deduped
    and are always kept.
    """
    det = list(deterministic_rows or [])
    llm = list(llm_rows or [])
    prefer_llm = prefer != "deterministic"

    llm_by_col: dict[str, dict[str, Any]] = {}
    llm_unkeyed: list[dict[str, Any]] = []
    for row in llm:
        key = row.get("sourceColumn")
        if key:
            llm_by_col[key] = row
        else:
            llm_unkeyed.append(row)

    merged: list[dict[str, Any]] = []
    seen_cols: set[str] = set()
    for row in det:
        key = row.get("sourceColumn")
        if key and key in llm_by_col:
            merged.append(llm_by_col[key] if prefer_llm else row)
            seen_cols.add(key)
        else:
            merged.append(row)
            if key:
                seen_cols.add(key)

    # Append LLM-only columns (and any unkeyed LLM rows) that were not merged.
    for row in llm:
        key = row.get("sourceColumn")
        if key and key not in seen_cols:
            merged.append(row)
            seen_cols.add(key)
    merged.extend(llm_unkeyed)
    return merged


class CompleteConfigError(RuntimeError):
    """Raised when complete-configuration assembly or generation fails."""


class CompleteConfigResult:
    """Bundle of everything :meth:`CompleteConfigService.generate` produces.

    Attributes:
        contract: The assembled exact :class:`ConfigurationContract`.
        document: The exact contract ``dict`` (kebab-case keys, blob strings).
        report: The complete-config validation report (tasks 17-21).
        validation: The Deliverable 4 generation result (rows + rejected).
        transformation: The Deliverable 6 generation result (rows + rejected).
    """

    __slots__ = ("contract", "document", "report", "validation", "transformation")

    def __init__(
        self,
        contract: ConfigurationContract,
        document: dict[str, Any],
        report: CompleteConfigReport,
        validation: Optional[ValidationGenerationResult],
        transformation: Optional[TransformationGenerationResult],
    ) -> None:
        self.contract = contract
        self.document = document
        self.report = report
        self.validation = validation
        self.transformation = transformation

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Return the exact deterministic JSON document string (task 22)."""
        return self.contract.to_contract_json(indent=indent)


class CompleteConfigService:
    """Orchestrate complete-configuration generation + validation."""

    def __init__(
        self,
        validation_ir_service: Optional[ValidationIRService] = None,
        transformation_ir_service: Optional[TransformationIRService] = None,
        assembler: Optional[CompleteConfigAssembler] = None,
        ingestion_service: Optional[IngestionService] = None,
    ) -> None:
        self._validation = validation_ir_service or ValidationIRService()
        self._transformation = transformation_ir_service or TransformationIRService()
        self._assembler = assembler or CompleteConfigAssembler()
        self._ingestion = ingestion_service or IngestionService()

    # -- full generation from a prebuilt canonical model -------------------
    def generate(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[CompleteConfigContext] = None,
    ) -> CompleteConfigResult:
        """Generate blocks, assemble the complete config, and validate it.

        Args:
            model: The Deliverable 1 canonical analysis model.
            context: Optional identity/audit/delivery context; defaults are used
                when omitted (self-consistent standalone generation).

        Returns:
            A :class:`CompleteConfigResult` with the assembled contract, the
            exact document, the validation report, and the sub-block results.
        """
        ctx = context or CompleteConfigContext()
        validation = self._generate_validation(model, ctx)
        transformation = self._generate_transformation(model, ctx)
        return self._assemble(model, ctx, validation, transformation)

    def generate_from_session(
        self,
        session_id: str,
        context: Optional[CompleteConfigContext] = None,
    ) -> CompleteConfigResult:
        """Ingest a session, generate blocks, assemble + validate end-to-end.

        The canonical model is ingested ONCE here and reused for both the
        validation / transformation block generation and the column-relations /
        configuration-data assembly, so a session is parsed a single time.
        """
        ctx = context or CompleteConfigContext()
        model = self._ingest(session_id)
        model.session_id = model.session_id or session_id
        return self.generate(model, ctx)

    # -- pure deterministic assembly from prebuilt blocks ------------------
    def assemble(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[CompleteConfigContext] = None,
        *,
        validation_rules: Optional[list[dict[str, Any]]] = None,
        transformation_rules: Optional[list[dict[str, Any]]] = None,
        dataset_transformation_rules: Optional[list[dict[str, Any]]] = None,
    ) -> CompleteConfigResult:
        """Assemble + validate from already-generated blocks (no analysis).

        This is the pure deterministic compiler step: it never runs the LLM or
        the Deliverable 3-6 analyzers. Use it when the caller already has the
        validation / transformation rows (e.g. the targeted-edit flow).
        """
        ctx = context or CompleteConfigContext()
        try:
            contract = self._assembler.assemble(
                model=model,
                context=ctx,
                validation_rules=validation_rules,
                transformation_rules=transformation_rules,
                dataset_transformation_rules=dataset_transformation_rules,
            )
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[complete-config] Assembly failed")
            raise CompleteConfigError(str(e)) from e

        report = validate_complete_config(contract)
        document = contract.to_contract_dict()
        logger.info(
            "[complete-config] assembled valid=%s contract_valid=%s errors=%d warnings=%d",
            report.is_valid,
            report.contract_valid,
            report.error_count,
            report.warning_count,
        )
        return CompleteConfigResult(
            contract=contract,
            document=document,
            report=report,
            validation=None,
            transformation=None,
        )

    # -- deterministic + LLM merge -----------------------------------------
    def generate_merged(
        self,
        model: CanonicalAnalysisModel,
        llm_validation_rules: Optional[list[dict[str, Any]]],
        context: Optional[CompleteConfigContext] = None,
        *,
        prefer: str = "llm",
    ) -> tuple[CompleteConfigResult, dict[str, int]]:
        """Assemble a final config from DETERMINISTIC + LLM validation rules.

        Runs the deterministic validation + transformation generators over the
        canonical model, merges the deterministic ``validation-rules`` rows with
        the supplied LLM rows (per-column by ``sourceColumn``; ``prefer`` decides
        the winner on conflicts), then assembles + validates the complete config.

        Returns the :class:`CompleteConfigResult` plus a small counts dict so the
        caller can report how many rules came from each source.
        """
        ctx = context or CompleteConfigContext()
        validation = self._generate_validation(model, ctx)
        transformation = self._generate_transformation(model, ctx)
        merged = merge_validation_rule_rows(
            validation.rows, llm_validation_rules, prefer=prefer
        )
        result = self.assemble(
            model,
            ctx,
            validation_rules=merged,
            transformation_rules=transformation.rows,
            dataset_transformation_rules=transformation.dataset_rows,
        )
        counts = {
            "deterministic_validation_count": len(validation.rows),
            "llm_validation_count": len(llm_validation_rules or []),
            "merged_validation_count": len(merged),
            "transformation_count": len(transformation.rows),
            "dataset_transformation_count": len(transformation.dataset_rows),
        }
        return result, counts

    def generate_merged_from_session(
        self,
        session_id: str,
        llm_validation_rules: Optional[list[dict[str, Any]]],
        context: Optional[CompleteConfigContext] = None,
        *,
        prefer: str = "llm",
    ) -> tuple[CompleteConfigResult, dict[str, int]]:
        """Ingest a session then run :meth:`generate_merged` end-to-end."""
        ctx = context or CompleteConfigContext()
        model = self._ingest(session_id)
        model.session_id = model.session_id or session_id
        return self.generate_merged(
            model, llm_validation_rules, ctx, prefer=prefer
        )

    # -- internals ----------------------------------------------------------
    def _assemble(
        self,
        model: CanonicalAnalysisModel,
        context: CompleteConfigContext,
        validation: ValidationGenerationResult,
        transformation: TransformationGenerationResult,
    ) -> CompleteConfigResult:
        try:
            contract = self._assembler.assemble(
                model=model,
                context=context,
                validation_rules=validation.rows,
                transformation_rules=transformation.rows,
                dataset_transformation_rules=transformation.dataset_rows,
            )
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[complete-config] Assembly failed")
            raise CompleteConfigError(str(e)) from e

        report = validate_complete_config(contract)
        document = contract.to_contract_dict()
        logger.info(
            "[complete-config] generated valid=%s contract_valid=%s "
            "columns=%d validation=%d transformation=%d dataset=%d "
            "rejected_validation=%d rejected_transformation=%d",
            report.is_valid,
            report.contract_valid,
            report.column_count,
            report.validation_count,
            report.transformation_count,
            report.dataset_count,
            validation.ir.rejected_count(),
            transformation.ir.rejected_count(),
        )
        return CompleteConfigResult(
            contract=contract,
            document=document,
            report=report,
            validation=validation,
            transformation=transformation,
        )

    def _generate_validation(
        self, model: CanonicalAnalysisModel, context: CompleteConfigContext
    ) -> ValidationGenerationResult:
        try:
            return self._validation.generate(model, context.validation_context())
        except ValidationIRError as e:
            raise CompleteConfigError(f"validation generation failed: {e}") from e

    def _generate_transformation(
        self, model: CanonicalAnalysisModel, context: CompleteConfigContext
    ) -> TransformationGenerationResult:
        try:
            return self._transformation.generate(
                model, context.transformation_context()
            )
        except TransformationIRError as e:
            raise CompleteConfigError(f"transformation generation failed: {e}") from e

    def _ingest(self, session_id: str) -> CanonicalAnalysisModel:
        """Ingest a session's input + expected-output files into a canonical model.

        Ingestion / parse errors propagate unchanged so the route can map them to
        their specific codes; only genuinely unexpected failures are normalized.
        """
        return self._ingestion.ingest_session(session_id)


__all__ = [
    "CompleteConfigService",
    "CompleteConfigError",
    "CompleteConfigResult",
    "merge_validation_rule_rows",
]
