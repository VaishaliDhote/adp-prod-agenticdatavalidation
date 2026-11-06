# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Accuracy measurement (task 16) + stopping criteria (tasks 23, 24).
# ============================================================================
"""
accuracy_loop.accuracy
=====================
Deterministic accuracy measurement and stop-decision helpers.

* :func:`compute_accuracy` - accepted / total rows across every pushed block,
  with per-block and per-kind breakdowns (task 16).
* :func:`should_stop` - encodes the loop's stopping criteria: converged (no
  mismatches), 95% target reached (task 24), five-iteration cap (task 23), no
  actionable suggestions left, or a fixpoint (identical mismatches twice).
"""
from __future__ import annotations

from typing import Optional

from accuracy_loop.models import (
    AccuracyLoopContext,
    AccuracyReport,
    BlockPushResult,
    LoopStopReason,
    Mismatch,
    OutputComparisonReport,
)


def compute_accuracy(
    push_results: list[BlockPushResult],
    *,
    output_report: Optional[OutputComparisonReport] = None,
) -> AccuracyReport:
    """Measure acceptance across all pushed rows (task 16).

    Accuracy is ``accepted / total``. A push carrying no rows scores 1.0 (nothing
    to reject). Per-block accuracy and per-kind rejection counts support the
    mismatch breakdowns surfaced to the UI/tests.

    When ``output_report`` is provided (tasks 9-11), the cell-level produced-vs-
    known match is folded into the overall accuracy: ``(accepted_rows +
    matched_cells) / (total_rows + expected_cells)``. The per-block/per-kind
    breakdowns stay row-based, and ``output_comparison_pending`` flips to
    ``False`` so callers know the output diff actually ran.
    """
    total = sum(r.item_count for r in push_results)
    accepted = sum(r.accepted_count for r in push_results)
    rejected = max(total - accepted, 0)

    by_block: dict[str, float] = {}
    for r in push_results:
        if r.item_count:
            by_block[r.block.value] = round(r.accepted_count / r.item_count, 6)
        else:
            by_block[r.block.value] = 1.0

    by_kind: dict[str, int] = {}
    for r in push_results:
        for rej in r.rejections:
            by_kind[rej.kind.value] = by_kind.get(rej.kind.value, 0) + 1

    row_accuracy = 1.0 if total == 0 else round(accepted / total, 6)

    if output_report is not None:
        combined_num = accepted + output_report.matched_cells
        combined_den = total + output_report.expected_cells
        accuracy = 1.0 if combined_den == 0 else round(combined_num / combined_den, 6)
        output_accuracy: Optional[float] = output_report.output_accuracy
        pending = False
    else:
        accuracy = row_accuracy
        output_accuracy = None
        pending = True

    return AccuracyReport(
        accuracy=accuracy,
        total_items=total,
        accepted_items=accepted,
        rejected_items=rejected,
        by_block=by_block,
        by_kind=by_kind,
        output_accuracy=output_accuracy,
        output_report=output_report,
        output_comparison_pending=pending,
    )


def mismatch_signature(mismatches: list[Mismatch]) -> frozenset[tuple[str, int, str]]:
    """Stable signature of a mismatch set for fixpoint detection."""
    return frozenset(
        (m.block.value if m.block else "output", m.row_index, m.code) for m in mismatches
    )


def should_stop(
    report: AccuracyReport,
    iteration: int,
    context: AccuracyLoopContext,
    *,
    actionable_suggestions: int,
    is_fixpoint: bool,
) -> Optional[LoopStopReason]:
    """Decide whether the loop should stop after the current cycle.

    Priority: converged -> target reached (task 24) -> iteration cap (task 23) ->
    nothing left to try -> fixpoint. Returns ``None`` to continue.
    """
    if report.rejected_items == 0:
        return LoopStopReason.NO_MISMATCHES
    if report.accuracy >= context.target_accuracy:
        return LoopStopReason.TARGET_ACCURACY
    if iteration >= context.max_iterations:
        return LoopStopReason.MAX_ITERATIONS
    if actionable_suggestions == 0:
        return LoopStopReason.NO_SUGGESTIONS
    if is_fixpoint:
        return LoopStopReason.FIXPOINT
    return None


__all__ = ["compute_accuracy", "mismatch_signature", "should_stop"]
