# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Public API of the accuracy-loop package.
#
# Deliverable 8 takes the exact ``ConfigurationContract`` produced by Deliverable
# 7, pushes each of its five blocks to the existing DVTS backend (composed from
# the available ``/ahub/ds/config/*`` endpoints - there is no single "execute"
# endpoint), captures per-row acceptance/rejection, measures accuracy, analyzes
# the mismatches, auto-applies deterministic rule-change suggestions to the
# validation / transformation rows, regenerates the affected JSON via the
# Deliverable 7 deterministic assembler, and repeats - up to five iterations or
# until a 95% acceptance criterion is met - preserving the full iteration
# history (Sprint 8 tasks 1-25).
# ============================================================================
"""accuracy_loop - Deliverable 8 execution + development accuracy loop."""
from __future__ import annotations

from accuracy_loop.accuracy import compute_accuracy, should_stop
from accuracy_loop.analysis import MismatchAnalyzer
from accuracy_loop.client import ExecutionApiClient
from accuracy_loop.comparison import build_mismatches, compare_with_known_output
from accuracy_loop.composer import ConfigPushComposer
from accuracy_loop.loop import AccuracyLoopController, LoopOutcome
from accuracy_loop.models import (
    AccuracyLoopContext,
    AccuracyReport,
    BlockPushResult,
    ConfigBlock,
    ExecutionApiConfig,
    ExecutionBackend,
    IterationRecord,
    LoopStopReason,
    Mismatch,
    MismatchKind,
    OutputCellDiff,
    OutputComparisonReport,
    PushOperation,
    RowRejection,
    Suggestion,
    SuggestionAction,
)
from accuracy_loop.output_comparison import (
    CompareOptions,
    OutputLocations,
    compare_output_files,
    compare_tabular,
    load_tabular,
    uri_to_local_path,
)
from accuracy_loop.simulator import ExecutionSimulator

__all__ = [
    "AccuracyLoopContext",
    "AccuracyLoopController",
    "AccuracyReport",
    "BlockPushResult",
    "CompareOptions",
    "ConfigBlock",
    "ConfigPushComposer",
    "ExecutionApiClient",
    "ExecutionApiConfig",
    "ExecutionBackend",
    "ExecutionSimulator",
    "IterationRecord",
    "LoopOutcome",
    "LoopStopReason",
    "Mismatch",
    "MismatchAnalyzer",
    "MismatchKind",
    "OutputCellDiff",
    "OutputComparisonReport",
    "OutputLocations",
    "PushOperation",
    "RowRejection",
    "Suggestion",
    "SuggestionAction",
    "build_mismatches",
    "compare_output_files",
    "compare_tabular",
    "compare_with_known_output",
    "compute_accuracy",
    "load_tabular",
    "should_stop",
    "uri_to_local_path",
]
