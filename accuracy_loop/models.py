# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Domain models for the accuracy loop (tasks 1-25).
#
# Every model here is deterministic and JSON-serialisable so the loop, its
# iteration history, and the final report can be returned verbatim over HTTP and
# asserted in tests. The identity/loop-control settings live in
# :class:`AccuracyLoopContext`; the wire contract with the existing backend lives
# in :class:`ExecutionApiConfig` + :class:`PushOperation` / :class:`BlockPushResult`;
# the analysis surface lives in :class:`Mismatch` / :class:`Suggestion`; the
# per-cycle audit lives in :class:`IterationRecord`.
# ============================================================================
"""
accuracy_loop.models
====================
Context, wire, analysis and history models for Deliverable 8.

Model map::

    AccuracyLoopContext        # tasks 1,2,16,23,24 - identity + loop controls
      -> complete_config_context() -> complete_config.CompleteConfigContext

    ExecutionApiConfig         # task 2 - external API connection settings
    PushOperation              # tasks 3,4 - one block -> one backend call
    BlockPushResult            # tasks 5,6,7,8 - captured response per block
    RowRejection               # a single rejected row inside a block

    Mismatch                   # tasks 11-15 - a normalised discrepancy
    Suggestion                 # tasks 17,18 - a proposed rule-change
    AccuracyReport             # task 16 - accepted / total ratio + breakdowns
    IterationRecord            # tasks 22,25 - one cycle of the loop
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ConfigBlock(str, Enum):
    """The five configuration sections, keyed by their exact contract names."""

    COLUMN_RELATIONS = "column-relations"
    CONFIGURATION_DATA = "configuration-data"
    DATASET_TRANSFORMATION_RULES = "dataset-transformation-rules"
    TRANSFORMATION_RULES = "transformation-rules"
    VALIDATION_RULES = "validation-rules"


class MismatchKind(str, Enum):
    """Classification of a discrepancy (tasks 12-15, and 10-11 for output)."""

    VALIDATION = "validation"          # task 12 - a validation-rule was rejected
    TRANSFORMATION = "transformation"  # task 13 - a transformation-rule was rejected
    MAPPING = "mapping"                # task 14 - a source/target reference is unresolved
    CONFIG = "config"                  # task 15 - a structural/consistency error
    OUTPUT = "output"                  # tasks 10-11 - produced vs known output (pending)


class SuggestionAction(str, Enum):
    """How a mismatch should be corrected (task 18)."""

    FIX_FIELD = "fix_field"     # set a field on a row to a corrected value
    ALIGN_FIELD = "align_field"  # align a field to another block's value
    DROP_RULE = "drop_rule"     # remove an unresolvable rule row
    MANUAL = "manual"           # cannot be auto-fixed; needs a human


class LoopStopReason(str, Enum):
    """Why the accuracy loop stopped (tasks 23, 24)."""

    TARGET_ACCURACY = "target_accuracy_reached"   # task 24
    MAX_ITERATIONS = "max_iterations_reached"     # task 23
    NO_MISMATCHES = "no_mismatches"
    FIXPOINT = "no_further_improvement"
    NO_SUGGESTIONS = "no_actionable_suggestions"


# ---------------------------------------------------------------------------
# Loop context (identity triple + audit + loop controls)
# ---------------------------------------------------------------------------
class AccuracyLoopContext(BaseModel):
    """Identity, audit, delivery metadata and loop-control settings (task 1).

    The identity triple + audit + delivery fields mirror
    :class:`complete_config.models.CompleteConfigContext` so the regenerated JSON
    is stamped identically across iterations (guarantees deterministic
    regeneration - tasks 20, 22). The loop-control fields drive the stopping
    criteria (tasks 23, 24) and the auto-apply behaviour (task 19).
    """

    model_config = ConfigDict(extra="forbid")

    # -- identity triple + audit -------------------------------------------
    bu: str = Field("DVTS", description="Business unit (identity triple)")
    platform: str = Field("GENERIC", description="Destination platform (identity triple)")
    data_category: str = Field("GENERATED", description="Business use-case (identity triple)")
    useremail: str = Field("dvts@adp.com", description="Audit owner email")
    userrole: str = Field("DVTS_USER", description="Audit owner role")

    # -- configuration-data delivery metadata (optional passthrough) -------
    app_code: Optional[str] = Field(None, description="Hosting app/tenant ('ahub-*')")
    source: Optional[str] = Field(None, description="Upstream capture tool (DataStudio/MyDC/RPA)")
    target_location: Optional[str] = Field(None, description="Delivery kind (SFG/SharePoint/SharedDrive)")
    target_path: Optional[str] = Field(None, description="Concrete output folder path")

    # -- loop controls ------------------------------------------------------
    max_iterations: int = Field(5, ge=1, le=20, description="Hard cap on cycles (task 23)")
    target_accuracy: float = Field(0.85, ge=0.0, le=1.0, description="Stop-at accuracy (save threshold)")
    auto_approve: bool = Field(True, description="Auto-apply suggested corrections each cycle (task 19)")
    demo_defects: bool = Field(
        False,
        description=(
            "Verification affordance: deterministically seed a few fixable defects "
            "into the initial rows so the loop demonstrably converges over multiple "
            "iterations (tasks 12-14, 19-22). Never set in production."
        ),
    )

    # -- produced-vs-known output comparison (tasks 9-11, manual workflow) ---
    compare_output: bool = Field(
        False,
        description=(
            "When true, diff the manually-downloaded produced output file against the "
            "known/expected output and fold the cell-level match into the final "
            "accuracy (Deliverable 8 tasks 9-11). The backend writes the produced file "
            "to a network share; the operator pastes it into the local drop folder."
        ),
    )
    produced_output_file: Optional[str] = Field(
        None,
        description=(
            "Produced output filename (looked up in the drop folder) or an explicit "
            "path. When omitted and compare_output is on, the newest file in the drop "
            "folder is used."
        ),
    )
    known_output_file: Optional[str] = Field(
        None,
        description="Known/expected output filename (in the expected folder) or an explicit path.",
    )
    output_sheet: Optional[str] = Field(
        None,
        description="Optional worksheet name to compare when the files are multi-sheet workbooks.",
    )

    def complete_config_context(self) -> "Any":
        """Project onto a Deliverable 7 ``CompleteConfigContext`` for assembly.

        Imported lazily to avoid a hard import cycle between the loop package and
        the complete-config package at module import time.
        """
        from complete_config.models import CompleteConfigContext

        return CompleteConfigContext(
            bu=self.bu,
            platform=self.platform,
            data_category=self.data_category,
            useremail=self.useremail,
            userrole=self.userrole,
            app_code=self.app_code,
            source=self.source,
            target_location=self.target_location,
            target_path=self.target_path,
        )


# ---------------------------------------------------------------------------
# External API connection (task 2)
# ---------------------------------------------------------------------------
class ExecutionApiConfig(BaseModel):
    """Connection settings for the existing DVTS backend (task 2).

    There is no single "execute input file + config" endpoint in the backend;
    the loop *composes* an execution from the available config endpoints
    (``/ahub/ds/config/*``). When :attr:`base_url` is empty the loop falls back
    to the deterministic in-process :class:`~accuracy_loop.simulator.ExecutionSimulator`
    so the whole pipeline is runnable and testable offline.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: Optional[str] = Field(None, description="Backend base URL; empty => use simulator")
    timeout_seconds: float = Field(30.0, gt=0, description="Per-request timeout")
    max_retries: int = Field(2, ge=0, le=5, description="Retry attempts on transient failures")
    verify_ssl: bool = Field(True, description="Verify TLS certificates")
    endpoints: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_ENDPOINTS),
        description="block/action -> path map (composed execution)",
    )
    methods: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_METHODS),
        description="block/action -> HTTP method map",
    )

    @property
    def use_simulator(self) -> bool:
        """True when no real backend URL is configured."""
        return not (self.base_url and self.base_url.strip())

    @classmethod
    def from_env(cls) -> "ExecutionApiConfig":
        """Build config from ``EXECUTION_API_*`` environment variables (task 2)."""
        base = os.getenv("EXECUTION_API_BASE_URL") or os.getenv("REACT_APP_DVTS_NEW_API_URL")
        timeout = os.getenv("EXECUTION_API_TIMEOUT")
        retries = os.getenv("EXECUTION_API_MAX_RETRIES")
        verify = os.getenv("EXECUTION_API_VERIFY_SSL")
        kwargs: dict[str, Any] = {}
        if base:
            kwargs["base_url"] = base.strip()
        if timeout:
            try:
                kwargs["timeout_seconds"] = float(timeout)
            except ValueError:
                pass
        if retries:
            try:
                kwargs["max_retries"] = int(retries)
            except ValueError:
                pass
        if verify is not None:
            kwargs["verify_ssl"] = verify.strip().lower() not in {"0", "false", "no"}
        return cls(**kwargs)


# Default composed-execution endpoint map (from DVTS V2 API quick reference).
_DEFAULT_ENDPOINTS: dict[str, str] = {
    ConfigBlock.COLUMN_RELATIONS.value: "/ahub/ds/config/column-configuration/bulk",
    ConfigBlock.CONFIGURATION_DATA.value: "/ahub/ds/config",
    "version": "/ahub/ds/config/version",
    ConfigBlock.VALIDATION_RULES.value: "/ahub/ds/config/validation-rule",
    ConfigBlock.TRANSFORMATION_RULES.value: "/ahub/ds/config/transformation-rule",
    ConfigBlock.DATASET_TRANSFORMATION_RULES.value: "/ahub/ds/config/dataset/transformation-rule",
}

_DEFAULT_METHODS: dict[str, str] = {
    ConfigBlock.COLUMN_RELATIONS.value: "POST",
    ConfigBlock.CONFIGURATION_DATA.value: "POST",
    "version": "POST",
    ConfigBlock.VALIDATION_RULES.value: "PATCH",
    ConfigBlock.TRANSFORMATION_RULES.value: "PATCH",
    ConfigBlock.DATASET_TRANSFORMATION_RULES.value: "PATCH",
}


# ---------------------------------------------------------------------------
# Push request / response (tasks 3-8)
# ---------------------------------------------------------------------------
class PushOperation(BaseModel):
    """One backend call carrying one configuration block (tasks 3, 4)."""

    model_config = ConfigDict(extra="forbid")

    block: ConfigBlock = Field(..., description="Which section this call carries")
    action: str = Field(..., description="Endpoint key ('block' name or 'version')")
    method: str = Field(..., description="HTTP method")
    path: str = Field(..., description="Backend path (relative to base URL)")
    params: dict[str, str] = Field(default_factory=dict, description="Query params (identity triple)")
    payload: Any = Field(None, description="Request body (list of rows or object)")
    item_count: int = Field(0, ge=0, description="Number of rows carried")


class RowRejection(BaseModel):
    """A single rejected row within a pushed block (tasks 6, 7)."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0, description="Row index within the block payload")
    uuid: Optional[str] = Field(None, description="Column/rule uuid, when applicable")
    kind: MismatchKind = Field(..., description="Why the backend rejected the row")
    code: str = Field(..., description="Stable backend error code")
    field: Optional[str] = Field(None, description="Offending field, when applicable")
    expected: Optional[str] = Field(None, description="What the backend expected")
    actual: Optional[str] = Field(None, description="What was sent")
    message: str = Field(..., description="Human-readable reason")


class BlockPushResult(BaseModel):
    """The captured response for one pushed block (task 8)."""

    model_config = ConfigDict(extra="forbid")

    block: ConfigBlock
    action: str
    method: str
    path: str
    success: bool = Field(..., description="Envelope-level success flag")
    status_code: int = Field(..., description="HTTP status (or synthetic for simulator)")
    item_count: int = Field(0, ge=0, description="Rows sent")
    accepted_count: int = Field(0, ge=0, description="Rows accepted")
    error_code: Optional[str] = Field(None, description="Envelope errorCode on failure")
    message: Optional[str] = Field(None, description="Envelope message")
    rejections: list[RowRejection] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, description="Raw envelope (trimmed)")


# ---------------------------------------------------------------------------
# Analysis surface (tasks 11-18)
# ---------------------------------------------------------------------------
class Mismatch(BaseModel):
    """A normalised discrepancy identified from the backend response (11-15).

    ``block`` is optional: config-block mismatches (tasks 12-15) always carry the
    section they came from, while produced-vs-known output mismatches (tasks
    9-11) have no owning config block and leave it ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    block: Optional[ConfigBlock] = None
    kind: MismatchKind
    code: str
    row_index: int = Field(..., ge=0)
    column_uuid: Optional[str] = None
    column_name: Optional[str] = None
    field: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str


class Suggestion(BaseModel):
    """A proposed rule-change for one mismatch (tasks 17, 18)."""

    model_config = ConfigDict(extra="forbid")

    block: ConfigBlock
    kind: MismatchKind
    action: SuggestionAction
    row_index: int = Field(..., ge=0)
    target_field: Optional[str] = None
    new_value: Any = None
    column_uuid: Optional[str] = None
    rationale: str = Field(..., description="Why this change resolves the mismatch")
    auto_applicable: bool = Field(..., description="True when the loop may apply it unattended")
    source: str = Field("deterministic", description="'deterministic' or 'llm'")


# ---------------------------------------------------------------------------
# Produced-vs-known output comparison (tasks 9-11)
# ---------------------------------------------------------------------------
class OutputCellDiff(BaseModel):
    """A single differing cell between the produced and the known output."""

    model_config = ConfigDict(extra="forbid")

    row_index: int = Field(..., ge=0, description="0-based data row (excludes header)")
    column: str = Field(..., description="Column header the diff was found under")
    expected: Optional[str] = Field(None, description="Known/expected cell value")
    actual: Optional[str] = Field(None, description="Produced cell value")


class OutputComparisonReport(BaseModel):
    """Result of diffing a produced output file against the known output (9-11).

    ``output_accuracy`` is ``matched_cells / expected_cells`` where the
    denominator is the full known grid (``known_rows * known_columns``), so
    missing rows/columns are penalised, not silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    produced_file: str
    known_file: str
    produced_sheet: Optional[str] = None
    known_sheet: Optional[str] = None
    produced_rows: int = Field(..., ge=0)
    known_rows: int = Field(..., ge=0)
    produced_columns: int = Field(..., ge=0)
    known_columns: int = Field(..., ge=0)
    compared_cells: int = Field(..., ge=0, description="Cells actually compared (overlap)")
    matched_cells: int = Field(..., ge=0)
    expected_cells: int = Field(..., ge=0, description="Full known grid = rows * columns")
    output_accuracy: float = Field(..., ge=0.0, le=1.0)
    match: bool = Field(..., description="True when produced == known exactly")
    missing_columns: list[str] = Field(default_factory=list, description="In known, absent from produced")
    extra_columns: list[str] = Field(default_factory=list, description="In produced, absent from known")
    missing_rows: int = Field(0, ge=0, description="Rows in known beyond the produced row count")
    extra_rows: int = Field(0, ge=0, description="Rows in produced beyond the known row count")
    cell_diffs: list[OutputCellDiff] = Field(default_factory=list)
    truncated_diffs: bool = Field(False, description="True when cell_diffs was capped")


# ---------------------------------------------------------------------------
# Accuracy + iteration history (tasks 16, 22, 25)
# ---------------------------------------------------------------------------
class AccuracyReport(BaseModel):
    """Accepted / total measurement for one push cycle (task 16).

    When a produced output file is available, the cell-level output match is
    folded into :attr:`accuracy` (accepted rows + matched cells over total rows +
    expected cells) and :attr:`output_comparison_pending` flips to ``False``
    (tasks 9-11). Otherwise accuracy is measured from backend row-acceptance only.
    """

    model_config = ConfigDict(extra="forbid")

    accuracy: float = Field(..., ge=0.0, le=1.0)
    total_items: int = Field(..., ge=0)
    accepted_items: int = Field(..., ge=0)
    rejected_items: int = Field(..., ge=0)
    by_block: dict[str, float] = Field(default_factory=dict)
    by_kind: dict[str, int] = Field(default_factory=dict)
    output_accuracy: Optional[float] = Field(
        None,
        description="Cell-level produced-vs-known match, when an output file was compared (9-11).",
    )
    output_report: Optional[OutputComparisonReport] = Field(
        None,
        description="Full produced-vs-known output diff, when available (tasks 9-11).",
    )
    output_comparison_pending: bool = Field(
        True,
        description=(
            "True until a produced output file is diffed against the known output "
            "(tasks 9-11); while pending, accuracy is backend row-acceptance only."
        ),
    )


class IterationRecord(BaseModel):
    """One full cycle of the loop: push -> compare -> analyze -> apply (22, 25)."""

    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(..., ge=1)
    accuracy: AccuracyReport
    pushed_counts: dict[str, int] = Field(default_factory=dict)
    push_results: list[BlockPushResult] = Field(default_factory=list)
    mismatches: list[Mismatch] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    applied_count: int = Field(0, ge=0)
    stop_reason: Optional[LoopStopReason] = None


# ---------------------------------------------------------------------------
# Backend protocol (implemented by the client and the simulator)
# ---------------------------------------------------------------------------
@runtime_checkable
class ExecutionBackend(Protocol):
    """A synchronous backend that accepts pushed blocks and returns results.

    Both :class:`~accuracy_loop.client.ExecutionApiClient` (real HTTP) and
    :class:`~accuracy_loop.simulator.ExecutionSimulator` (in-process) implement
    this, so the loop controller is backend-agnostic and fully testable offline.
    """

    def push(self, operations: list[PushOperation]) -> list[BlockPushResult]:
        """Send every operation and return one result per operation."""
        ...


__all__ = [
    "AccuracyLoopContext",
    "AccuracyReport",
    "BlockPushResult",
    "ConfigBlock",
    "ExecutionApiConfig",
    "ExecutionBackend",
    "IterationRecord",
    "LoopStopReason",
    "Mismatch",
    "MismatchKind",
    "OutputCellDiff",
    "OutputComparisonReport",
    "PushOperation",
    "RowRejection",
    "Suggestion",
    "SuggestionAction",
]
