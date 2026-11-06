# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation (Sprint 4).
# Validate the generated validation block (tasks 16-19).
#
# Before the generated ``validation-rules`` array is handed to Deliverable 6
# (full-config assembly) it is re-checked deterministically:
#   * task 17 - exact JSON keys: every row has EXACTLY the 30 contract keys, in
#               order, with exact spelling (including the isSkipValidaitons typo).
#   * task 18 - exact keyword values: dataType / validationRuleType (and the
#               Y/N flag fields) hold only values from the supported vocabulary.
#   * task 19 - UUID references: every sourceColumn / dependsOn UUID resolves to
#               a known column (when a reference set is supplied).
#   * task 16 - the block round-trips through the exact contract model (and,
#               optionally, the full contract validator inside a minimal envelope).
# The result is a plain, testable report (no exceptions for invalid content).
# ============================================================================
"""
validation_ir.validation
=========================
Deterministic validation of a generated ``validation-rules`` block (tasks 16-19).

Usage::

    from validation_ir.validation import validate_validation_block

    report = validate_validation_block(rows, known_column_uuids=uuids)
    if not report.is_valid:
        for issue in report.errors:
            print(issue.location, issue.message)
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from contract.enums import ContractDataType, ValidationRuleType
from contract.models import ValidationRule

# The exact keys of a validation-rules object, in contract (alphabetical) order
# (the 30 authored keys plus the server-generated ``id``). Kept explicit so any
# drift in the model surfaces as a task-17 failure.
_EXACT_KEYS: tuple[str, ...] = (
    "bu",
    "columnRequiredInErrorFile",
    "conditionalValidationRule",
    "dataCategory",
    "dataExclusionRules",
    "dataFormat",
    "dataTransformationRules",
    "dataType",
    "dependsOn",
    "errorMessageInfo",
    "id",
    "isMandatory",
    "isSkipValidaitons",
    "lovCheckType",
    "lovValidationRequired",
    "maxLengthAllowed",
    "maxValue",
    "minLengthAllowed",
    "minValue",
    "platform",
    "sourceColumn",
    "sourceColumnName",
    "specialCharNotAllowed",
    "specialCharToBeRemoved",
    "stringCheckRule",
    "subDataCategory",
    "transformationRequired",
    "uniqueValueInColumn",
    "useremail",
    "userrole",
    "validationRuleType",
)

# Y/N flag fields whose value, when present, must be exactly "Y" or "N".
_YES_NO_FIELDS: tuple[str, ...] = (
    "columnRequiredInErrorFile",
    "isMandatory",
    "lovValidationRequired",
    "transformationRequired",
    "uniqueValueInColumn",
)

# Accept BOTH the dashed v4 form used in the sample corpus and the compact
# 32-hex form the Deliverable 1 canonical model emits (uuid4().hex).
_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{32})$"
)


class Severity(str, Enum):
    """Issue severity."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(str, Enum):
    """Stable machine codes for validation-block issues."""

    KEYS = "KEYS"                    # missing/extra key (task 17)
    KEYWORD = "KEYWORD"             # value outside supported vocabulary (task 18)
    TYPE = "TYPE"                    # wrong JSON type / model parse failure (task 16)
    UUID = "UUID"                    # malformed or dangling UUID reference (task 19)


class BlockIssue(BaseModel):
    """A single problem found while validating a generated validation block."""

    severity: Severity = Field(..., description="error or warning")
    code: IssueCode = Field(..., description="Stable machine code")
    location: str = Field(..., description="Dotted path to the offending value")
    message: str = Field(..., description="Human-readable description")


