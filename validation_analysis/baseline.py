# ============================================================================
# Phase 0 - Keyword-keyed Baseline Rule Store (the "truth file" guarantee).
# REUSABLE.
#
# The input file is the source of truth: every validation present in it must be
# present in FUTURE input files for the same scope. To enforce that (instead of
# re-inferring rules from whatever file is passed), we persist the truth file's
# generated validation rules keyed by the STABLE ``column_key``
# (validation_analysis.keyword_registry), not by the per-ingestion UUID. On a
# later file we RE-APPLY the baseline onto that file's columns (resolving
# key -> current UUID) and VERIFY the file's own analysis still covers every
# baseline rule, reporting any dropped validations.
#
# This module is persistence-agnostic and offline-testable: the in-memory
# :class:`ValidationBaseline` model plus the pure :func:`apply_baseline` /
# :func:`verify_against_baseline` functions carry all the logic; a thin
# JSON-file persister (:class:`JsonBaselineRepository`) is provided for local
# use and can later be swapped for the DynamoDB-backed knowledge.rules_store.
# ============================================================================
"""
validation_analysis.baseline
=============================
Persist and re-apply a truth file's validations, keyed by stable column key.

Public surface
--------------
* :class:`BaselineRule` / :class:`BaselineColumn` / :class:`ValidationBaseline`
  - the serializable baseline captured from a truth file.
* :func:`baseline_from_ir` - derive a baseline from a built Validation IR.
* :func:`apply_baseline` - resolve baseline rules onto a new file's columns.
* :func:`verify_against_baseline` - report baseline rules a new file dropped.
* :class:`JsonBaselineRepository` - local JSON persistence (offline/testable).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from validation_analysis.keyword_registry import (
    KeywordRegistry,
    normalize_column_key,
)

if TYPE_CHECKING:  # avoid import cycles / heavy imports at runtime
    from validation_ir.models import ValidationIR


# ---------------------------------------------------------------------------
# Baseline data model (plain dataclasses -> trivially JSON-serializable)
# ---------------------------------------------------------------------------
@dataclass
class BaselineRule:
    """One validation rule captured from the truth file.

    Stored by validation KIND plus the concrete contract fields it wrote, so it
    can be both re-applied (replayed onto a new column) and verified (checked
    for presence) without re-running analysis.
    """

    kind: str
    contract_field: str
    # The exact contract field -> value pairs this rule contributed (e.g.
    # ``{"isMandatory": "Y"}`` or ``{"maxLengthAllowed": "12"}``).
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "contract_field": self.contract_field, "fields": self.fields}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaselineRule":
        return cls(
            kind=data["kind"],
            contract_field=data.get("contract_field", ""),
            fields=dict(data.get("fields", {})),
        )


@dataclass
class BaselineColumn:
    """All baseline rules for one column concept, keyed by stable ``column_key``."""

    column_key: str
    column_name: str
    data_type: Optional[str] = None
    rules: list[BaselineRule] = field(default_factory=list)

    def rule_kinds(self) -> set[str]:
        return {r.kind for r in self.rules}

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_key": self.column_key,
            "column_name": self.column_name,
            "data_type": self.data_type,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaselineColumn":
        return cls(
            column_key=data["column_key"],
            column_name=data.get("column_name", ""),
            data_type=data.get("data_type"),
            rules=[BaselineRule.from_dict(r) for r in data.get("rules", [])],
        )


@dataclass
class ValidationBaseline:
    """A truth file's validations, scoped by identity triple and keyed by concept."""

    bu: str
    platform: str
    data_category: str
    session_id: Optional[str] = None
    columns: dict[str, BaselineColumn] = field(default_factory=dict)

    def scope_key(self) -> str:
        """Stable scope identifier (BU/platform/dataCategory)."""
        return f"{self.bu}#{self.platform}#{self.data_category}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bu": self.bu,
            "platform": self.platform,
            "data_category": self.data_category,
            "session_id": self.session_id,
            "columns": {k: c.to_dict() for k, c in self.columns.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValidationBaseline":
        return cls(
            bu=data["bu"],
            platform=data["platform"],
            data_category=data["data_category"],
            session_id=data.get("session_id"),
            columns={
                k: BaselineColumn.from_dict(v) for k, v in data.get("columns", {}).items()
            },
        )


# ---------------------------------------------------------------------------
# Derive a baseline from a built Validation IR
# ---------------------------------------------------------------------------
def baseline_from_ir(
    ir: "ValidationIR",
    *,
    registry: Optional[KeywordRegistry] = None,
) -> ValidationBaseline:
    """Capture a :class:`ValidationBaseline` from a compiled truth-file IR.

    Each emittable column becomes a :class:`BaselineColumn` keyed by the stable
    ``column_key`` derived from its name; each of its IR rules becomes a
    :class:`BaselineRule` recording the kind, contract field, and the concrete
    field/value pairs captured in the rule's parameters.
    """
    reg = registry
    baseline = ValidationBaseline(
        bu=ir.context.bu,
        platform=ir.context.platform,
        data_category=ir.context.data_category,
        session_id=ir.session_id,
    )
    for column in ir.emittable_columns():
        name = column.source_column.column_name
        key = reg.resolve_key(name) if reg else normalize_column_key(name)
        if not key:
            continue
        bcol = BaselineColumn(
            column_key=key,
            column_name=name,
            data_type=column.data_type.value if column.data_type else None,
        )
        for rule in column.rules:
            bcol.rules.append(
                BaselineRule(
                    kind=rule.kind.value,
                    contract_field=rule.contract_field,
                    fields=_rule_fields(rule),
                )
            )
        baseline.columns[key] = bcol
    return baseline


def _rule_fields(rule: Any) -> dict[str, Any]:
    """Extract the concrete contract field/value pairs a rule contributes.

    Prefers an explicit ``{contract_field: keyword}`` when the rule pins a fixed
    keyword; otherwise surfaces the rule's parameters verbatim (limits/formats).
    """
    if getattr(rule, "keyword", None) is not None and getattr(rule, "contract_field", ""):
        return {rule.contract_field: rule.keyword}
    return dict(getattr(rule, "parameters", {}) or {})


# ---------------------------------------------------------------------------
# Apply + verify
# ---------------------------------------------------------------------------
@dataclass
class AppliedRule:
    """A baseline rule resolved onto a concrete column in the new file."""

    column_key: str
    column_uuid: str
    column_name: str
    kind: str
    contract_field: str
    fields: dict[str, Any]


@dataclass
class BaselineApplication:
    """Result of replaying a baseline onto a new file's columns."""

    applied: list[AppliedRule] = field(default_factory=list)
    # baseline column keys with no matching column in the new file.
    unresolved_keys: list[str] = field(default_factory=list)

    def applied_count(self) -> int:
        return len(self.applied)


def apply_baseline(
    baseline: ValidationBaseline,
    key_to_uuid: dict[str, str],
    *,
    key_to_name: Optional[dict[str, str]] = None,
) -> BaselineApplication:
    """Replay a baseline onto a new file, resolving ``column_key -> uuid``.

    Args:
        baseline: The truth-file baseline.
        key_to_uuid: This file's stable-key -> UUID index
            (see :func:`validation_analysis.keyword_registry.build_key_to_uuid_index`).
        key_to_name: Optional stable-key -> column name for nicer reporting.

    Returns:
        A :class:`BaselineApplication` with one :class:`AppliedRule` per baseline
        rule whose column concept exists in the new file, plus the list of
        baseline keys that could not be resolved (concept missing from the file).
    """
    names = key_to_name or {}
    result = BaselineApplication()
    for key, bcol in baseline.columns.items():
        uuid = key_to_uuid.get(key)
        if uuid is None:
            result.unresolved_keys.append(key)
            continue
        for rule in bcol.rules:
            result.applied.append(
                AppliedRule(
                    column_key=key,
                    column_uuid=uuid,
                    column_name=names.get(key, bcol.column_name),
                    kind=rule.kind,
                    contract_field=rule.contract_field,
                    fields=dict(rule.fields),
                )
            )
    return result


@dataclass
class BaselineGap:
    """A baseline rule the new file's own analysis failed to reproduce."""

    column_key: str
    column_name: str
    kind: str
    reason: str


@dataclass
class VerificationReport:
    """Coverage of a new file's rules against the truth-file baseline."""

    gaps: list[BaselineGap] = field(default_factory=list)
    unresolved_keys: list[str] = field(default_factory=list)

    @property
    def is_covered(self) -> bool:
        """True when the new file reproduced every resolvable baseline rule."""
        return not self.gaps and not self.unresolved_keys

    def gap_count(self) -> int:
        return len(self.gaps)


def verify_against_baseline(
    baseline: ValidationBaseline,
    produced_by_key: dict[str, set[str]],
    *,
    key_to_name: Optional[dict[str, str]] = None,
) -> VerificationReport:
    """Report every baseline validation a new file dropped.

    Args:
        baseline: The truth-file baseline.
        produced_by_key: ``column_key -> {kind, ...}`` the NEW file's own
            analysis produced (kinds as :class:`SupportedValidation` ``.value``).
        key_to_name: Optional stable-key -> column name for reporting.

    Returns:
        A :class:`VerificationReport`: a :class:`BaselineGap` for each baseline
        rule kind missing from the new file's output, plus any baseline column
        concept entirely absent from the new file.
    """
    names = key_to_name or {}
    report = VerificationReport()
    for key, bcol in baseline.columns.items():
        produced = produced_by_key.get(key)
        if produced is None:
            report.unresolved_keys.append(key)
            continue
        for kind in bcol.rule_kinds():
            if kind not in produced:
                report.gaps.append(
                    BaselineGap(
                        column_key=key,
                        column_name=names.get(key, bcol.column_name),
                        kind=kind,
                        reason=(
                            f"Baseline requires validation '{kind}' on column "
                            f"'{bcol.column_name}', but the new file's analysis did "
                            f"not produce it."
                        ),
                    )
                )
    return report


# ---------------------------------------------------------------------------
# Local JSON persistence (offline / testable; swap for rules_store in prod)
# ---------------------------------------------------------------------------
class JsonBaselineRepository:
    """Persist/fetch baselines as JSON files, scoped by identity triple.

    One file per scope under ``root_dir``; the filename is a filesystem-safe
    rendering of the scope key. This mirrors the DynamoDB rules_store contract
    (save/fetch by BU/platform/category) but runs with no cloud dependency.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)

    def _path(self, bu: str, platform: str, data_category: str) -> Path:
        safe = normalize_column_key(f"{bu}_{platform}_{data_category}") or "default"
        return self._root / f"baseline_{safe}.json"

    def save(self, baseline: ValidationBaseline) -> Path:
        """Persist a baseline, overwriting any existing one for the same scope."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(baseline.bu, baseline.platform, baseline.data_category)
        path.write_text(json.dumps(baseline.to_dict(), indent=2), encoding="utf-8")
        return path

    def fetch(
        self, bu: str, platform: str, data_category: str
    ) -> Optional[ValidationBaseline]:
        """Load the baseline for a scope, or ``None`` when none is stored."""
        path = self._path(bu, platform, data_category)
        if not path.exists():
            return None
        return ValidationBaseline.from_dict(json.loads(path.read_text(encoding="utf-8")))


__all__ = [
    "BaselineRule",
    "BaselineColumn",
    "ValidationBaseline",
    "baseline_from_ir",
    "AppliedRule",
    "BaselineApplication",
    "apply_baseline",
    "BaselineGap",
    "VerificationReport",
    "verify_against_baseline",
    "JsonBaselineRepository",
]
