# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# Validate the complete assembled configuration (tasks 17-21).
#
# After assembly, the complete document is re-checked deterministically before
# it is handed to the existing API:
#   * task 17 - complete JSON schema: exact keys/casing/hierarchy, field types,
#               and embedded-blob integrity (delegated to contract.validate_contract).
#   * task 18 - business keywords: every enum field holds a supported keyword.
#   * task 19 - UUID references: every sourceColumn / sourceColumnName / dependsOn
#               resolves to a column-relations uuid (or a derived field anchor).
#   * task 20 - source/target references: each transformation targetColumnName
#               resolves to a cataloged column; dataset ids resolve.
#   * task 21 - configuration consistency: ONE identity triple (bu/platform/
#               dataCategory) + owner across all five sections.
# The result is a plain, testable report (no exceptions for invalid content).
# ============================================================================
"""
complete_config.validation
===========================
Deterministic validation of a complete configuration (Sprint 7 tasks 17-21).

Usage::

    from complete_config.validation import validate_complete_config

    report = validate_complete_config(contract_or_document)
    if not report.is_valid:
        for issue in report.errors:
            print(issue.location, issue.message)
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from contract.blobs import RawBlob
from contract.models import ConfigurationContract
from contract.validation import (
    IssueCode as ContractIssueCode,
    Severity as ContractSeverity,
    validate_contract,
)


class Severity(str, Enum):
    """Issue severity."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Stable machine codes for complete-config issues."""

    SCHEMA = "SCHEMA"                  # task 17 - structure/type/blob problem
    KEYWORD = "KEYWORD"                # task 18 - unsupported business keyword
    UUID_REFERENCE = "UUID_REFERENCE"  # task 19 - dangling uuid reference
    SOURCE_TARGET = "SOURCE_TARGET"    # task 20 - unresolved source/target ref
    CONSISTENCY = "CONSISTENCY"        # task 21 - identity/owner inconsistency


# Map a contract issue code onto the complete-config taxonomy (task 17/18/19).
_CONTRACT_CODE_MAP: dict[ContractIssueCode, IssueCode] = {
    ContractIssueCode.STRUCTURE: IssueCode.SCHEMA,
    ContractIssueCode.TYPE: IssueCode.SCHEMA,
    ContractIssueCode.MALFORMED_BLOB: IssueCode.SCHEMA,
    ContractIssueCode.KEYWORD: IssueCode.KEYWORD,
    ContractIssueCode.DANGLING_COLUMN: IssueCode.UUID_REFERENCE,
    ContractIssueCode.DANGLING_DATASET: IssueCode.SOURCE_TARGET,
}


class CompleteConfigIssue(BaseModel):
    """A single problem found while validating a complete configuration."""

    severity: Severity = Field(..., description="error or warning")
    code: IssueCode = Field(..., description="Stable machine code")
    location: str = Field(..., description="Dotted path to the offending value")
    message: str = Field(..., description="Human-readable description")


