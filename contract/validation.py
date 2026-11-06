# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure (task 23).
# Deterministic contract validation.
#
# Detects, BEFORE the configuration is sent to the existing API:
#   * structural problems - missing/extra keys, wrong casing (extra="forbid"),
#     wrong JSON hierarchy;
#   * data-type problems  - a field holding the wrong JSON type;
#   * keyword problems    - an enum field (dataType, validationRuleType, ...)
#     holding a value outside the exact supported vocabulary;
#   * malformed embedded JSON-string blobs;
#   * cross-reference problems - a rule that points at a column uuid / dataset id
#     that does not exist in the document (reported as WARNINGS, since the
#     existing API is the final arbiter and approved configs legitimately carry
#     derived-column references that are not in the column catalog).
#
# Structural/type/enum checks are delegated to the Pydantic v2 models
# (:mod:`contract.models`); cross-reference and blob-integrity checks are
# implemented here on top of the parsed model. The result is a plain, testable
# report object (no exceptions raised for invalid input).
# ============================================================================
"""
contract.validation
====================
Deterministic validation of a configuration document against the exact JSON
contract.

Usage::

    from contract.validation import validate_contract

    result = validate_contract(raw_dict_or_json_string)
    if not result.is_valid:
        for issue in result.errors:
            print(issue.location, issue.message)
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from contract.blobs import RawBlob
from contract.models import ConfigurationContract

# Fields (per section) whose value is an embedded JSON string. Used for the
# raw-level "is this valid JSON?" check.
_BLOB_FIELDS: dict[str, tuple[str, ...]] = {
    "column-relations": ("dataExclusionRules",),
    "configuration-data": (
        "dataRules",
        "filesInfo",
        "inputRules",
        "outputFileRules",
        "outputRules",
    ),
    "dataset-transformation-rules": ("rule",),
    "transformation-rules": (
        "defaultValue",
        "dataTransformationRules",
        "specialCharToBeRemoved",
        "dependsOn",
    ),
    "validation-rules": (
        "conditionalValidationRule",
        "dataExclusionRules",
        "dataTransformationRules",
        "dependsOn",
        "specialCharNotAllowed",
        "specialCharToBeRemoved",
        "stringCheckRule",
    ),
}

# Blob strings that are legitimately NOT JSON and must not be flagged malformed.
_NON_JSON_SENTINELS = {"", "<null>"}

# Matches an embedded ``{{<uuid>}}`` column reference inside conditional/default
# blobs (lhs/rhs references).
_UUID_REF_RE = re.compile(r"\{\{\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*\}\}")


class Severity(str, Enum):
    """Severity of a contract issue."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Stable machine codes for contract issues."""

    STRUCTURE = "STRUCTURE"          # missing/extra key, wrong hierarchy
    TYPE = "TYPE"                    # wrong JSON data type
    KEYWORD = "KEYWORD"             # value outside the supported vocabulary
    MALFORMED_BLOB = "MALFORMED_BLOB"  # embedded JSON string does not parse
    DANGLING_COLUMN = "DANGLING_COLUMN"  # reference to a missing column uuid
    DANGLING_DATASET = "DANGLING_DATASET"  # reference to a missing dataset id


class ContractIssue(BaseModel):
    """A single problem found while validating a configuration document."""

    severity: Severity = Field(..., description="error or warning")
    code: IssueCode = Field(..., description="Stable machine code")
    location: str = Field(..., description="Dotted path to the offending value")
    message: str = Field(..., description="Human-readable description")


