# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation (Sprint 4).
# The inventory -> IR builder (tasks 6-9).
#
# Converts a Deliverable 3 ValidationAnalysisResult (the "what must be
# validated" inventory) into the Validation IR. Every SUPPORTED requirement is
# mapped onto its exact contract field + fixed keyword via the shared
# validation_analysis.catalog (the single source of truth for "what the API can
# represent"); every UNSUPPORTED requirement - and anything that cannot be
# faithfully mapped here - is REJECTED into ValidationIR.rejected instead of
# being invented into JSON. This is the guardrail that keeps generation
# deterministic and contract-faithful.
# ============================================================================
"""
validation_ir.builder
======================
Build the Validation IR from the Sprint 3 validation inventory (tasks 6-9).

* Task 6 - map each validation requirement to a supported catalogue entry.
* Task 7 - resolve the exact contract field/keyword from the catalogue.
* Task 8 - preserve exact keyword casing/spelling (taken verbatim).
* Task 9 - reject unsupported requirements (flag, never invent).
"""
from __future__ import annotations

import logging
from typing import Optional

from validation_analysis.catalog import get_spec, is_supported
from validation_analysis.enums import SupportStatus
from validation_analysis.models import (
    ColumnValidationProfile,
    ValidationAnalysisResult,
    ValidationRequirement,
)
from validation_ir.models import (
    ColumnReference,
    ColumnValidationIR,
    RejectedValidation,
    ValidationGenerationContext,
    ValidationIR,
    ValidationRuleIR,
)

logger = logging.getLogger(__name__)


class ValidationIRBuilder:
    """Deterministically convert a validation inventory into the Validation IR."""

    def build(
        self,
        analysis: ValidationAnalysisResult,
        context: Optional[ValidationGenerationContext] = None,
    ) -> ValidationIR:
        """Build the Validation IR from a Sprint 3 analysis result.

        Args:
            analysis: The Deliverable 3 :class:`ValidationAnalysisResult`.
            context: Optional identity/audit overrides. When ``None`` the
                self-consistent defaults in :class:`ValidationGenerationContext`
                are used (the orchestrator injects a real identity triple).

        Returns:
            A fully-populated :class:`ValidationIR` whose emittable columns each
            carry only SUPPORTED, faithfully-mapped rules, plus a ``rejected``
            list of every requirement that could not be represented (task 9).
        """
        ctx = context or ValidationGenerationContext()
        columns: list[ColumnValidationIR] = []
        rejected: list[RejectedValidation] = []

        for profile in analysis.inventory.columns:
            column_ir, column_rejected = self._build_column(profile)
            rejected.extend(column_rejected)
            # Keep the column only if at least one supported rule survived.
            if column_ir.rules:
                columns.append(column_ir)

        ir = ValidationIR(
            session_id=analysis.session_id,
            context=ctx,
            columns=columns,
            rejected=rejected,
        )
        logger.info(
            "[validation-ir] built columns=%d rules=%d rejected=%d",
            len(ir.emittable_columns()),
            ir.rule_count(),
            ir.rejected_count(),
        )
        return ir

    # -- per-column ---------------------------------------------------------
    def _build_column(
        self, profile: ColumnValidationProfile
    ) -> tuple[ColumnValidationIR, list[RejectedValidation]]:
        """Map one column profile into a column IR + its rejected list."""
        reference = ColumnReference(
            column_uuid=profile.source_column_uuid,
            column_name=profile.column_name,
        )
        column_ir = ColumnValidationIR(
            source_column=reference,
            data_type=profile.data_type,
            file_name=profile.file_name,
            sheet_name=profile.sheet_name,
        )
        rejected: list[RejectedValidation] = []

        for req in profile.requirements:
            rule = self._map_requirement(profile, req, rejected)
            if rule is not None:
                column_ir.rules.append(rule)

        return column_ir, rejected

    # -- per-requirement ----------------------------------------------------
    def _map_requirement(
        self,
        profile: ColumnValidationProfile,
        req: ValidationRequirement,
        rejected: list[RejectedValidation],
    ) -> Optional[ValidationRuleIR]:
        """Map one requirement to an IR rule, or reject it (tasks 6-9).

        Returns ``None`` (and appends to ``rejected``) when the requirement is
        unsupported or cannot be faithfully mapped to a catalogue entry.
        """
        # Task 9 - honour Sprint 3's explicit unsupported flag.
        if req.status is SupportStatus.UNSUPPORTED:
            rejected.append(
                self._reject(
                    profile,
                    req,
                    req.unsupported_reason
                    or "Requirement was flagged unsupported during analysis.",
                )
            )
            return None

        # Task 6 - the kind must exist in the supported catalogue.
        if not is_supported(req.kind):
            rejected.append(
                self._reject(
                    profile,
                    req,
                    f"No supported API validation exists for kind '{req.kind.value}'.",
                )
            )
            return None

        spec = get_spec(req.kind)

        # Task 7/8 - resolve the exact contract field and fixed keyword verbatim.
        # The requirement's contract_field must agree with the catalogue; if it
        # somehow drifted, trust the catalogue (single source of truth).
        contract_field = spec.contract_field
        keyword = self._resolve_keyword(spec.keyword, req)

        return ValidationRuleIR(
            kind=req.kind,
            contract_field=contract_field,
            keyword=keyword,
            parameters=dict(req.parameters),  # task 14 - carry params forward
            depends_on=list(req.parameters.get("dependsOn", []) or []),  # task 5
            source=req.source,
            confidence=req.confidence,
            rationale=req.rationale,
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _resolve_keyword(
        spec_keyword: Optional[str], req: ValidationRequirement
    ) -> Optional[str]:
        """Return the exact fixed keyword (task 8), preferring the catalogue.

        When the catalogue pins a keyword (e.g. ``"Y"``, ``"Email"``) that exact
        token is used verbatim. Otherwise, for data-type/format rules the
        keyword is the data-derived ``dataType`` value carried in parameters.
        """
        if spec_keyword is not None:
            return spec_keyword
        # data-derived: surface the concrete dataType keyword when present.
        data_type = req.parameters.get("dataType")
        return str(data_type) if data_type is not None else None

    @staticmethod
    def _reject(
        profile: ColumnValidationProfile,
        req: ValidationRequirement,
        reason: str,
    ) -> RejectedValidation:
        """Build a :class:`RejectedValidation` for an unmappable requirement."""
        return RejectedValidation(
            source_column_uuid=profile.source_column_uuid,
            column_name=profile.column_name,
            kind=req.kind,
            reason=reason,
        )


__all__ = ["ValidationIRBuilder"]
