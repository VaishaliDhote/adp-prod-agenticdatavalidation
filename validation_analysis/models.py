# ============================================================================
# Deliverable 3 - Validation Rule Identification models (tasks 22-25). REUSABLE.
# Pydantic v2 models for the Validation Rule Inventory and Validation Analysis
# Output. These are the DELIVERABLE of Sprint 3: they describe WHICH supported
# validations a future input file must satisfy - they do NOT enforce anything
# (enforcement stays with the existing Java/API engine). The inventory carries
# source-column UUIDs and per-rule parameters so Sprint 4 can compile it into
# the exact validation JSON.
# ============================================================================
"""
validation_analysis.models
===========================
Models for validation rule identification.

Model hierarchy::

    ValidationAnalysisResult                 # task 25 - top-level output
      ├── expected_structures: [ExpectedStructure]   # tasks 3-5 (per sheet)
      ├── inventory: ValidationRuleInventory         # task 22
      │     └── columns: [ColumnValidationProfile]   # per validated column
      │           └── requirements: [ValidationRequirement]  # tasks 6-21, 23
      └── summary: AnalysisSummary                    # counts / support split
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from contract.enums import ContractDataType
from validation_analysis.enums import (
    RequirementSource,
    SupportedValidation,
    SupportStatus,
)


def _new_uuid() -> str:
    """Generate a fresh hex UUID for analysis-entity identifiers."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Expected input structure (Sprint 3 tasks 2-5)
# ---------------------------------------------------------------------------
class ExpectedColumn(BaseModel):
    """Expected column contract for a future input file (tasks 3, 5)."""

    name: str = Field(..., description="Expected column name")
    position: int = Field(..., ge=0, description="Expected zero-based order")
    source_column_uuid: str = Field(
        ..., description="Canonical column UUID this expectation derives from (task 24)"
    )


class ExpectedStructure(BaseModel):
    """Expected structure of one input sheet (Sprint 3 tasks 2-5).

    Captures the column names, count, and ordering a future input file must
    reproduce, derived from the analyzed current input sheet.
    """

    file_name: str = Field(..., description="Source input file name")
    file_uuid: str = Field(..., description="Canonical file UUID")
    sheet_name: str = Field(..., description="Sheet name")
    sheet_uuid: str = Field(..., description="Canonical sheet UUID")

    expected_column_count: int = Field(
        ..., ge=0, description="Number of columns expected (task 4)"
    )
    expected_columns: list[ExpectedColumn] = Field(
        default_factory=list, description="Ordered expected columns (tasks 3, 5)"
    )

    def expected_column_names(self) -> list[str]:
        """Return expected column names in order."""
        return [c.name for c in self.expected_columns]


