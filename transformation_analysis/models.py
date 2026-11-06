# ============================================================================
# Deliverable 5 - Transformation Rule Identification models (tasks 17-20).
# REUSABLE. Pydantic v2 models for the Transformation Rule Inventory and the
# Transformation Analysis Output. These are the DELIVERABLE of Sprint 5: they
# describe WHICH supported transformations turn the known input into the known
# output - they do NOT execute anything (execution stays with the existing
# Java/API engine). The inventory carries source/target column UUIDs, per-rule
# parameters, and ordering so Sprint 6 can compile it into the exact
# transformation JSON.
# ============================================================================
"""
transformation_analysis.models
==============================
Models for transformation rule identification.

Model hierarchy::

    TransformationAnalysisResult                  # task 20 - top-level output
      ├── sheet_mappings: [SheetTransformationMapping]  # tasks 3
      ├── inventory: TransformationRuleInventory        # task 20
      │     ├── columns: [ColumnTransformationProfile]  # tasks 4-13,17-19
      │     │     └── requirements: [TransformationRequirement]  # tasks 5-16
      │     └── dataset_rules: [DatasetTransformationRequirement]
      ├── added_columns / removed_columns              # tasks 4 (added/removed)
      ├── unsupported: [UnsupportedTransformation]     # task 16
      └── summary: TransformationAnalysisSummary       # task 20
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from contract.enums import ContractDataType
from transformation_analysis.enums import (
    SupportedTransformation,
    SupportStatus,
    TransformationCategory,
    TransformationSignal,
)


def _new_uuid() -> str:
    """Generate a fresh hex UUID for analysis-entity identifiers."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Column reference (source/target linkage, tasks 10-11, 18-19)
# ---------------------------------------------------------------------------
class ColumnRef(BaseModel):
    """A UUID-anchored reference to a source or target column (tasks 18-19).

    ``column_uuid`` is the canonical column UUID (the deterministic join key
    that becomes ``transformation-rules.sourceColumnName`` for a source, and the
    target column the rule produces). Names/positions are carried for
    readability and for ``targetColumnName`` generation.
    """

    column_uuid: str = Field(..., description="Canonical column UUID (stable reference)")
    column_name: str = Field(..., description="Column name")
    position: int = Field(0, ge=0, description="Zero-based column position")
    sheet_name: str = Field("", description="Owning sheet name")
    file_name: str = Field("", description="Owning file name")


