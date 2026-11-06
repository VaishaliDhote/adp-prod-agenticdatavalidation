# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation (Sprint 6).
# Validate the generated transformation block (tasks 20-22).
#
# Before the generated ``transformation-rules`` / ``dataset-transformation-rules``
# arrays are handed to Deliverable 7 (full-config assembly) they are re-checked
# deterministically:
#   * task 21 - exact JSON keys: every row has EXACTLY the contract keys, in
#               order, with exact spelling (including the ``aggrigation`` typo).
#   * task 22 - exact keyword values: dataType / op keywords / Y-N flags hold
#               only values from the supported vocabulary.
#   * (UUID)  - source/target/dataSetId UUID references resolve to known columns.
#   * task 20 - the block round-trips through the exact contract models.
# The result is a plain, testable report (no exceptions for invalid content).
# ============================================================================
"""
transformation_ir.validation
=============================
Deterministic validation of generated transformation blocks (tasks 20-22).

Usage::

    from transformation_ir.validation import validate_transformation_block

    report = validate_transformation_block(rows, dataset_rows, known_column_uuids=uuids)
    if not report.is_valid:
        for issue in report.errors:
            print(issue.location, issue.message)
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from contract.enums import (
    ArithmeticOperator,
    CaseConversion,
    ContractDataType,
    TransformationOp,
)
from contract.models import DatasetTransformationRule, TransformationRule

# The exact keys of a transformation-rules object, in contract (declaration)
# order (the 20 authored keys). Kept explicit so any drift in the model surfaces
# as a task-21 failure.
_TRANSFORM_EXACT_KEYS: tuple[str, ...] = (
    "id",
    "bu",
    "platform",
    "dataCategory",
    "subDataCategory",
    "sourceColumnName",
    "dataType",
    "dataFormat",
    "targetColumnName",
    "targetFileName",
    "columnSequence",
    "defaultValue",
    "dataTransformationRules",
    "specialCharToBeRemoved",
    "lovCheckType",
    "transformationRequired",
    "lovValidationRequired",
    "dependsOn",
    "useremail",
    "userrole",
)

# The exact keys of a dataset-transformation-rules object (11 keys, in order).
_DATASET_EXACT_KEYS: tuple[str, ...] = (
    "id",
    "bu",
    "platform",
    "dataCategory",
    "dataSetId",
    "fieldName",
    "fieldType",
    "fieldUUID",
    "rule",
    "useremail",
    "userrole",
)

# Y/N flag fields whose value, when present, must be exactly "Y" or "N".
_YES_NO_FIELDS: tuple[str, ...] = (
    "transformationRequired",
    "lovValidationRequired",
)

# Accept BOTH the dashed v4 form used in the sample corpus and the compact
# 32-hex form the Deliverable 1 canonical model emits (uuid4().hex).
_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{32})$"
)

# Exact operation keywords allowed inside dataTransformationRules configurations.
_CONFIG_OP_KEYWORDS: frozenset[str] = frozenset(op.value for op in TransformationOp)


class Severity(str, Enum):
    """Issue severity."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Stable machine codes for transformation-block issues."""

    KEYS = "KEYS"          # missing/extra key (task 21)
    KEYWORD = "KEYWORD"    # value outside supported vocabulary (task 22)
    TYPE = "TYPE"          # wrong JSON type / model parse failure (task 20)
    UUID = "UUID"          # malformed or dangling UUID reference


class BlockIssue(BaseModel):
    """A single problem found while validating a generated transformation block."""

    severity: Severity = Field(..., description="error or warning")
    code: IssueCode = Field(..., description="Stable machine code")
    location: str = Field(..., description="Dotted path to the offending value")
    message: str = Field(..., description="Human-readable description")


