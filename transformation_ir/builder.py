# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation (Sprint 6).
# The Transformation Rule Inventory -> Transformation IR builder (tasks 7-10).
#
# Reads ONLY the Deliverable 5 TransformationAnalysisResult and lifts every
# SUPPORTED transformation requirement into a TransformationRuleIR, trusting the
# supported-transformation catalogue as the single source of truth for the exact
# contract field / operation keyword. Unsupported or underivable requirements are
# REJECTED (task 10) - captured for review, never invented into JSON.
# ============================================================================
"""
transformation_ir.builder
==========================
Build a :class:`~transformation_ir.models.TransformationIR` from a Deliverable 5
:class:`~transformation_analysis.models.TransformationAnalysisResult`.

* Task 7  - map transformation intent to a supported transformation.
* Task 8  - resolve the exact contract field / operation keyword (via catalogue).
* Task 9  - preserve exact keyword casing (values copied verbatim).
* Task 10 - reject unsupported transformations (flagged, never invented).
"""
from __future__ import annotations

import logging
from typing import Optional

from transformation_analysis.catalog import get_spec, is_supported
from transformation_analysis.enums import SupportedTransformation, SupportStatus
from transformation_analysis.models import (
    ColumnTransformationProfile,
    DatasetTransformationRequirement,
    TransformationAnalysisResult,
    TransformationRequirement,
)
from transformation_ir.models import (
    NULL_TARGET_FILE,
    ColumnTransformationIR,
    DatasetTransformationIR,
    RejectedTransformation,
    SourceRef,
    TargetRef,
    TransformationGenerationContext,
    TransformationIR,
    TransformationRuleIR,
)

logger = logging.getLogger(__name__)


class TransformationIRBuilder:
    """Lift a transformation rule inventory into the Transformation IR."""

    def build(
        self,
        analysis: TransformationAnalysisResult,
        context: Optional[TransformationGenerationContext] = None,
    ) -> TransformationIR:
        """Build the Transformation IR from a Deliverable 5 analysis result.

        Args:
            analysis: The Deliverable 5 transformation analysis result.
            context: Optional identity/audit context; defaults are used when
                omitted (self-consistent standalone generation).

        Returns:
            The :class:`TransformationIR`.
        """
        ctx = context or TransformationGenerationContext()

        columns: list[ColumnTransformationIR] = []
        rejected: list[RejectedTransformation] = []

        for profile in analysis.inventory.columns:
            column_ir, column_rejected = self._build_column(profile)
            rejected.extend(column_rejected)
            # Keep the column only if it produced at least one emittable op.
            if column_ir.rules:
                columns.append(column_ir)

        dataset_rules = [
            self._build_dataset_rule(d) for d in analysis.inventory.dataset_rules
        ]

        ir = TransformationIR(
            session_id=analysis.session_id,
            context=ctx,
            columns=columns,
            dataset_rules=dataset_rules,
            rejected=rejected,
        )

        logger.info(
            "[transformation-ir] emittable_columns=%d ops=%d dataset_rules=%d rejected=%d",
            len(ir.emittable_columns()),
            ir.rule_count(),
            len(ir.dataset_rules),
            ir.rejected_count(),
        )
        return ir

    # -- per-column ---------------------------------------------------------
    def _build_column(
        self, profile: ColumnTransformationProfile
    ) -> tuple[ColumnTransformationIR, list[RejectedTransformation]]:
        """Lift one target-column profile into a column IR + rejections."""
        target = TargetRef(
            column_uuid=profile.target_column.column_uuid,
            column_name=profile.target_column.column_name,
            position=profile.target_column.position,
            sheet_name=profile.target_column.sheet_name,
            file_name=profile.target_column.file_name,
        )
        sources = [
            SourceRef(
                column_uuid=s.column_uuid,
                column_name=s.column_name,
                sheet_name=s.sheet_name,
                file_name=s.file_name,
            )
            for s in profile.source_columns
        ]

        rules: list[TransformationRuleIR] = []
        rejected: list[RejectedTransformation] = []

        for req in profile.requirements:
            outcome = self._map_requirement(req)
            if isinstance(outcome, RejectedTransformation):
                # Re-anchor the rejection to the target column identity.
                rejected.append(
                    RejectedTransformation(
                        target_column_uuid=target.column_uuid,
                        column_name=target.column_name,
                        kind=req.kind,
                        reason=outcome.reason,
                    )
                )
            else:
                rules.append(outcome)

        # Collect cross-column dependencies from any op that declared them.
        depends_on = self._collect_dependencies(rules)

        column_ir = ColumnTransformationIR(
            target=target,
            sources=sources,
            category=profile.category,
            data_type=profile.target_data_type,
            data_format=None,
            target_file_name=NULL_TARGET_FILE,
            transformation_required=profile.transformation_required,
            rules=rules,
            depends_on=depends_on,
        )
        return column_ir, rejected

    def _map_requirement(
        self, req: TransformationRequirement
    ) -> TransformationRuleIR | RejectedTransformation:
        """Map one requirement to a supported IR op, or reject it (tasks 7-10)."""
        # Task 10 - explicit unsupported status from Deliverable 5.
        if req.status is SupportStatus.UNSUPPORTED:
            return RejectedTransformation(
                target_column_uuid="",
                column_name="",
                kind=req.kind,
                reason=req.unsupported_reason
                or "Requirement was flagged unsupported by transformation analysis.",
            )

        # Task 10 - defensive: the kind is not in the supported catalogue.
        if not is_supported(req.kind):
            return RejectedTransformation(
                target_column_uuid="",
                column_name="",
                kind=req.kind,
                reason=f"Transformation kind '{req.kind.value}' is not in the supported catalogue.",
            )

        # Tasks 7-9 - resolve the exact contract field / op keyword from the
        # catalogue (single source of truth), copying values verbatim.
        spec = get_spec(req.kind)
        return TransformationRuleIR(
            kind=req.kind,
            contract_field=spec.contract_field,
            config_op=spec.config_op,
            keyword=spec.keyword,
            parameters=dict(req.parameters),
            order=req.order,
            signal=req.signal,
            confidence=req.confidence,
            rationale=req.rationale,
        )

    @staticmethod
    def _collect_dependencies(rules: list[TransformationRuleIR]) -> list[str]:
        """De-duplicated, order-preserving union of any declared dependsOn uuids."""
        seen: dict[str, None] = {}
        for rule in rules:
            deps = rule.parameters.get("dependsOn")
            if isinstance(deps, (list, tuple)):
                for dep in deps:
                    if isinstance(dep, str) and dep and dep not in seen:
                        seen[dep] = None
        return list(seen.keys())

    # -- dataset-level ------------------------------------------------------
    def _build_dataset_rule(
        self, req: DatasetTransformationRequirement
    ) -> DatasetTransformationIR:
        """Lift a dataset-level derived-field requirement into IR (task 13)."""
        spec = get_spec(req.kind) if is_supported(req.kind) else None
        return DatasetTransformationIR(
            field_name=req.field_name,
            field_uuid=req.field_uuid,
            dataset_uuid=req.dataset_uuid or "",
            kind=req.kind,
            contract_field="rule",
            config_op=spec.config_op if spec else None,
            keyword=spec.keyword if spec else None,
            parameters=dict(req.parameters),
            rationale=req.rationale,
        )


__all__ = ["TransformationIRBuilder"]
