# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation (Sprint 6).
# The Transformation Intermediate Representation (tasks 1-6).
#
# The IR is the deterministic bridge between Deliverable 5's "how does the input
# become the expected output" inventory (transformation_analysis.
# TransformationAnalysisResult) and the exact ``transformation-rules`` (20-key)
# and ``dataset-transformation-rules`` (11-key) JSON the existing API consumes.
# It is a pure, serializable data structure carrying every supported
# transformation, its exact contract field + operation keyword + parameters, its
# source/target column UUID references, and its ordering - and it records,
# separately, every requirement REJECTED because the API cannot represent it
# (never invented into JSON). The IR -> JSON compiler (compiler.py) reads only
# this structure, guaranteeing a reproducible, auditable generation step.
# ============================================================================
"""
transformation_ir.models
=========================
The Transformation Intermediate Representation (Sprint 6 tasks 1-6).

Model hierarchy::

    TransformationIR                                # task 1 - top-level IR
      ├── context: TransformationGenerationContext  # identity/audit defaults
      ├── columns: [ColumnTransformationIR]         # one per emittable target column
      │     ├── target: TargetRef                   # task 4 - target uuid/name/seq
      │     ├── sources: [SourceRef]                # task 3 - source uuid(s)
      │     └── rules: [TransformationRuleIR]        # tasks 2, 5, 18 - per operation
      ├── dataset_rules: [DatasetTransformationIR]  # task 13 - dataset-level fields
      └── rejected: [RejectedTransformation]         # task 10 - unsupported, flagged

Design contract
---------------
* **Deterministic** - the IR is built by a rule-based builder (no LLM); the same
  analysis always yields the same IR.
* **Faithful** - every :class:`TransformationRuleIR` names the exact contract
  field it populates and the exact operation keyword (casing/spelling preserved,
  including ``Case_Conversion`` and the ``aggrigation`` typo) taken from
  :mod:`transformation_analysis.catalog`. Nothing is invented.
* **Auditable** - unsupported requirements are captured in
  :class:`RejectedTransformation` rather than silently dropped or forced to JSON.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contract.enums import ContractDataType
from transformation_analysis.enums import (
    SupportedTransformation,
    TransformationCategory,
    TransformationSignal,
)


def _new_uuid() -> str:
    """Generate a fresh hex UUID for IR-entity identifiers."""
    return uuid4().hex


# The literal ``targetFileName`` sentinel used across the corpus when a target
# column stays in the natural (single) output file rather than a named dataset.
NULL_TARGET_FILE = "<null>"


# ---------------------------------------------------------------------------
# Generation context (identity triple + audit)
# ---------------------------------------------------------------------------
class TransformationGenerationContext(BaseModel):
    """Identity / audit values stamped onto every generated transformation row.

    In a real configuration these come from the parent config's identity triple
    (``bu``/``platform``/``dataCategory``) and the authenticated user. When the
    transformation block is generated standalone (Deliverable 6 in isolation)
    these defaults make the output self-consistent and contract-valid. Callers
    may override any field (e.g. the orchestrator injects the real identity
    triple).

    Every field maps 1:1 onto a required ``transformation-rules`` /
    ``dataset-transformation-rules`` key so the compiler can stamp them without
    transformation (Sprint 6 tasks 12, 14).
    """

    model_config = ConfigDict(extra="forbid")

    bu: str = Field("DVTS", description="Business unit (transformation-rules.bu)")
    platform: str = Field(
        "GENERIC", description="Destination platform (transformation-rules.platform)"
    )
    data_category: str = Field(
        "GENERATED", description="Business use-case (transformation-rules.dataCategory)"
    )
    useremail: str = Field(
        "dvts@adp.com", description="Audit owner email (transformation-rules.useremail)"
    )
    userrole: str = Field(
        "DVTS_USER", description="Audit owner role (transformation-rules.userrole)"
    )


# ---------------------------------------------------------------------------
# Source / target references (Sprint 6 tasks 3-4)
# ---------------------------------------------------------------------------
class SourceRef(BaseModel):
    """Stable reference to a source (input) column (Sprint 6 task 3).

    ``column_uuid`` is the canonical column UUID that becomes
    ``transformation-rules.sourceColumnName`` (a UUID despite the ``...Name``
    suffix) and is referenced inside transformation operation payloads
    (``columns`` / ``field`` / ``{{uuid}}``). ``column_name`` is audit-only.
    """

    model_config = ConfigDict(extra="forbid")

    column_uuid: str = Field(..., description="Canonical source column UUID (-> sourceColumnName)")
    column_name: str = Field(..., description="Source column name (audit/readability)")
    sheet_name: str = Field("", description="Owning source sheet name (audit)")
    file_name: str = Field("", description="Owning source file name (audit)")


class TargetRef(BaseModel):
    """Stable reference to a target (output) column (Sprint 6 tasks 4, 16, 19).

    ``column_uuid`` is the canonical output column UUID (audit / block-validation
    join key); ``column_name`` becomes ``transformation-rules.targetColumnName``;
    ``position`` becomes ``columnSequence`` when the output ordering is pinned.
    """

    model_config = ConfigDict(extra="forbid")

    column_uuid: str = Field(..., description="Canonical target column UUID (audit)")
    column_name: str = Field(..., description="Target column NAME (-> targetColumnName)")
    position: Optional[int] = Field(
        None, description="Output ordering (-> columnSequence); null => natural order"
    )
    sheet_name: str = Field("", description="Owning target sheet name (audit)")
    file_name: str = Field("", description="Owning target file name (audit)")


# ---------------------------------------------------------------------------
# Individual transformation operation in the IR (Sprint 6 tasks 2, 5, 18)
# ---------------------------------------------------------------------------
class TransformationRuleIR(BaseModel):
    """One supported transformation operation, ready to compile to JSON.

    Each operation is anchored to exactly one :class:`SupportedTransformation`
    kind and carries:

    * ``contract_field`` - the exact ``transformation-rules`` key it populates
      (``sourceColumnName`` / ``targetColumnName`` / ``dataTransformationRules``
      / ``defaultValue`` / ``specialCharToBeRemoved`` / ``lovCheckType`` / ``rule``;
      Sprint 6 task 8).
    * ``config_op`` - the exact operation keyword inside a
      ``dataTransformationRules[].configurations`` object (e.g. ``"date_format"``,
      ``"Case_Conversion"``) or a ``defaultValue`` family key, when applicable.
    * ``keyword`` - a pinned fixed token when the contract requires one
      (e.g. ``"cnt"`` for aggregation), preserved verbatim (Sprint 6 task 9).
    * ``parameters`` - the concrete operation values captured during Sprint 5
      analysis (Sprint 6 tasks 5, 17).
    * ``order`` - position in the per-column transform sequence (Sprint 6
      task 18); mapping/rename ops use 0.
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(default_factory=_new_uuid, description="Stable IR operation id")
    kind: SupportedTransformation = Field(..., description="Supported transformation kind")
    contract_field: str = Field(
        ..., description="Exact transformation-rules key this op populates (task 8)"
    )
    config_op: Optional[str] = Field(
        None, description="Exact configurations/defaultValue op keyword (when applicable)"
    )
    keyword: Optional[str] = Field(
        None, description="Exact pinned API keyword when the contract fixes one (task 9)"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Concrete operation values (tasks 5, 17)"
    )
    order: int = Field(
        0, ge=0, description="Position in the per-column transform sequence (task 18)"
    )

    # -- provenance (carried through from Sprint 5 for auditing) -----------
    signal: TransformationSignal = Field(
        TransformationSignal.RELATIONSHIP, description="Signal that produced the op"
    )
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Deterministic-heuristic confidence"
    )
    rationale: str = Field("", description="Why this op was inferred (audit trail)")