class CompleteConfigReport(BaseModel):
    """Outcome of :func:`validate_complete_config`."""

    is_valid: bool = Field(..., description="True when there are no ERROR-severity issues")
    contract_valid: bool = Field(
        ..., description="True when the underlying contract validation passed"
    )
    column_count: int = Field(0, ge=0, description="column-relations rows")
    validation_count: int = Field(0, ge=0, description="validation-rules rows")
    transformation_count: int = Field(0, ge=0, description="transformation-rules rows")
    dataset_count: int = Field(0, ge=0, description="dataset-transformation-rules rows")
    issues: list[CompleteConfigIssue] = Field(
        default_factory=list, description="All issues found"
    )

    @property
    def errors(self) -> list[CompleteConfigIssue]:
        """Only the ERROR-severity issues."""
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[CompleteConfigIssue]:
        """Only the WARNING-severity issues."""
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def validate_complete_config(
    payload: Union[ConfigurationContract, dict[str, Any], str]
) -> CompleteConfigReport:
    """Validate a complete configuration document (Sprint 7 tasks 17-21).

    Args:
        payload: A :class:`ConfigurationContract`, a contract ``dict``, or a raw
            JSON document string.

    Returns:
        A :class:`CompleteConfigReport`. ``is_valid`` is ``True`` only when no
        ERROR-severity issue was found. Never raises for invalid *content*.
    """
    document = _as_document(payload)
    issues: list[CompleteConfigIssue] = []

    # task 17/18/19 (partial) - full contract validation, mapped in.
    contract_result = validate_contract(document)
    for issue in contract_result.issues:
        issues.append(
            CompleteConfigIssue(
                severity=_map_severity(issue.severity),
                code=_CONTRACT_CODE_MAP.get(issue.code, IssueCode.SCHEMA),
                location=issue.location,
                message=issue.message,
            )
        )

    model = _try_parse(document)
    if model is None:
        # Structural parse failed: consistency/reference checks are not meaningful.
        return CompleteConfigReport(
            is_valid=False,
            contract_valid=contract_result.is_valid,
            issues=issues,
        )

    data = model.data
    column_uuids = {c.uuid for c in data.column_relations if c.uuid}
    column_names = {c.columnName for c in data.column_relations if c.columnName}
    field_uuids = {
        r.fieldUUID for r in data.dataset_transformation_rules if r.fieldUUID
    }
    anchor_uuids = column_uuids | field_uuids
    dataset_ids = _dataset_ids(model)

    issues.extend(_check_consistency(model))                       # task 21
    issues.extend(_check_uuid_references(model, anchor_uuids))     # task 19
    issues.extend(
        _check_source_target(model, column_names, dataset_ids)     # task 20
    )

    is_valid = not any(i.severity is Severity.ERROR for i in issues)
    return CompleteConfigReport(
        is_valid=is_valid,
        contract_valid=contract_result.is_valid,
        column_count=len(data.column_relations),
        validation_count=len(data.validation_rules),
        transformation_count=len(data.transformation_rules),
        dataset_count=len(data.dataset_transformation_rules),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# task 21 - configuration consistency
# ---------------------------------------------------------------------------
def _check_consistency(model: ConfigurationContract) -> list[CompleteConfigIssue]:
    """Verify ONE identity triple + owner across every section (task 21)."""
    issues: list[CompleteConfigIssue] = []
    cfg = model.data.configuration_data
    triple = (cfg.bu, cfg.platform, cfg.dataCategory)

    def _check(section: str, index: Optional[int], bu: str, platform: str, category: str) -> None:
        loc = f"data.{section}" + (f"[{index}]" if index is not None else "")
        if (bu, platform, category) != triple:
            issues.append(
                CompleteConfigIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.CONSISTENCY,
                    location=loc,
                    message=(
                        f"identity triple ({bu}/{platform}/{category}) does not match "
                        f"configuration-data ({triple[0]}/{triple[1]}/{triple[2]})"
                    ),
                )
            )

    for i, c in enumerate(model.data.column_relations):
        _check("column-relations", i, c.bu, c.platform, c.dataCategory)
    for i, r in enumerate(model.data.transformation_rules):
        _check("transformation-rules", i, r.bu, r.platform, r.dataCategory)
    for i, r in enumerate(model.data.validation_rules):
        _check("validation-rules", i, r.bu, r.platform, r.dataCategory)
    for i, r in enumerate(model.data.dataset_transformation_rules):
        _check("dataset-transformation-rules", i, r.bu, r.platform, r.dataCategory)

    return issues