# ---------------------------------------------------------------------------
# Individual transformation requirement (Sprint 5 tasks 5-16)
# ---------------------------------------------------------------------------
class TransformationRequirement(BaseModel):
    """A single identified transformation for a target column.

    One requirement = one operation the analyzer inferred is needed to turn the
    source value into the target value (e.g. "reformat date yyyyMMdd ->
    dd/MM/yyyy", "UpperCase", "pad to length 6"). It records the supported-API
    classification (tasks 15-16), the exact contract field/operation it will
    populate, its parameters (task 12), its position in the per-column transform
    order (task 13), and a deterministic confidence + rationale.
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable requirement id")
    kind: SupportedTransformation = Field(..., description="Transformation identified")
    status: SupportStatus = Field(
        SupportStatus.SUPPORTED, description="Supported / unsupported (tasks 15-16)"
    )
    signal: TransformationSignal = Field(
        ..., description="Comparison signal that produced this requirement"
    )

    contract_field: str = Field(
        ..., description="Exact transformation-rules key this requirement populates"
    )
    config_op: Optional[str] = Field(
        None, description="Exact configurations operation keyword (when applicable)"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Operation parameters (Sprint 5 task 12)"
    )
    order: int = Field(
        0, ge=0, description="Position in the per-column transform sequence (task 13)"
    )

    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Deterministic-heuristic confidence"
    )
    rationale: str = Field(
        "", description="Why this transformation was inferred (audit trail)"
    )
    unsupported_reason: Optional[str] = Field(
        None, description="Populated only when status is UNSUPPORTED (task 16)"
    )


# ---------------------------------------------------------------------------
# Per-column transformation profile (Sprint 5 tasks 4-13, 17-19)
# ---------------------------------------------------------------------------
class ColumnTransformationProfile(BaseModel):
    """All identified transformations for a single target (output) column.

    Anchored to the target column UUID and its source column UUID(s) so every
    requirement is deterministically tied to the columns it maps between
    (tasks 10-11, 18-19). ``category`` classifies the overall relationship
    (direct/renamed/derived/transformed/multi-transform; tasks 5-9).
    """

    target_column: ColumnRef = Field(..., description="Target/output column (task 19)")
    source_columns: list[ColumnRef] = Field(
        default_factory=list, description="Source/input column(s) feeding it (tasks 10, 18)"
    )
    category: TransformationCategory = Field(
        ..., description="Overall relationship category (tasks 5-9)"
    )

    source_data_type: Optional[ContractDataType] = Field(
        None, description="Resolved source contract data type"
    )
    target_data_type: Optional[ContractDataType] = Field(
        None, description="Resolved target contract data type"
    )
    transformation_required: bool = Field(
        False, description="Whether any value transform is needed (transformationRequired)"
    )

    requirements: list[TransformationRequirement] = Field(
        default_factory=list, description="Identified transformations for this column"
    )

    def supported_requirements(self) -> list[TransformationRequirement]:
        """Return only the supported requirements."""
        return [r for r in self.requirements if r.status is SupportStatus.SUPPORTED]

    def unsupported_requirements(self) -> list[TransformationRequirement]:
        """Return only the unsupported requirements."""
        return [r for r in self.requirements if r.status is SupportStatus.UNSUPPORTED]

    def value_transforms(self) -> list[TransformationRequirement]:
        """Return requirements that change the value (exclude pure mapping/rename)."""
        return [
            r
            for r in self.requirements
            if r.kind
            not in (
                SupportedTransformation.DIRECT_MAPPING,
                SupportedTransformation.RENAME,
            )
        ]


# ---------------------------------------------------------------------------
# Dataset-level derived-field requirement (contract file 3)
# ---------------------------------------------------------------------------
class DatasetTransformationRequirement(BaseModel):
    """A dataset-level derived field (aggregation / date stamp; file 3).

    Rare in the corpus (14 rows / 692 files). Modeled for completeness so the
    Sprint 6 compiler can emit ``dataset-transformation-rules`` objects, but the
    deterministic analyzer only proposes one when a clear dataset-level signal
    exists (it never invents derived fields).
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable requirement id")
    field_name: str = Field(..., description="Human name of the new derived field")
    field_uuid: str = Field(default_factory=_new_uuid, description="Stable id of the new field")
    kind: SupportedTransformation = Field(..., description="Dataset transformation kind")
    dataset_uuid: Optional[str] = Field(
        None, description="Target dataset (sheet/file) UUID the field is added to"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Rule parameters (aggrigation/date_operations)"
    )
    rationale: str = Field("", description="Why this dataset field was inferred")


# ---------------------------------------------------------------------------
# Sheet-level mapping (Sprint 5 task 3)
# ---------------------------------------------------------------------------
class SheetTransformationMapping(BaseModel):
    """Which output sheet is produced from which input sheet(s) (task 3)."""

    target_sheet_uuid: str = Field(..., description="Output sheet UUID")
    target_sheet_name: str = Field(..., description="Output sheet name")
    target_file_name: str = Field("", description="Output file name")
    source_sheet_uuids: list[str] = Field(
        default_factory=list, description="Source input sheet UUID(s)"
    )
    source_sheet_names: list[str] = Field(
        default_factory=list, description="Source input sheet name(s)"
    )
    derivation_type: Optional[str] = Field(
        None, description="Sheet derivation classification (single_source/join/...)"
    )


# ---------------------------------------------------------------------------
# Added / removed column summaries (Sprint 5 task 4 context)
# ---------------------------------------------------------------------------
class AddedColumn(BaseModel):
    """An output column with no matching input column (derived candidate)."""

    target_column: ColumnRef
    reason: str = Field(..., description="Why it is considered added/derived")


class RemovedColumn(BaseModel):
    """An input column not present in the output (dropped; no transform row)."""

    source_column: ColumnRef
    reason: str = Field(..., description="Why it is considered removed")