class ValidationBlockReport(BaseModel):
    """Outcome of :func:`validate_validation_block`."""

    is_valid: bool = Field(..., description="True when there are no ERROR-severity issues")
    row_count: int = Field(0, ge=0, description="Number of rows checked")
    issues: list[BlockIssue] = Field(default_factory=list, description="All issues found")

    @property
    def errors(self) -> list[BlockIssue]:
        """Only the ERROR-severity issues."""
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[BlockIssue]:
        """Only the WARNING-severity issues."""
        return [i for i in self.issues if i.severity is Severity.WARNING]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def validate_validation_block(
    rows: list[dict[str, Any]],
    known_column_uuids: Optional[set[str]] = None,
) -> ValidationBlockReport:
    """Validate a generated ``validation-rules`` block (tasks 16-19).

    Args:
        rows: The generated validation-rules array (list of dicts, as emitted by
            :meth:`ValidationRuleCompiler.compile`).
        known_column_uuids: Optional set of valid column UUIDs. When provided,
            every ``sourceColumn`` / ``dependsOn`` reference is checked to exist
            (task 19); dangling references become ERRORs. When ``None`` only the
            UUID *format* is checked (dangling refs are not knowable).

    Returns:
        A :class:`ValidationBlockReport`. ``is_valid`` is ``True`` only when no
        ERROR-severity issues were found. Never raises for invalid content.
    """
    issues: list[BlockIssue] = []

    for index, row in enumerate(rows):
        loc = f"validation-rules[{index}]"
        issues.extend(_check_exact_keys(row, loc))            # task 17
        issues.extend(_check_model_roundtrip(row, loc))       # task 16
        issues.extend(_check_keyword_values(row, loc))        # task 18
        issues.extend(_check_uuid_refs(row, loc, known_column_uuids))  # task 19

    is_valid = not any(i.severity is Severity.ERROR for i in issues)
    return ValidationBlockReport(is_valid=is_valid, row_count=len(rows), issues=issues)


# ---------------------------------------------------------------------------
# task 17 - exact JSON keys
# ---------------------------------------------------------------------------
def _check_exact_keys(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify the row has EXACTLY the 30 contract keys, correctly spelled."""
    issues: list[BlockIssue] = []
    keys = set(row.keys())
    expected = set(_EXACT_KEYS)

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
                message=f"Unexpected key '{extra}' (not part of the validation-rules contract).",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# task 16 - round-trip through the exact contract model
# ---------------------------------------------------------------------------
def _check_model_roundtrip(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify the row parses as an exact :class:`ValidationRule` (structure/type)."""
    try:
        ValidationRule.model_validate(row)
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
# task 18 - exact keyword values
# ---------------------------------------------------------------------------
def _check_keyword_values(row: dict[str, Any], loc: str) -> list[BlockIssue]:
    """Verify enum/flag fields hold only exact supported values (task 18)."""
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

    rule_type = row.get("validationRuleType")
    if rule_type is not None and not _is_member(ValidationRuleType, rule_type):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.KEYWORD,
                location=f"{loc}.validationRuleType",
                message=(
                    f"validationRuleType '{rule_type}' is not a supported keyword "
                    f"({', '.join(t.value for t in ValidationRuleType)})."
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

    if row.get("isSkipValidaitons") not in (False, True):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.TYPE,
                location=f"{loc}.isSkipValidaitons",
                message="isSkipValidaitons must be a boolean.",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# task 19 - UUID references
# ---------------------------------------------------------------------------
def _check_uuid_refs(
    row: dict[str, Any], loc: str, known: Optional[set[str]]
) -> list[BlockIssue]:
    """Verify sourceColumn / dependsOn UUIDs are well-formed and resolvable."""
    issues: list[BlockIssue] = []

    source_column = row.get("sourceColumn")
    if not source_column or not _UUID_RE.match(str(source_column)):
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.UUID,
                location=f"{loc}.sourceColumn",
                message=f"sourceColumn '{source_column}' is not a valid column UUID.",
            )
        )
    elif known is not None and str(source_column) not in known:
        issues.append(
            BlockIssue(
                severity=Severity.ERROR,
                code=IssueCode.UUID,
                location=f"{loc}.sourceColumn",
                message=f"sourceColumn '{source_column}' does not reference a known column.",
            )
        )

    depends_on = row.get("dependsOn")
    for dep in _iter_uuid_blob(depends_on):
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


def _iter_uuid_blob(value: Any) -> list[str]:
    """Yield UUID strings from a dependsOn value (JSON-string, list, or None)."""
    if value is None:
        return []
    if isinstance(value, str):
        import json

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
    "ValidationBlockReport",
    "validate_validation_block",
]
