# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation (Sprint 4).
# The Validation Intermediate Representation (tasks 1-5).
#
# The IR is the deterministic bridge between Deliverable 3's "what must be
# validated" inventory (validation_analysis.ValidationAnalysisResult) and the
# exact ``validation-rules`` JSON the existing API consumes. It is a pure,
# serializable data structure: it carries every supported validation rule,
# its exact contract field + fixed keyword, its parameters, its source-column
# UUID reference, and any cross-rule dependencies - and it records, separately,
# every requirement that was REJECTED because the API cannot represent it
# (never invented into JSON). The IR -> JSON compiler (compiler.py) reads only
# this structure, guaranteeing a reproducible, auditable generation step.
# ============================================================================
"""
validation_ir.models
=====================
The Validation Intermediate Representation (Sprint 4 tasks 1-5).

Model hierarchy::

    ValidationIR                               # task 1 - top-level IR
      ├── context: ValidationGenerationContext # identity/audit defaults
      ├── columns: [ColumnValidationIR]        # one per emittable column
      │     ├── source_column: ColumnReference # task 3 - UUID + name
      │     └── rules: [ValidationRuleIR]       # tasks 2, 4, 5 - per rule
      └── rejected: [RejectedValidation]        # task 9 - unsupported, flagged

Design contract
---------------
* **Deterministic** - the IR is built by a rule-based builder (no LLM); the same
  analysis always yields the same IR.
* **Faithful** - every :class:`ValidationRuleIR` names the exact
  ``validation-rules`` contract field it populates and the exact fixed keyword
  (casing/spelling preserved) taken from
  :mod:`validation_analysis.catalog`. Nothing is invented.
* **Auditable** - unsupported requirements are captured in
  :class:`RejectedValidation` rather than silently dropped or forced into JSON.
"""
from __future__ import annotations

from typing import Any, ClassVar, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contract.enums import ContractDataType, ValidationRuleType
from validation_analysis.enums import (
    RequirementSource,
    SupportedValidation,
)