# ---------------------------------------------------------------------------
# Individual validation requirement (Sprint 3 tasks 6-21, 23)
# ---------------------------------------------------------------------------
class ValidationRequirement(BaseModel):
    """A single identified validation requirement for a column.

    One requirement = one constraint the analyzer inferred from the current
    input (e.g. "mandatory", "max length 40", "must be a valid Date in
    yyyy-MM-dd"). It records the supported-API classification (tasks 19-21),
    the exact contract field it will populate, its parameters (task 23), and a
    deterministic confidence + rationale for explainability.
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable requirement id")
    kind: SupportedValidation = Field(..., description="Validation kind identified")
    status: SupportStatus = Field(
        SupportStatus.SUPPORTED, description="Supported / unsupported (tasks 20-21)"
    )
    source: RequirementSource = Field(
        ..., description="Signal that produced this requirement (explainability)"
    )

    contract_field: str = Field(
        ..., description="Exact validation-rules key this requirement populates"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Rule parameters (Sprint 3 task 23)"
    )

    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Deterministic-heuristic confidence"
    )
    rationale: str = Field(
        "", description="Why this requirement was inferred (audit trail)"
    )
    unsupported_reason: Optional[str] = Field(
        None, description="Populated only when status is UNSUPPORTED (task 21)"
    )


# ---------------------------------------------------------------------------
# Per-column validation profile (Sprint 3 task 24)
# ---------------------------------------------------------------------------
class ColumnValidationProfile(BaseModel):
    """All identified validation requirements for a single source column.

    ``source_column_uuid`` is the join key back to the canonical column (and to
    the ``validation-rules.sourceColumn`` field in the generated JSON), keeping
    every requirement deterministically anchored to a column (task 24).
    """

    source_column_uuid: str = Field(
        ..., description="Canonical column UUID (validation-rules.sourceColumn)"
    )
    column_name: str = Field(..., description="Column name (for readability)")
    position: int = Field(..., ge=0, description="Zero-based column position")

    file_name: str = Field(..., description="Owning input file name")
    sheet_name: str = Field(..., description="Owning sheet name")

    data_type: Optional[ContractDataType] = Field(
        None, description="Resolved contract data type (task 10), if determinable"
    )
    is_mandatory: bool = Field(
        False, description="Whether the column was identified as mandatory (task 6)"
    )
    is_nullable: bool = Field(
        True, description="Whether the column allows empty values (task 9)"
    )

    requirements: list[ValidationRequirement] = Field(
        default_factory=list, description="Identified requirements for this column"
    )

    def supported_requirements(self) -> list[ValidationRequirement]:
        """Return only the supported requirements."""
        return [r for r in self.requirements if r.status is SupportStatus.SUPPORTED]

    def unsupported_requirements(self) -> list[ValidationRequirement]:
        """Return only the unsupported requirements."""
        return [r for r in self.requirements if r.status is SupportStatus.UNSUPPORTED]


# ---------------------------------------------------------------------------
# Validation rule inventory (Sprint 3 task 22)
# ---------------------------------------------------------------------------
class ValidationRuleInventory(BaseModel):
    """Structured inventory of every identified validation requirement (task 22).

    Grouped by column profile; each profile carries its source-column UUID,
    supported/unsupported requirements, and parameters ready for Sprint 4 IR
    generation.
    """

    columns: list[ColumnValidationProfile] = Field(
        default_factory=list, description="Per-column validation profiles"
    )

    def total_requirements(self) -> int:
        """Total number of identified requirements across all columns."""
        return sum(len(c.requirements) for c in self.columns)

    def find_column(self, source_column_uuid: str) -> Optional[ColumnValidationProfile]:
        """Return the profile for a given column UUID, or ``None``."""
        return next(
            (c for c in self.columns if c.source_column_uuid == source_column_uuid),
            None,
        )


# ---------------------------------------------------------------------------
# Analysis summary + top-level output (Sprint 3 task 25)
# ---------------------------------------------------------------------------
class AnalysisSummary(BaseModel):
    """Roll-up counts for the validation analysis (task 25)."""

    input_file_count: int = Field(0, ge=0)
    sheet_count: int = Field(0, ge=0)
    column_count: int = Field(0, ge=0)
    requirement_count: int = Field(0, ge=0)
    supported_count: int = Field(0, ge=0)
    unsupported_count: int = Field(0, ge=0)
    mandatory_column_count: int = Field(0, ge=0)
    nullable_column_count: int = Field(0, ge=0)
    requirements_by_kind: dict[str, int] = Field(
        default_factory=dict, description="Count of requirements per kind"
    )


class UnsupportedRequirement(BaseModel):
    """A flagged unsupported requirement surfaced at the top level (task 21)."""

    source_column_uuid: str
    column_name: str
    kind: SupportedValidation
    reason: str


class ValidationAnalysisResult(BaseModel):
    """Top-level validation analysis output (Sprint 3 task 25).

    The complete, serializable result of running validation rule identification
    over a canonical analysis model: expected input structure, the validation
    rule inventory, the flagged unsupported requirements, and a summary.
    """

    session_id: Optional[str] = Field(None, description="Owning session identifier")
    canonical_model_uuid: Optional[str] = Field(
        None, description="Optional reference to the source canonical model"
    )

    expected_structures: list[ExpectedStructure] = Field(
        default_factory=list, description="Expected input structure per sheet (tasks 2-5)"
    )
    inventory: ValidationRuleInventory = Field(
        default_factory=ValidationRuleInventory,
        description="Validation rule inventory (task 22)",
    )
    unsupported: list[UnsupportedRequirement] = Field(
        default_factory=list, description="Flagged unsupported requirements (task 21)"
    )
    summary: AnalysisSummary = Field(
        default_factory=AnalysisSummary, description="Roll-up counts (task 25)"
    )


__all__ = [
    "ExpectedColumn",
    "ExpectedStructure",
    "ValidationRequirement",
    "ColumnValidationProfile",
    "ValidationRuleInventory",
    "AnalysisSummary",
    "UnsupportedRequirement",
    "ValidationAnalysisResult",
]
