# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# The execution accuracy-loop controller (tasks 19-25).
#
# Orchestrates one deterministic development-accuracy cycle and repeats it:
#   assemble (D7) -> compose -> push (D8 client/simulator) -> capture -> compare
#   -> measure accuracy -> analyze -> auto-apply corrections -> regenerate.
# It enforces the five-iteration cap (task 23) and the 95% acceptance criterion
# (task 24) and preserves the full per-cycle history (task 25).
# ============================================================================
"""
accuracy_loop.loop
=================
The :class:`AccuracyLoopController` drives the full Deliverable 8 loop.

Each iteration regenerates the exact configuration from the current rule rows via
the Deliverable 7 deterministic assembler (so corrections applied to the IR rows
are reflected in byte-stable JSON - tasks 20, 22), pushes it to the backend,
compares, measures, analyzes, and - when auto-approve is on and the stop
criteria are not yet met - applies the actionable suggestions to the rows before
the next cycle (task 19). Row indices are stable because the assembler carries
pre-compiled rows through verbatim (only re-stamping the identity triple), so a
suggestion targeting row *k* always edits the same rule it was raised for.
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from accuracy_loop.accuracy import compute_accuracy, mismatch_signature, should_stop
from accuracy_loop.analysis import MismatchAnalyzer
from accuracy_loop.comparison import build_column_index, build_mismatches
from accuracy_loop.composer import ConfigPushComposer
from accuracy_loop.models import (
    AccuracyLoopContext,
    AccuracyReport,
    ConfigBlock,
    ExecutionBackend,
    IterationRecord,
    LoopStopReason,
    Suggestion,
    SuggestionAction,
)
from canonical.models import CanonicalAnalysisModel
from complete_config.models import CompleteConfigContext
from contract.models import ConfigurationContract

logger = logging.getLogger(__name__)


@dataclass
class LoopOutcome:
    """Everything one :meth:`AccuracyLoopController.run` produces."""

    contract: ConfigurationContract
    document: dict[str, Any]
    complete_config_report: Any
    iterations: list[IterationRecord] = field(default_factory=list)
    final_accuracy: Optional[AccuracyReport] = None
    stop_reason: Optional[LoopStopReason] = None
    final_rows: dict[str, list[dict]] = field(default_factory=dict)


class AccuracyLoopController:
    """Run the develop-execute-compare-correct loop to convergence."""

    def __init__(
        self,
        complete_config_service: Any,
        composer: Optional[ConfigPushComposer] = None,
        analyzer: Optional[MismatchAnalyzer] = None,
    ) -> None:
        self._complete = complete_config_service
        self._composer = composer or ConfigPushComposer()
        self._analyzer = analyzer or MismatchAnalyzer()

    def run(
        self,
        model: CanonicalAnalysisModel,
        cc_context: CompleteConfigContext,
        loop_context: AccuracyLoopContext,
        rows: dict[str, list[dict]],
        backend: ExecutionBackend,
    ) -> LoopOutcome:
        """Execute the loop until a stopping criterion is met.

        Args:
            model: Canonical model (source of column-relations/configuration-data).
            cc_context: Deliverable 7 identity/audit/delivery context.
            loop_context: Loop controls (max iterations, target accuracy, ...).
            rows: Working rule rows keyed ``validation``/``transformation``/``dataset``.
            backend: The push target (real client or offline simulator).

        Returns:
            A :class:`LoopOutcome` with the final config + full iteration history.
        """
        current = {
            "validation": rows.get("validation", []),
            "transformation": rows.get("transformation", []),
            "dataset": rows.get("dataset", []),
        }
        iterations: list[IterationRecord] = []
        prev_signature: Optional[frozenset] = None
        contract: Optional[ConfigurationContract] = None
        document: dict[str, Any] = {}
        report: Any = None
        accuracy: Optional[AccuracyReport] = None
        stop: Optional[LoopStopReason] = None

        for iteration in range(1, loop_context.max_iterations + 1):
            # 20/22) regenerate the exact config from the current rows.
            result = self._complete.assemble(
                model, cc_context,
                validation_rules=current["validation"],
                transformation_rules=current["transformation"],
                dataset_transformation_rules=current["dataset"],
            )
            contract, document, report = result.contract, result.document, result.report

            # 3/4/5) compose + push, then 8) capture.
            operations = self._composer.compose(contract)
            push_results = backend.push(operations)

            data = document["data"]
            column_rows = data.get(ConfigBlock.COLUMN_RELATIONS.value, [])
            col_index = build_column_index(column_rows)

            # 11-15) identify mismatches; 16) measure accuracy.
            mismatches = build_mismatches(push_results, col_index)
            accuracy = compute_accuracy(push_results)

            rows_by_block = {
                ConfigBlock.COLUMN_RELATIONS.value: column_rows,
                ConfigBlock.VALIDATION_RULES.value: data.get(ConfigBlock.VALIDATION_RULES.value, []),
                ConfigBlock.TRANSFORMATION_RULES.value: data.get(ConfigBlock.TRANSFORMATION_RULES.value, []),
                ConfigBlock.DATASET_TRANSFORMATION_RULES.value: data.get(
                    ConfigBlock.DATASET_TRANSFORMATION_RULES.value, []
                ),
            }

            # 17/18) analyze + suggest.
            suggestions = (
                self._analyzer.analyze(mismatches, rows_by_block, loop_context)
                if mismatches else []
            )
            actionable = [s for s in suggestions if s.auto_applicable]

            signature = mismatch_signature(mismatches)
            is_fixpoint = prev_signature is not None and signature == prev_signature
            stop = should_stop(
                accuracy, iteration, loop_context,
                actionable_suggestions=len(actionable), is_fixpoint=is_fixpoint,
            )

            # 19/20) auto-apply corrections when continuing.
            applied = 0
            if stop is None and loop_context.auto_approve:
                edited, applied = self._apply(rows_by_block, actionable)
                current = {
                    "validation": edited[ConfigBlock.VALIDATION_RULES.value],
                    "transformation": edited[ConfigBlock.TRANSFORMATION_RULES.value],
                    "dataset": edited[ConfigBlock.DATASET_TRANSFORMATION_RULES.value],
                }

            iterations.append(
                IterationRecord(
                    iteration=iteration,
                    accuracy=accuracy,
                    pushed_counts={op.block.value: op.item_count for op in operations},
                    push_results=push_results,
                    mismatches=mismatches,
                    suggestions=suggestions,
                    applied_count=applied,
                    stop_reason=stop,
                )
            )
            prev_signature = signature

            if stop is not None:
                break
            if applied == 0:
                # Nothing changed and no stop reason: guard against a spin.
                stop = LoopStopReason.FIXPOINT
                iterations[-1].stop_reason = stop
                break

        logger.info(
            "[accuracy-loop] finished after %d iteration(s): accuracy=%.4f reason=%s",
            len(iterations), accuracy.accuracy if accuracy else 0.0,
            stop.value if stop else "unknown",
        )
        return LoopOutcome(
            contract=contract,  # type: ignore[arg-type]
            document=document,
            complete_config_report=report,
            iterations=iterations,
            final_accuracy=accuracy,
            stop_reason=stop,
            final_rows=current,
        )

    # -- correction application (task 19) ----------------------------------
    @staticmethod
    def _apply(
        rows_by_block: dict[str, list[dict]], suggestions: list[Suggestion]
    ) -> tuple[dict[str, list[dict]], int]:
        """Apply actionable suggestions to a deep copy of the rows.

        Field fixes are applied first (index-stable), then rule drops per block in
        descending index order so earlier drops never shift later indices.
        """
        work = {k: [copy.deepcopy(r) for r in v] for k, v in rows_by_block.items()}
        applied = 0

        for s in suggestions:
            if not s.auto_applicable or s.action not in (SuggestionAction.FIX_FIELD, SuggestionAction.ALIGN_FIELD):
                continue
            block_rows = work.get(s.block.value)
            if block_rows is None or not s.target_field:
                continue
            if 0 <= s.row_index < len(block_rows):
                block_rows[s.row_index][s.target_field] = s.new_value
                applied += 1

        drops: dict[str, list[int]] = defaultdict(list)
        for s in suggestions:
            if s.auto_applicable and s.action == SuggestionAction.DROP_RULE:
                drops[s.block.value].append(s.row_index)
        for block_name, idxs in drops.items():
            block_rows = work.get(block_name)
            if block_rows is None:
                continue
            for idx in sorted(set(idxs), reverse=True):
                if 0 <= idx < len(block_rows):
                    del block_rows[idx]
                    applied += 1

        return work, applied


__all__ = ["AccuracyLoopController", "LoopOutcome"]