def _new_uuid() -> str:
    """Generate a fresh hex UUID for IR-entity identifiers."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Generation context (identity triple + audit + rule-type defaults)
# ---------------------------------------------------------------------------
class ValidationGenerationContext(BaseModel):
    """Identity / audit values stamped onto every generated validation row.

    In a real configuration these come from the parent config's identity triple
    (``bu``/``platform``/``dataCategory``) and the authenticated user. When the
    validation block is generated standalone (Deliverable 4 in isolation) these
    defaults make the output self-consistent and contract-valid. Callers may
    override any field (e.g. the orchestrator injects the real identity triple).

    Every field maps 1:1 onto a required ``validation-rules`` key so the
    compiler can stamp them without transformation (Sprint 4 tasks 11-13).
    """

    model_config = ConfigDict(extra="forbid")

    bu: str = Field("DVTS", description="Business unit (validation-rules.bu)")
    platform: str = Field("GENERIC", description="Destination platform (validation-rules.platform)")
    data_category: str = Field(
        "GENERATED", description="Business use-case (validation-rules.dataCategory)"
    )
    useremail: str = Field(
        "dvts@adp.com",
        description=(
            "Audit owner email (validation-rules.useremail). Fallback only - the "
            "route prefers the authenticated user's email from the request token."
        ),
    )
    userrole: str = Field(
        "agent_role",
        description="Audit owner role (validation-rules.userrole); static for now.",
    )
    validation_rule_type: ValidationRuleType = Field(
        ValidationRuleType.CLIENT,
        description="Origin/scope keyword (validation-rules.validationRuleType): 'client' or 'CAM'",
    )
    is_skip_validations: bool = Field(
        False,
        description="Master skip switch (validation-rules.isSkipValidaitons; always false in corpus)",
    )

    # -- identity-injection enforcement (Phase 0) --------------------------
    # The self-consistent defaults above keep standalone generation valid, but a
    # REAL config must stamp the parent config's identity triple onto every row
    # (so the rules apply to the correct BU/platform/dataCategory and match a
    # truth file). These sentinels are exactly the placeholder defaults; if any
    # survives into a "real" generation the identity was never injected.
    _PLACEHOLDER_BU: ClassVar[str] = "DVTS"
    _PLACEHOLDER_PLATFORM: ClassVar[str] = "GENERIC"
    _PLACEHOLDER_DATA_CATEGORY: ClassVar[str] = "GENERATED"

    def missing_identity_fields(self) -> list[str]:
        """Return identity-triple fields that are blank or still placeholders.

        A field counts as "not injected" when it is empty/whitespace or equals
        its standalone placeholder default. Used by :meth:`require_identity` and
        by the service's strict generation path.
        """
        missing: list[str] = []
        if not (self.bu or "").strip() or self.bu == self._PLACEHOLDER_BU:
            missing.append("bu")
        if not (self.platform or "").strip() or self.platform == self._PLACEHOLDER_PLATFORM:
            missing.append("platform")
        if (
            not (self.data_category or "").strip()
            or self.data_category == self._PLACEHOLDER_DATA_CATEGORY
        ):
            missing.append("dataCategory")
        return missing

    def has_real_identity(self) -> bool:
        """Whether the real identity triple was injected (no placeholders left)."""
        return not self.missing_identity_fields()

    def require_identity(self) -> "ValidationGenerationContext":
        """Return self if the identity triple was injected, else raise.

        Raises:
            ValueError: when ``bu`` / ``platform`` / ``dataCategory`` are blank or
                still carry their standalone placeholder defaults - meaning the
                orchestrator did not inject the parent config's real identity.
        """
        missing = self.missing_identity_fields()
        if missing:
            raise ValueError(
                "Validation generation requires an injected identity triple; "
                f"missing/placeholder fields: {', '.join(missing)}. Provide a "
                "ValidationGenerationContext with the real bu/platform/dataCategory."
            )
        return self


# ---------------------------------------------------------------------------
# Column reference (Sprint 4 task 3)
# ---------------------------------------------------------------------------
class ColumnReference(BaseModel):
    """Stable reference to a source column (Sprint 4 task 3).

    ``column_uuid`` is the canonical column UUID that becomes
    ``validation-rules.sourceColumn`` - the single deterministic join key back
    to ``column-relations`` and the transformation rows. ``column_name`` is kept
    for readability/auditing only (the contract's ``sourceColumnName`` is always
    null; the UUID is authoritative).
    """

    model_config = ConfigDict(extra="forbid")

    column_uuid: str = Field(..., description="Canonical column UUID (-> sourceColumn)")
    column_name: str = Field(..., description="Column name (audit/readability only)")


# ---------------------------------------------------------------------------
# Individual validation rule in the IR (Sprint 4 tasks 2, 4, 5)
# ---------------------------------------------------------------------------
class ValidationRuleIR(BaseModel):
    """One supported validation rule, ready to compile to JSON (tasks 2, 4, 5).

    Each rule is anchored to exactly one :class:`SupportedValidation` kind and
    carries:

    * ``contract_field`` - the exact ``validation-rules`` key it populates
      (resolved from the catalogue; Sprint 4 task 7).
    * ``keyword`` - the exact fixed API token when the contract pins one
      (e.g. ``"Y"`` for a flag, ``"Email"`` for the e-mail dataType), preserved
      verbatim (Sprint 4 task 8). ``None`` when the value is data-derived.
    * ``parameters`` - the concrete values (limits, formats, blob payloads)
      captured during Sprint 3 analysis (Sprint 4 tasks 4, 14).
    * ``depends_on`` - UUIDs of other columns this rule depends on (Sprint 4
      task 5 -> ``validation-rules.dependsOn``).
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(default_factory=_new_uuid, description="Stable IR rule id")
    kind: SupportedValidation = Field(..., description="Supported validation kind")
    contract_field: str = Field(
        ..., description="Exact validation-rules key this rule populates (task 7)"
    )
    keyword: Optional[str] = Field(
        None, description="Exact fixed API keyword when pinned (task 8); else null"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Concrete rule values (tasks 4, 14)"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Prerequisite column UUIDs (task 5)"
    )

    # -- provenance (carried through from Sprint 3 for auditing) -----------
    source: RequirementSource = Field(
        RequirementSource.STRUCTURE, description="Signal that produced the rule"
    )
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Deterministic-heuristic confidence"
    )
    rationale: str = Field("", description="Why this rule was inferred (audit trail)")


