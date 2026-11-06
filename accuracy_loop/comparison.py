# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Mismatch identification (tasks 12-15) + produced-vs-known output diff (9-11).
#
# Turns the captured per-block backend responses into normalised, column-aware
# :class:`~accuracy_loop.models.Mismatch` records that the analyzer consumes, and
# delegates the produced-vs-known output comparison (tasks 9-11) to
# :mod:`accuracy_loop.output_comparison` (manual-drop workflow).
# ============================================================================
"""
accuracy_loop.comparison
========================
Identify mismatches from backend responses.

* :func:`build_mismatches` - map every :class:`RowRejection` into a
  :class:`Mismatch`, enriched with the column name resolved from the pushed
  ``column-relations`` block (tasks 12, 13, 14, 15).
* :func:`compare_with_known_output` - diff the manually-downloaded produced
  output file against the known/expected output (tasks 9, 10, 11). Delegates to
  :func:`accuracy_loop.output_comparison.compare_output_files`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from accuracy_loop.models import (
    BlockPushResult,
    ConfigBlock,
    Mismatch,
    OutputComparisonReport,
)


class OutputComparisonPending(NotImplementedError):
    """Deprecated: produced-vs-known output comparison is now implemented.

    Retained only so any external caller importing it keeps working. The real
    implementation lives in :mod:`accuracy_loop.output_comparison`.
    """


def build_column_index(operations_or_rows: Any) -> dict[str, str]:
    """Build a ``uuid -> columnName`` lookup from column-relations rows.

    Accepts either the list of column-relation row dicts or a mapping already
    keyed by uuid. Missing names fall back to the uuid itself downstream.
    """
    index: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    if isinstance(operations_or_rows, list):
        rows = operations_or_rows
    for row in rows:
        uuid = row.get("uuid")
        if uuid:
            index[uuid] = row.get("columnName") or uuid
    return index


def build_mismatches(
    push_results: list[BlockPushResult],
    column_index: dict[str, str] | None = None,
) -> list[Mismatch]:
    """Normalise every rejected row into a :class:`Mismatch` (tasks 12-15)."""
    column_index = column_index or {}
    mismatches: list[Mismatch] = []
    for result in push_results:
        for rej in result.rejections:
            column_name = column_index.get(rej.uuid) if rej.uuid else None
            mismatches.append(
                Mismatch(
                    block=result.block,
                    kind=rej.kind,
                    code=rej.code,
                    row_index=rej.index,
                    column_uuid=rej.uuid,
                    column_name=column_name,
                    field=rej.field,
                    expected=rej.expected,
                    actual=rej.actual,
                    message=rej.message,
                )
            )
    return mismatches


def compare_with_known_output(
    produced_path: str | Path,
    known_path: str | Path,
    *,
    sheet: Optional[str] = None,
) -> tuple[OutputComparisonReport, list[Mismatch]]:
    """Diff the produced output against the known output (tasks 9, 10, 11).

    The produced file is the one the operator downloaded from the backend's
    network share and pasted into the local drop folder; the known file is the
    expected output. Returns the :class:`OutputComparisonReport` plus one
    ``OUTPUT``-kind :class:`Mismatch` per differing cell. Delegates to
    :func:`accuracy_loop.output_comparison.compare_output_files`.
    """
    # Imported lazily to keep this module free of the pandas dependency unless
    # an output comparison is actually requested.
    from accuracy_loop.output_comparison import compare_output_files

    return compare_output_files(produced_path, known_path, sheet=sheet)


__all__ = [
    "OutputComparisonPending",
    "build_column_index",
    "build_mismatches",
    "compare_with_known_output",
]