class TransformationBlockReport(BaseModel):
    """Outcome of :func:`validate_transformation_block`."""

    is_valid: bool = Field(..., description="True when there are no ERROR-severity issues")
    row_count: int = Field(0, ge=0, description="transformation-rules rows checked")
    dataset_row_count: int = Field(0, ge=0, description="dataset-transformation-rules rows checked")
    issues: list[BlockIssue] = Field(default_factory=list, description="All issues found")

    @property
    def errors(self) -> list[BlockIssue]:
        """Only the ERROR-severity issues."""
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[BlockIssue]:
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
def validate_transformation_block(
    rows: list[dict[str, Any]],
    dataset_rows: Optional[list[dict[str, Any]]] = None,
    known_column_uuids: Optional[set[str]] = None,
) -> TransformationBlockReport:
    """Validate generated transformation blocks (tasks 20-22).

    Args:
        rows: The generated ``transformation-rules`` array (list of dicts).
        dataset_rows: The generated ``dataset-transformation-rules`` array.
        known_column_uuids: Optional set of valid column UUIDs. When provided,
            every ``sourceColumnName`` / ``dependsOn`` reference is checked to
            exist; dangling references become ERRORs. When ``None`` only the UUID
            *format* is checked.

    Returns:
        A :class:`TransformationBlockReport`. ``is_valid`` is ``True`` only when
        no ERROR-severity issues were found. Never raises for invalid content.
    """
    issues: list[BlockIssue] = []
    dataset_rows = dataset_rows or []

    for index, row in enumerate(rows):
        loc = f"transformation-rules[{index}]"
        issues.extend(_check_exact_keys(row, loc, _TRANSFORM_EXACT_KEYS, "transformation-rules"))
        issues.extend(_check_transform_roundtrip(row, loc))       # task 20
        issues.extend(_check_transform_keywords(row, loc))        # task 22
        issues.extend(_check_transform_uuids(row, loc, known_column_uuids))

    for index, row in enumerate(dataset_rows):
        loc = f"dataset-transformation-rules[{index}]"
        issues.extend(_check_exact_keys(row, loc, _DATASET_EXACT_KEYS, "dataset-transformation-rules"))
        issues.extend(_check_dataset_roundtrip(row, loc))         # task 20
        issues.extend(_check_dataset_rule_blob(row, loc, known_column_uuids))

    is_valid = not any(i.severity is Severity.ERROR for i in issues)
    return TransformationBlockReport(
        is_valid=is_valid,
        row_count=len(rows),
        dataset_row_count=len(dataset_rows),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# task 21 - exact JSON keys
# ---------------------------------------------------------------------------
def _check_exact_keys(
    row: dict[str, Any], loc: str, expected_keys: tuple[str, ...], contract_name: str
) -> list[BlockIssue]:
    """Verify the row has EXACTLY the contract keys, correctly spelled."""
    issues: list[BlockIssue] = []
    keys = set(row.keys())
    expected = set(expected_keys)

    for missing in sorted(expected - keys):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.KEYS,
                location=f"{loc}.{missing}",
                message=f"Missing required key '{missing}'.",
            )
        )
    for extra in sorted(keys - expected):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.KEYS,
                location=f"{loc}.{extra}",
                message=f"Unexpected key '{extra}' (not part of the {contract_name} contract).",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# task 20 - round-trip through the exact contract models
# ---------------------------------------------------------------------------
def _check_transform_roundtrip(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify the row parses as an exact :class:`TransformationRule`."""
    return _roundtrip(row, loc, TransformationRule)


def _check_dataset_roundtrip(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify the row parses as an exact :class:`DatasetTransformationRule`."""
    return _roundtrip(row, loc, DatasetTransformationRule)


def _roundtrip(row: dict[str, Any], loc: str, model: type[BaseModel]) -> list[BlockIssue]:
    try:
        model.model_validate(row)
    except ValidationError as exc:
        return [
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.TYPE,
                location=f"{loc}." + ".".join(str(p) for p in err["loc"]),
                message=err["msg"],
            )
            for err in exc.errors()
        ]
    return []


