# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Agent-based mismatch analysis (task 17) + rule-change suggestions (task 18).
#
# Deterministic-first: every mismatch this system raises has a knowledge-grounded,
# reproducible repair (drop an unresolvable rule, swap an inverted length range,
# reset an unresolved target file, correct an out-of-vocabulary rule type). An
# optional LLM advisor can be supplied to propose repairs for mismatches the
# deterministic mapper marks MANUAL, without ever overriding a deterministic fix.
# ============================================================================
"""
accuracy_loop.analysis
=====================
Convert mismatches into concrete, auto-applicable rule-change suggestions.

The analyzer is intentionally deterministic so the loop converges reproducibly
and is fully testable offline. The optional ``llm_advisor`` hook (a callable
receiving the unresolved mismatches + rows and returning extra suggestions) lets
a Bedrock / gateway model assist with genuinely ambiguous cases (tasks 17, 18)
while keeping the default path free of any network dependency.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from accuracy_loop.models import (
    AccuracyLoopContext,
    ConfigBlock,
    Mismatch,
    MismatchKind,
    Suggestion,
    SuggestionAction,
)

logger = logging.getLogger(__name__)

# Optional LLM advisor signature: (unresolved_mismatches, rows_by_block, context)
LlmAdvisor = Callable[[list[Mismatch], dict[str, list[dict]], AccuracyLoopContext], list[Suggestion]]


class MismatchAnalyzer:
    """Map mismatches to rule-change suggestions (tasks 17, 18)."""

    def __init__(self, *, llm_advisor: Optional[LlmAdvisor] = None) -> None:
        self._llm_advisor = llm_advisor

    def analyze(
        self,
        mismatches: list[Mismatch],
        rows_by_block: dict[str, list[dict]],
        context: AccuracyLoopContext,
    ) -> list[Suggestion]:
        """Return one or more suggestions per mismatch.

        Deterministic repairs are produced first. Any mismatch the deterministic
        mapper cannot resolve (``MANUAL``) is optionally forwarded to the LLM
        advisor, whose suggestions are appended but never replace a deterministic
        fix for the same row/field.
        """
        suggestions: list[Suggestion] = []
        unresolved: list[Mismatch] = []

        for mm in mismatches:
            produced = self._deterministic(mm, rows_by_block)
            if produced:
                suggestions.extend(produced)
            else:
                unresolved.append(mm)

        if unresolved and self._llm_advisor is not None:
            try:
                extra = self._llm_advisor(unresolved, rows_by_block, context)
                suggestions.extend(extra)
            except Exception as exc:  # noqa: BLE001 - advisory only, never fatal
                logger.warning("[accuracy-loop] LLM advisor failed: %s", exc)
        else:
            # Record MANUAL suggestions so the report shows the unresolved items.
            for mm in unresolved:
                suggestions.append(self._manual(mm))

        return suggestions

    # -- deterministic repair rules ----------------------------------------
    def _deterministic(
        self, mm: Mismatch, rows_by_block: dict[str, list[dict]]
    ) -> list[Suggestion]:
        if mm.code == "REF_UNKNOWN_COLUMN":
            return [self._drop(mm, "references a column uuid absent from column-relations")]
        if mm.code == "DATASET_UNRESOLVED":
            return [self._drop(mm, "references a dataset id not declared in configuration-data")]
        if mm.code == "TARGET_FILE_UNRESOLVED":
            return [
                self._fix(
                    mm, "targetFileName", "<null>",
                    "reset targetFileName to the '<null>' default output sentinel",
                )
            ]
        if mm.code == "RULE_TYPE_INVALID":
            return [
                self._fix(
                    mm, "validationRuleType", "client",
                    "reset validationRuleType to the default 'client' scope",
                )
            ]
        if mm.code == "LENGTH_RANGE_INVALID":
            return self._fix_length_range(mm, rows_by_block)
        return []

    def _fix_length_range(
        self, mm: Mismatch, rows_by_block: dict[str, list[dict]]
    ) -> list[Suggestion]:
        rows = rows_by_block.get(mm.block.value, [])
        if not (0 <= mm.row_index < len(rows)):
            return [self._manual(mm)]
        row = rows[mm.row_index]
        lo, hi = row.get("minLengthAllowed"), row.get("maxLengthAllowed")
        try:
            int(lo)  # type: ignore[arg-type]
            int(hi)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return [self._manual(mm)]
        # Swap so min <= max while preserving both authored bounds.
        return [
            self._fix(mm, "minLengthAllowed", str(hi),
                      "swap inverted length bounds so minLengthAllowed <= maxLengthAllowed"),
            self._fix(mm, "maxLengthAllowed", str(lo),
                      "swap inverted length bounds so maxLengthAllowed >= minLengthAllowed"),
        ]

    # -- suggestion factories ----------------------------------------------
    @staticmethod
    def _fix(mm: Mismatch, field: str, value: Any, rationale: str) -> Suggestion:
        return Suggestion(
            block=mm.block, kind=mm.kind, action=SuggestionAction.FIX_FIELD,
            row_index=mm.row_index, target_field=field, new_value=value,
            column_uuid=mm.column_uuid, rationale=rationale,
            auto_applicable=True, source="deterministic",
        )

    @staticmethod
    def _drop(mm: Mismatch, rationale: str) -> Suggestion:
        return Suggestion(
            block=mm.block, kind=mm.kind, action=SuggestionAction.DROP_RULE,
            row_index=mm.row_index, target_field=None, new_value=None,
            column_uuid=mm.column_uuid, rationale=f"drop the rule: {rationale}",
            auto_applicable=True, source="deterministic",
        )

    @staticmethod
    def _manual(mm: Mismatch) -> Suggestion:
        return Suggestion(
            block=mm.block, kind=mm.kind, action=SuggestionAction.MANUAL,
            row_index=mm.row_index, target_field=mm.field, new_value=None,
            column_uuid=mm.column_uuid,
            rationale=f"no automatic repair for {mm.code}; needs human review",
            auto_applicable=False, source="deterministic",
        )


__all__ = ["MismatchAnalyzer", "LlmAdvisor"]