# ---------------------------------------------------------------------------
# Per-column IR (all rules for one column)
# ---------------------------------------------------------------------------
class ColumnValidationIR(BaseModel):
    """Every supported validation rule for a single source column.

    The compiler collapses this into exactly ONE ``validation-rules`` object
    (the corpus stores ~one row per validated column). ``data_type`` is the
    baseline contract type for the column; the compiler resolves the final
    emitted ``dataType`` from the rules using a fixed precedence.
    """

    model_config = ConfigDict(extra="forbid")

    source_column: ColumnReference = Field(..., description="Column UUID + name (task 3)")
    data_type: Optional[ContractDataType] = Field(
        None, description="Baseline contract dataType for the column (task 10)"
    )
    file_name: str = Field("", description="Owning input file name (audit)")
    sheet_name: str = Field("", description="Owning sheet name (audit)")
    rules: list[ValidationRuleIR] = Field(
        default_factory=list, description="Supported validation rules for the column"
    )

    def rule_kinds(self) -> set[SupportedValidation]:
        """Return the set of validation kinds present on this column."""
        return {r.kind for r in self.rules}

    def find_rule(self, kind: SupportedValidation) -> Optional[ValidationRuleIR]:
        """Return the first rule of ``kind`` on this column, or ``None``."""
        return next((r for r in self.rules if r.kind is kind), None)

    def dependency_uuids(self) -> list[str]:
        """Return the de-duplicated, order-preserving union of rule dependencies."""
        seen: dict[str, None] = {}
        for rule in self.rules:
            for dep in rule.depends_on:
                if dep and dep not in seen:
                    seen[dep] = None
        return list(seen.keys())


# ---------------------------------------------------------------------------
# Rejected (unsupported) validation (Sprint 4 task 9)
# ---------------------------------------------------------------------------
class RejectedValidation(BaseModel):
    """An identified requirement that was NOT converted to JSON (task 9).

    Deliverable 4 must never invent a rule the existing API cannot represent.
    Any requirement flagged unsupported in Sprint 3 - or that cannot be faithfully
    mapped here - is captured explicitly for human review instead of being
    forced into the validation block.
    """

    model_config = ConfigDict(extra="forbid")

    source_column_uuid: str = Field(..., description="Column the rule applied to")
    column_name: str = Field(..., description="Column name (readability)")
    kind: SupportedValidation = Field(..., description="Validation kind that was rejected")
    reason: str = Field(..., description="Why it was rejected (no faithful API mapping)")


# ---------------------------------------------------------------------------
# Top-level Validation IR (Sprint 4 task 1)
# ---------------------------------------------------------------------------
class ValidationIR(BaseModel):
    """The complete Validation IR for one analyzed session (Sprint 4 task 1).

    Produced by :class:`validation_ir.builder.ValidationIRBuilder` from a
    Deliverable 3 :class:`~validation_analysis.models.ValidationAnalysisResult`,
    and consumed by :class:`validation_ir.compiler.ValidationRuleCompiler` to
    emit the exact ``validation-rules`` JSON array.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = Field(None, description="Owning session identifier")
    context: ValidationGenerationContext = Field(
        default_factory=ValidationGenerationContext,
        description="Identity/audit values stamped on every row",
    )
    columns: list[ColumnValidationIR] = Field(
        default_factory=list, description="Per-column validation rules (emittable)"
    )
    rejected: list[RejectedValidation] = Field(
        default_factory=list, description="Flagged unsupported requirements (task 9)"
    )

    # -- convenience roll-ups ----------------------------------------------
    def rule_count(self) -> int:
        """Total number of supported validation rules across all columns."""
        return sum(len(c.rules) for c in self.columns)

    def emittable_columns(self) -> list[ColumnValidationIR]:
        """Columns that will produce a ``validation-rules`` row (have >=1 rule)."""
        return [c for c in self.columns if c.rules]

    def rejected_count(self) -> int:
        """Number of rejected (unsupported) requirements."""
        return len(self.rejected)

    def find_column(self, column_uuid: str) -> Optional[ColumnValidationIR]:
        """Return the column IR for a given UUID, or ``None``."""
        return next(
            (c for c in self.columns if c.source_column.column_uuid == column_uuid),
            None,
        )


__all__ = [
    "ValidationGenerationContext",
    "ColumnReference",
    "ValidationRuleIR",
    "ColumnValidationIR",
    "RejectedValidation",
    "ValidationIR",
]