# ---------------------------------------------------------------------------
# Per-column IR (all operations for one target column)
# ---------------------------------------------------------------------------
class ColumnTransformationIR(BaseModel):
    """Every supported transformation for a single target (output) column.

    The compiler collapses this into exactly ONE ``transformation-rules`` object
    (the corpus stores one row per target column, even a pass-through). The
    primary ``source`` (first of ``sources``) provides ``sourceColumnName``;
    additional sources feed multi-column ops (concat/arithmetic).
    """

    model_config = ConfigDict(extra="forbid")

    target: TargetRef = Field(..., description="Target/output column (task 4, 19)")
    sources: list[SourceRef] = Field(
        default_factory=list, description="Source/input column(s) feeding it (tasks 3, 15)"
    )
    category: TransformationCategory = Field(
        TransformationCategory.DIRECT, description="Overall relationship (audit)"
    )
    data_type: Optional[ContractDataType] = Field(
        None, description="Target contract dataType (-> dataType; null => inherit)"
    )
    data_format: Optional[str] = Field(
        None, description="Target format mask/spec (-> dataFormat) when pinned"
    )
    target_file_name: str = Field(
        NULL_TARGET_FILE, description="targetFileName sentinel / dataSetId / JSON spec"
    )
    transformation_required: bool = Field(
        False, description="Whether any value transform applies (-> transformationRequired)"
    )
    rules: list[TransformationRuleIR] = Field(
        default_factory=list, description="Supported transformation operations, ordered"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Prerequisite column UUIDs (-> dependsOn)"
    )

    @property
    def primary_source(self) -> Optional[SourceRef]:
        """The first source column (provides ``sourceColumnName``), or ``None``."""
        return self.sources[0] if self.sources else None

    def rule_kinds(self) -> set[SupportedTransformation]:
        """Return the set of transformation kinds present on this column."""
        return {r.kind for r in self.rules}

    def find_rule(self, kind: SupportedTransformation) -> Optional[TransformationRuleIR]:
        """Return the first rule of ``kind`` on this column, or ``None``."""
        return next((r for r in self.rules if r.kind is kind), None)

    def ops_for_field(self, contract_field: str) -> list[TransformationRuleIR]:
        """Return this column's ops targeting ``contract_field``, in transform order."""
        return sorted(
            (r for r in self.rules if r.contract_field == contract_field),
            key=lambda r: r.order,
        )

    def value_transforms(self) -> list[TransformationRuleIR]:
        """Return the ops that change the value (exclude pure mapping/rename)."""
        return [
            r
            for r in self.rules
            if r.kind
            not in (
                SupportedTransformation.DIRECT_MAPPING,
                SupportedTransformation.RENAME,
            )
        ]