class ContractValidationResult(BaseModel):
    """Outcome of :func:`validate_contract`."""

    is_valid: bool = Field(..., description="True when there are no ERROR-severity issues")
    issues: list[ContractIssue] = Field(default_factory=list, description="All issues found")

    @property
    def errors(self) -> list[ContractIssue]:
        """Only the ERROR-severity issues."""

        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ContractIssue]:
        """Only the WARNING-severity issues."""

        return [i for i in self.issues if i.severity is Severity.WARNING]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_contract(payload: dict[str, Any] | str) -> ContractValidationResult:
    """Validate a configuration document against the exact JSON contract.

    Args:
        payload: The configuration as a ``dict`` or a raw JSON document string.

    Returns:
        A :class:`ContractValidationResult`. ``is_valid`` is ``True`` only when
        no ERROR-severity issues were found. This function never raises for
        invalid *content* (it may raise ``json.JSONDecodeError`` only if a
        ``str`` payload is not JSON at all).
    """

    raw = json.loads(payload) if isinstance(payload, str) else payload
    issues: list[ContractIssue] = []

    # 1) Structure / type / keyword checks via the Pydantic models.
    try:
        model = ConfigurationContract.model_validate(raw)
    except ValidationError as exc:
        issues.extend(_map_pydantic_errors(exc))
        # Structural parse failed: cross-reference checks are not meaningful.
        return ContractValidationResult(is_valid=False, issues=issues)

    # 2) Embedded-blob JSON integrity (operates on the raw document).
    if isinstance(raw, dict):
        issues.extend(_check_blob_json(raw))

    # 3) Cross-reference resolution (operates on the parsed model).
    issues.extend(_check_cross_references(model))

    is_valid = not any(i.severity is Severity.ERROR for i in issues)
    return ContractValidationResult(is_valid=is_valid, issues=issues)


# ---------------------------------------------------------------------------
# 1) Pydantic error mapping
# ---------------------------------------------------------------------------