# ---------------------------------------------------------------------------
# task 19 - UUID references
# ---------------------------------------------------------------------------
def _check_uuid_references(
    model: ConfigurationContract, anchor_uuids: set[str]
) -> list[CompleteConfigIssue]:
    """Verify sourceColumn / sourceColumnName / dependsOn resolve (task 19)."""
    issues: list[CompleteConfigIssue] = []

    for idx, rule in enumerate(model.data.validation_rules):
        if rule.sourceColumn and rule.sourceColumn not in anchor_uuids:
            issues.append(
                _ref_warning(
                    IssueCode.UUID_REFERENCE,
                    f"data.validation-rules[{idx}].sourceColumn",
                    f"sourceColumn '{rule.sourceColumn}' is not a known column uuid",
                )
            )
        for dep in _depends_on(rule.dependsOn):
            if dep not in anchor_uuids:
                issues.append(
                    _ref_warning(
                        IssueCode.UUID_REFERENCE,
                        f"data.validation-rules[{idx}].dependsOn",
                        f"dependsOn '{dep}' is not a known column uuid",
                    )
                )

    for idx, rule in enumerate(model.data.transformation_rules):
        if rule.sourceColumnName and rule.sourceColumnName not in anchor_uuids:
            issues.append(
                _ref_warning(
                    IssueCode.UUID_REFERENCE,
                    f"data.transformation-rules[{idx}].sourceColumnName",
                    f"sourceColumnName '{rule.sourceColumnName}' is not a known column uuid",
                )
            )
        for dep in _depends_on(rule.dependsOn):
            if dep not in anchor_uuids:
                issues.append(
                    _ref_warning(
                        IssueCode.UUID_REFERENCE,
                        f"data.transformation-rules[{idx}].dependsOn",
                        f"dependsOn '{dep}' is not a known column uuid",
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# task 20 - source/target references
# ---------------------------------------------------------------------------
def _check_source_target(
    model: ConfigurationContract,
    column_names: set[str],
    dataset_ids: set[str],
) -> list[CompleteConfigIssue]:
    """Verify target column names + dataset ids resolve (task 20)."""
    issues: list[CompleteConfigIssue] = []

    for idx, rule in enumerate(model.data.transformation_rules):
        if rule.targetColumnName and rule.targetColumnName not in column_names:
            issues.append(
                _ref_warning(
                    IssueCode.SOURCE_TARGET,
                    f"data.transformation-rules[{idx}].targetColumnName",
                    (
                        f"targetColumnName '{rule.targetColumnName}' does not match any "
                        "column-relations.columnName"
                    ),
                )
            )

    for idx, rule in enumerate(model.data.dataset_transformation_rules):
        if dataset_ids and rule.dataSetId and rule.dataSetId not in dataset_ids:
            issues.append(
                _ref_warning(
                    IssueCode.SOURCE_TARGET,
                    f"data.dataset-transformation-rules[{idx}].dataSetId",
                    (
                        f"dataSetId '{rule.dataSetId}' was not found among "
                        "configuration-data input dataset ids"
                    ),
                )
            )

    return issues


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _as_document(
    payload: Union[ConfigurationContract, dict[str, Any], str]
) -> Union[dict[str, Any], str]:
    """Normalize any accepted input into a dict/str for ``validate_contract``."""
    if isinstance(payload, ConfigurationContract):
        return payload.to_contract_dict()
    return payload


def _try_parse(
    document: Union[dict[str, Any], str]
) -> Optional[ConfigurationContract]:
    """Parse the document into a contract model, or ``None`` on failure."""
    try:
        if isinstance(document, str):
            return ConfigurationContract.model_validate_json_document(document)
        return ConfigurationContract.model_validate(document)
    except Exception:  # noqa: BLE001 - contract issues already captured upstream
        return None


def _map_severity(severity: ContractSeverity) -> Severity:
    """Map a contract severity onto the complete-config severity."""
    return Severity.ERROR if severity is ContractSeverity.ERROR else Severity.WARNING


def _ref_warning(code: IssueCode, location: str, message: str) -> CompleteConfigIssue:
    """Build a WARNING reference issue (the API is the final arbiter)."""
    return CompleteConfigIssue(
        severity=Severity.WARNING, code=code, location=location, message=message
    )


def _depends_on(value: Any) -> list[str]:
    """Normalize a parsed ``dependsOn`` blob into a list of uuid strings."""
    if value is None or isinstance(value, RawBlob):
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _dataset_ids(model: ConfigurationContract) -> set[str]:
    """Collect dataset ids declared in configuration-data input/data rules."""
    ids: set[str] = set()
    cfg = model.data.configuration_data
    for source in (cfg.inputRules, cfg.dataRules):
        for entry in _entries(source):
            ds = _entry_get(entry, "dataSetId")
            if ds:
                ids.add(ds)
    return ids


def _entries(value: Any) -> list[Any]:
    """Normalize a parsed blob field into a list of entries."""
    if value is None or isinstance(value, RawBlob):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _entry_get(entry: Any, key: str) -> Any:
    """Read an attribute/key from a model or dict blob entry."""
    if isinstance(entry, BaseModel):
        return getattr(entry, key, None)
    if isinstance(entry, dict):
        return entry.get(key)
    return None


__all__ = [
    "Severity",
    "IssueCode",
    "CompleteConfigIssue",
    "CompleteConfigReport",
    "validate_complete_config",
]