# ---------------------------------------------------------------------------
# Dataset-level derived field IR (Sprint 6 task 13)
# ---------------------------------------------------------------------------
class DatasetTransformationIR(BaseModel):
    """A dataset-level derived field (aggregation / date stamp; task 13).

    Maps onto a ``dataset-transformation-rules`` object. Rare in the corpus but
    fully representable so the compiler can emit it when the analysis proposes
    one (it is never invented).
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(default_factory=_new_uuid, description="Stable IR id")
    field_name: str = Field(..., description="Human name of the derived field (-> fieldName)")
    field_uuid: str = Field(
        default_factory=_new_uuid, description="Stable id of the new field (-> fieldUUID)"
    )
    dataset_uuid: str = Field(
        "", description="Target dataset uuid (-> dataSetId)"
    )
    kind: SupportedTransformation = Field(..., description="Dataset transformation kind")
    contract_field: str = Field("rule", description="Always 'rule' for dataset fields")
    config_op: Optional[str] = Field(
        None, description="'aggrigation' | 'date_operations'"
    )
    keyword: Optional[str] = Field(None, description="Pinned op keyword (e.g. 'cnt')")
    field_type: Optional[str] = Field(
        "TRANSFORMATION", description="'TRANSFORMATION' or null (-> fieldType)"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Rule parameters (field/operation/type/format)"
    )
    rationale: str = Field("", description="Why this dataset field was inferred")


# ---------------------------------------------------------------------------
# Rejected transformation (Sprint 6 task 10)
# ---------------------------------------------------------------------------
class RejectedTransformation(BaseModel):
    """An unsupported/underivable transformation, captured (never invented)."""

    model_config = ConfigDict(extra="forbid")

    target_column_uuid: str = Field(..., description="Target column UUID")
    column_name: str = Field(..., description="Target column name")
    kind: SupportedTransformation = Field(..., description="Attempted transformation kind")
    reason: str = Field(..., description="Why it cannot be represented in JSON")


# ---------------------------------------------------------------------------
# Top-level IR (Sprint 6 task 1)
# ---------------------------------------------------------------------------
class TransformationIR(BaseModel):
    """The complete Transformation Intermediate Representation (task 1)."""

    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = Field(None, description="Owning session identifier")
    context: TransformationGenerationContext = Field(
        default_factory=TransformationGenerationContext,
        description="Identity/audit values stamped on every row",
    )
    columns: list[ColumnTransformationIR] = Field(
        default_factory=list, description="Per-target-column transformation IR"
    )
    dataset_rules: list[DatasetTransformationIR] = Field(
        default_factory=list, description="Dataset-level derived fields (task 13)"
    )
    rejected: list[RejectedTransformation] = Field(
        default_factory=list, description="Unsupported requirements, flagged (task 10)"
    )

    def emittable_columns(self) -> list[ColumnTransformationIR]:
        """Columns that will produce a ``transformation-rules`` row (>=1 rule)."""
        return [c for c in self.columns if c.rules]

    def rule_count(self) -> int:
        """Total number of transformation operations across all columns."""
        return sum(len(c.rules) for c in self.columns)

    def rejected_count(self) -> int:
        """Number of rejected (unsupported) requirements."""
        return len(self.rejected)

    def find_column(self, target_uuid: str) -> Optional[ColumnTransformationIR]:
        """Return the IR for a given target-column UUID, or ``None``."""
        return next(
            (c for c in self.columns if c.target.column_uuid == target_uuid), None
        )


__all__ = [
    "NULL_TARGET_FILE",
    "TransformationGenerationContext",
    "SourceRef",
    "TargetRef",
    "TransformationRuleIR",
    "ColumnTransformationIR",
    "DatasetTransformationIR",
    "RejectedTransformation",
    "TransformationIR",
]