def _map_pydantic_errors(exc: ValidationError) -> list[ContractIssue]:
    """Translate Pydantic validation errors into contract issues."""

    issues: list[ContractIssue] = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"])
        etype = err["type"]
        if etype in {"missing", "extra_forbidden"}:
            code = IssueCode.STRUCTURE
        elif etype == "enum" or etype.endswith("_enum"):
            code = IssueCode.KEYWORD
        else:
            code = IssueCode.TYPE
        issues.append(
            ContractIssue(
                severity=Severity.ERROR,
                code=code,
                location=location or "<root>",
                message=err["msg"],
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 2) Blob JSON integrity
# ---------------------------------------------------------------------------


def _check_blob_json(raw: dict[str, Any]) -> list[ContractIssue]:
    """Flag embedded JSON-string fields whose content does not parse as JSON."""

    issues: list[ContractIssue] = []
    data = raw.get("data")
    if not isinstance(data, dict):
        return issues

    for section, fields in _BLOB_FIELDS.items():
        node = data.get(section)
        rows = node if isinstance(node, list) else [node]
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            prefix = f"data.{section}[{idx}]" if isinstance(node, list) else f"data.{section}"
            for field in fields:
                value = row.get(field)
                if not isinstance(value, str):
                    continue
                if value.strip() in _NON_JSON_SENTINELS:
                    continue
                try:
                    json.loads(value)
                except (json.JSONDecodeError, ValueError) as err:
                    issues.append(
                        ContractIssue(
                            severity=Severity.ERROR,
                            code=IssueCode.MALFORMED_BLOB,
                            location=f"{prefix}.{field}",
                            message=f"Embedded JSON-string is not valid JSON: {err}",
                        )
                    )
    return issues


# ---------------------------------------------------------------------------
# 3) Cross-reference resolution
# ---------------------------------------------------------------------------


def _as_entries(value: Any) -> list[Any]:
    """Normalize a parsed blob field into a list of entries (models/dicts)."""

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


def _check_cross_references(model: ConfigurationContract) -> list[ContractIssue]:
    """Verify that every uuid/dataset reference resolves within the document."""

    issues: list[ContractIssue] = []
    data = model.data

    # Anchor sets ---------------------------------------------------------
    column_uuids = {c.uuid for c in data.column_relations if c.uuid}
    field_uuids = {r.fieldUUID for r in data.dataset_transformation_rules if r.fieldUUID}
    anchor_uuids = column_uuids | field_uuids

    dataset_ids: set[str] = set()
    for entry in _as_entries(data.configuration_data.inputRules):
        ds = _entry_get(entry, "dataSetId")
        if ds:
            dataset_ids.add(ds)
    for entry in _as_entries(data.configuration_data.dataRules):
        ds = _entry_get(entry, "dataSetId")
        if ds:
            dataset_ids.add(ds)

    # transformation-rules.sourceColumnName -> column uuid ----------------
    # Cross-reference mismatches are WARNINGS, not hard errors: real approved
    # configs legitimately contain derived/generated columns whose source uuid
    # is not in the column catalog, and the existing API is the final arbiter.
    for idx, rule in enumerate(data.transformation_rules):
        if rule.sourceColumnName and rule.sourceColumnName not in column_uuids:
            issues.append(
                ContractIssue(
                    severity=Severity.WARNING,
                    code=IssueCode.DANGLING_COLUMN,
                    location=f"data.transformation-rules[{idx}].sourceColumnName",
                    message=(
                        f"sourceColumnName '{rule.sourceColumnName}' does not match any "
                        "column-relations.uuid"
                    ),
                )
            )

    # validation-rules.sourceColumn -> column uuid -----------------------
    for idx, rule in enumerate(data.validation_rules):
        if rule.sourceColumn and rule.sourceColumn not in column_uuids:
            issues.append(
                ContractIssue(
                    severity=Severity.WARNING,
                    code=IssueCode.DANGLING_COLUMN,
                    location=f"data.validation-rules[{idx}].sourceColumn",
                    message=(
                        f"sourceColumn '{rule.sourceColumn}' does not match any "
                        "column-relations.uuid"
                    ),
                )
            )

    # dataset-transformation-rules -> dataset id + aggregated column ------
    for idx, rule in enumerate(data.dataset_transformation_rules):
        if dataset_ids and rule.dataSetId and rule.dataSetId not in dataset_ids:
            issues.append(
                ContractIssue(
                    severity=Severity.WARNING,
                    code=IssueCode.DANGLING_DATASET,
                    location=f"data.dataset-transformation-rules[{idx}].dataSetId",
                    message=(
                        f"dataSetId '{rule.dataSetId}' was not found among "
                        "configuration-data input/data dataset ids"
                    ),
                )
            )
        rule_blob = rule.rule
        agg = _entry_get(rule_blob, "aggrigation")
        agg_field = _entry_get(agg, "field") if agg is not None else None
        if agg_field and agg_field not in anchor_uuids:
            issues.append(
                ContractIssue(
                    severity=Severity.WARNING,
                    code=IssueCode.DANGLING_COLUMN,
                    location=f"data.dataset-transformation-rules[{idx}].rule.aggrigation.field",
                    message=f"aggrigation.field '{agg_field}' does not match any column uuid",
                )
            )

    # {{uuid}} references inside conditional/default/stringCheck blobs -----
    issues.extend(_check_embedded_uuid_refs(model, anchor_uuids))

    return issues


def _check_embedded_uuid_refs(
    model: ConfigurationContract, anchor_uuids: set[str]
) -> list[ContractIssue]:
    """Warn when a ``{{uuid}}`` reference does not resolve to a known anchor."""

    issues: list[ContractIssue] = []
    data = model.data

    def _scan(container_json: Optional[str], location: str) -> None:
        if not container_json:
            return
        for ref in _UUID_REF_RE.findall(container_json):
            if ref not in anchor_uuids:
                issues.append(
                    ContractIssue(
                        severity=Severity.WARNING,
                        code=IssueCode.DANGLING_COLUMN,
                        location=location,
                        message=f"embedded reference '{{{{{ref}}}}}' does not match any column uuid",
                    )
                )

    for idx, rule in enumerate(data.transformation_rules):
        dumped = rule.model_dump(by_alias=True)
        _scan(dumped.get("defaultValue"), f"data.transformation-rules[{idx}].defaultValue")
    for idx, rule in enumerate(data.validation_rules):
        dumped = rule.model_dump(by_alias=True)
        _scan(
            dumped.get("conditionalValidationRule"),
            f"data.validation-rules[{idx}].conditionalValidationRule",
        )
        _scan(dumped.get("stringCheckRule"), f"data.validation-rules[{idx}].stringCheckRule")

    return issues


__all__ = [
    "Severity",
    "IssueCode",
    "ContractIssue",
    "ContractValidationResult",
    "validate_contract",
]