# ---------------------------------------------------------------------------
# task 22 - exact keyword values (transformation-rules)
# ---------------------------------------------------------------------------
def _check_transform_keywords(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify enum/flag fields hold only exact supported values (task 22)."""
    issues: list[BlockIssue] = []

    data_type = row.get("dataType")
    if data_type is not None and not _is_member(ContractDataType, data_type):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.KEYWORD,
                location=f"{loc}.dataType",
                message=(
                    f"dataType '{data_type}' is not a supported keyword "
                    f"({', '.join(t.value for t in ContractDataType)})."
                ),
            )
        )

    for field in _YES_NO_FIELDS:
        value = row.get(field)
        if value is not None and value not in ("Y", "N"):
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.KEYWORD,
                    location=f"{loc}.{field}",
                    message=f"{field} '{value}' must be exactly 'Y' or 'N'.",
                )
            )

    # dataTransformationRules configuration operation keywords + casing.
    issues.extend(_check_config_op_keywords(row.get("dataTransformationRules"), loc))
    return issues


def _check_config_op_keywords(value: Any, loc: str) -> list[BlockIssue]:
    """Verify every configurations op key is an exact supported keyword (task 22)."""
    issues: list[BlockIssue] = []
    for entry in _iter_blob_list(value):
        if not isinstance(entry, dict):
            continue
        configurations = entry.get("configurations")
        if not isinstance(configurations, dict):
            continue
        for op_key, payload in configurations.items():
            if op_key not in _CONFIG_OP_KEYWORDS:
                issues.append(
                    BlockIssue(
                        severity=Severity.ERROR,
                        code=IssueCode.KEYWORD,
                        location=f"{loc}.dataTransformationRules.configurations.{op_key}",
                        message=(
                            f"Operation '{op_key}' is not a supported transformation keyword "
                            f"({', '.join(sorted(_CONFIG_OP_KEYWORDS))})."
                        ),
                    )
                )
                continue
            issues.extend(_check_op_payload_keywords(op_key, payload, loc))
    return issues


def _check_op_payload_keywords(op_key: str, payload: Any, loc: str) -> list[BlockIssue]:
    """Verify pinned inner keywords (Case, arithmetic operator) exact-match."""
    issues: list[BlockIssue] = []
    if not isinstance(payload, dict):
        return issues

    base = f"{loc}.dataTransformationRules.configurations.{op_key}"

    if op_key == TransformationOp.CASE_CONVERSION.value:
        case = payload.get("Case")
        if case is not None and not _is_member(CaseConversion, case):
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.KEYWORD,
                    location=f"{base}.Case",
                    message=(
                        f"Case '{case}' is not a supported keyword "
                        f"({', '.join(c.value for c in CaseConversion)})."
                    ),
                )
            )

    if op_key == TransformationOp.ARITHMETIC_OPERATIONS.value:
        for i, operation in enumerate(payload.get("operations") or []):
            if not isinstance(operation, dict):
                continue
            operator = operation.get("operator")
            if operator is not None and not _is_member(ArithmeticOperator, operator):
                issues.append(
                    BlockIssue(
                        severity=Severity.ERROR,
                        code=IssueCode.KEYWORD,
                        location=f"{base}.operations[{i}].operator",
                        message=(
                            f"operator '{operator}' is not a supported keyword "
                            f"({', '.join(o.value for o in ArithmeticOperator)})."
                        ),
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# UUID references (transformation-rules)
# ---------------------------------------------------------------------------
def _check_transform_uuids(
    row: dict[str, Any], loc: str, known: Optional[set[str]]
) -> list[BlockIssue]:
    """Verify sourceColumnName / dependsOn UUIDs are well-formed and resolvable."""
    issues: list[BlockIssue] = []

    source = row.get("sourceColumnName")
    if not source or not _UUID_RE.match(str(source)):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.UUID,
                location=f"{loc}.sourceColumnName",
                message=f"sourceColumnName '{source}' is not a valid column UUID.",
            )
        )
    elif known is not None and str(source) not in known:
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.UUID,
                location=f"{loc}.sourceColumnName",
                message=f"sourceColumnName '{source}' does not reference a known column.",
            )
        )

    for dep in _iter_uuid_blob(row.get("dependsOn")):
        if not _UUID_RE.match(dep):
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.UUID,
                    location=f"{loc}.dependsOn",
                    message=f"dependsOn entry '{dep}' is not a valid column UUID.",
                )
            )
        elif known is not None and dep not in known:
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.UUID,
                    location=f"{loc}.dependsOn",
                    message=f"dependsOn entry '{dep}' does not reference a known column.",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# dataset-transformation-rules rule blob checks (task 22)
# ---------------------------------------------------------------------------
def _check_dataset_rule_blob(
    row: dict[str, Any], loc: str, known: Optional[set[str]]
) -> list[BlockIssue]:
    """Verify the dataset ``rule`` blob shape + the aggregated field UUID."""
    issues: list[BlockIssue] = []
    rule = _parse_blob_obj(row.get("rule"))
    if not isinstance(rule, dict):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.TYPE,
                location=f"{loc}.rule",
                message="dataset rule must be a JSON object with 'aggrigation' or 'date_operations'.",
            )
        )
        return issues

    if "aggrigation" not in rule and "date_operations" not in rule:
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.KEYWORD,
                location=f"{loc}.rule",
                message="dataset rule must contain 'aggrigation' or 'date_operations'.",
            )
        )

    agg = rule.get("aggrigation")
    if isinstance(agg, dict):
        field = agg.get("field")
        if field is not None and not _UUID_RE.match(str(field)):
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.UUID,
                    location=f"{loc}.rule.aggrigation.field",
                    message=f"aggrigation field '{field}' is not a valid column UUID.",
                )
            )
        elif field is not None and known is not None and str(field) not in known:
            issues.append(
                BlockIssue(
                    severity=Severity.ERROR,
                    code=IssueCode.UUID,
                    location=f"{loc}.rule.aggrigation.field",
                    message=f"aggrigation field '{field}' does not reference a known column.",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _is_member(enum_cls: type[Enum], value: Any) -> bool:
    """Whether ``value`` (or its enum) is a member value of ``enum_cls``."""
    if isinstance(value, enum_cls):
        return True
    try:
        enum_cls(value)
        return True
    except ValueError:
        return False


def _iter_blob_list(value: Any) -> list[Any]:
    """Yield entries from a JSON-string / list blob value (or empty)."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(value, list):
        return value
    return []


def _parse_blob_obj(value: Any) -> Any:
    """Parse a JSON-string / dict blob value into an object (or None)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value


def _iter_uuid_blob(value: Any) -> list[str]:
    """Yield UUID strings from a dependsOn value (JSON-string, list, or None)."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return [value]
        value = parsed
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []


__all__ = [
    "Severity",
    "IssueCode",
    "BlockIssue",
    "TransformationBlockReport",
    "validate_transformation_block",
]