# ---------------------------------------------------------------------------
# Transformation rule inventory (Sprint 5 task 20)
# ---------------------------------------------------------------------------
class TransformationRuleInventory(BaseModel):
    """Structured inventory of every identified transformation (task 20).

    Grouped by target-column profile; each profile carries its source/target
    UUIDs, supported/unsupported requirements, parameters, and ordering ready
    for Sprint 6 IR generation. Dataset-level derived fields (rare) live in
    ``dataset_rules``.
    """

    columns: list[ColumnTransformationProfile] = Field(
        default_factory=list, description="Per-target-column transformation profiles"
    )
    dataset_rules: list[DatasetTransformationRequirement] = Field(
        default_factory=list, description="Dataset-level derived fields (file 3)"
    )

    def total_requirements(self) -> int:
        """Total number of identified transformations across all columns."""
        return sum(len(c.requirements) for c in self.columns)

    def find_target(self, target_column_uuid: str) -> Optional[ColumnTransformationProfile]:
        """Return the profile for a given target-column UUID, or ``None``."""
        return next(
            (c for c in self.columns if c.target_column.column_uuid == target_column_uuid),
            None,
        )


# ---------------------------------------------------------------------------
# Analysis summary + top-level output (Sprint 5 task 20)
# ---------------------------------------------------------------------------
class TransformationAnalysisSummary(BaseModel):
    """Roll-up counts for the transformation analysis (task 20)."""

    input_file_count: int = Field(0, ge=0)
    output_file_count: int = Field(0, ge=0)
    sheet_mapping_count: int = Field(0, ge=0)
    target_column_count: int = Field(0, ge=0)
    requirement_count: int = Field(0, ge=0)
    supported_count: int = Field(0, ge=0)
    unsupported_count: int = Field(0, ge=0)
    direct_count: int = Field(0, ge=0)
    renamed_count: int = Field(0, ge=0)
    derived_count: int = Field(0, ge=0)
    transformed_count: int = Field(0, ge=0)
    multi_transform_count: int = Field(0, ge=0)
    added_column_count: int = Field(0, ge=0)
    removed_column_count: int = Field(0, ge=0)
    requirements_by_kind: dict[str, int] = Field(
        default_factory=dict, description="Count of requirements per kind"
    )


class UnsupportedTransformation(BaseModel):
    """A flagged unsupported transformation surfaced at the top level (task 16)."""

    target_column_uuid: str
    target_column_name: str
    kind: SupportedTransformation
    reason: str


class TransformationAnalysisResult(BaseModel):
    """Top-level transformation analysis output (Sprint 5 task 20).

    The complete, serializable result of running transformation rule
    identification over a canonical analysis model: sheet mappings, the
    transformation rule inventory, added/removed columns, the flagged
    unsupported transformations, and a summary.
    """

    session_id: Optional[str] = Field(None, description="Owning session identifier")
    canonical_model_uuid: Optional[str] = Field(
        None, description="Optional reference to the source canonical model"
    )

    sheet_mappings: list[SheetTransformationMapping] = Field(
        default_factory=list, description="Output-from-input sheet mappings (task 3)"
    )
    inventory: TransformationRuleInventory = Field(
        default_factory=TransformationRuleInventory,
        description="Transformation rule inventory (task 20)",
    )
    added_columns: list[AddedColumn] = Field(
        default_factory=list, description="Output columns with no input source (task 4)"
    )
    removed_columns: list[RemovedColumn] = Field(
        default_factory=list, description="Input columns absent from the output"
    )
    unsupported: list[UnsupportedTransformation] = Field(
        default_factory=list, description="Flagged unsupported transformations (task 16)"
    )
    summary: TransformationAnalysisSummary = Field(
        default_factory=TransformationAnalysisSummary, description="Roll-up counts (task 20)"
    )


__all__ = [
    "ColumnRef",
    "TransformationRequirement",
    "ColumnTransformationProfile",
    "DatasetTransformationRequirement",
    "SheetTransformationMapping",
    "AddedColumn",
    "RemovedColumn",
    "TransformationRuleInventory",
    "TransformationAnalysisSummary",
    "UnsupportedTransformation",
    "TransformationAnalysisResult",
]
