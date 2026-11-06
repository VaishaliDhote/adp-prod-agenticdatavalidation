# ============================================================================
# Deliverable 3 - Supported validation catalogue (tasks 14, 18, 19). REUSABLE.
# The FIXED set of validation rules the existing API can represent, plus the
# deterministic mapping from the canonical model's detected types/formats onto
# those supported validations. This catalogue is the single source of truth for
# "what is supported" and is shared by the analyzer (Sprint 3) and later by the
# Validation IR -> JSON compiler (Sprint 4).
# ============================================================================
"""
validation_analysis.catalog
============================
The supported-validation catalogue and canonical -> contract mapping tables.

Provenance
----------
Every entry mirrors a field of the ``validation-rules`` contract object as
measured across all 692 sample JSON files
(``documents_plan/contract_analysis/05_validation_rules_key_analysis.txt``).
Nothing here is invented: each :class:`SupportedValidationSpec` names the exact
contract field it populates and, where the contract fixes a keyword/value, the
exact token (casing/spelling preserved verbatim).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from canonical.enums import DataType, DetectedFormat
from contract.enums import ContractDataType
from validation_analysis.enums import SupportedValidation


# ---------------------------------------------------------------------------
# Catalogue entry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SupportedValidationSpec:
    """One supported validation rule the existing API can represent.

    Attributes:
        kind: The internal :class:`SupportedValidation` token.
        contract_field: The exact ``validation-rules`` key this rule populates
            (e.g. ``"isMandatory"``, ``"maxLengthAllowed"``, ``"lovCheckType"``).
        parameters: Names of the parameters this rule carries (Sprint 3 task 23).
        applies_to: Contract data types for which the rule is meaningful; empty
            means "any type".
        keyword: The exact fixed API value when the contract pins one
            (e.g. ``"Y"`` for a flag), else ``None`` (value is data-derived).
        description: Human-readable purpose.
    """

    kind: SupportedValidation
    contract_field: str
    parameters: tuple[str, ...] = ()
    applies_to: frozenset[ContractDataType] = frozenset()
    keyword: Optional[str] = None
    description: str = ""


# ---------------------------------------------------------------------------
# THE CATALOGUE - complete set of supported validations (contract file 5)
# ---------------------------------------------------------------------------
_CATALOG: dict[SupportedValidation, SupportedValidationSpec] = {
    SupportedValidation.MANDATORY: SupportedValidationSpec(
        kind=SupportedValidation.MANDATORY,
        contract_field="isMandatory",
        parameters=("isMandatory",),
        keyword="Y",
        description="Column value must be present (non-empty).",
    ),
    SupportedValidation.UNIQUE: SupportedValidationSpec(
        kind=SupportedValidation.UNIQUE,
        contract_field="uniqueValueInColumn",
        parameters=("uniqueValueInColumn",),
        keyword="Y",
        description="Values in the column must be unique (no duplicates).",
    ),
    SupportedValidation.DATA_TYPE: SupportedValidationSpec(
        kind=SupportedValidation.DATA_TYPE,
        contract_field="dataType",
        parameters=("dataType",),
        description="Column must conform to a supported data type.",
    ),
    SupportedValidation.MIN_LENGTH: SupportedValidationSpec(
        kind=SupportedValidation.MIN_LENGTH,
        contract_field="minLengthAllowed",
        parameters=("minLengthAllowed",),
        applies_to=frozenset({ContractDataType.TEXT, ContractDataType.REGEX}),
        description="Minimum string length (stored as a string integer).",
    ),
    SupportedValidation.MAX_LENGTH: SupportedValidationSpec(
        kind=SupportedValidation.MAX_LENGTH,
        contract_field="maxLengthAllowed",
        parameters=("maxLengthAllowed",),
        applies_to=frozenset({ContractDataType.TEXT, ContractDataType.REGEX}),
        description="Maximum string length (stored as a string integer).",
    ),
    SupportedValidation.MIN_VALUE: SupportedValidationSpec(
        kind=SupportedValidation.MIN_VALUE,
        contract_field="minValue",
        parameters=("minValue",),
        applies_to=frozenset({ContractDataType.NUMBER}),
        description="Numeric lower bound (stored as a string number).",
    ),
    SupportedValidation.MAX_VALUE: SupportedValidationSpec(
        kind=SupportedValidation.MAX_VALUE,
        contract_field="maxValue",
        parameters=("maxValue",),
        applies_to=frozenset({ContractDataType.NUMBER}),
        description="Numeric upper bound (stored as a string number).",
    ),
    SupportedValidation.DATE_FORMAT: SupportedValidationSpec(
        kind=SupportedValidation.DATE_FORMAT,
        contract_field="dataFormat",
        parameters=("dataType", "dataFormat"),
        applies_to=frozenset({ContractDataType.DATE}),
        description="Date column must match a date mask (dataFormat).",
    ),
    SupportedValidation.NUMERIC_FORMAT: SupportedValidationSpec(
        kind=SupportedValidation.NUMERIC_FORMAT,
        contract_field="dataFormat",
        parameters=("dataType", "dataFormat"),
        applies_to=frozenset({ContractDataType.NUMBER}),
        description="Number column must match a numeric spec (dataFormat).",
    ),
    SupportedValidation.EMAIL_FORMAT: SupportedValidationSpec(
        kind=SupportedValidation.EMAIL_FORMAT,
        contract_field="dataType",
        parameters=("dataType",),
        keyword=ContractDataType.EMAIL.value,
        applies_to=frozenset({ContractDataType.EMAIL}),
        description="Column must contain valid e-mail addresses.",
    ),
    SupportedValidation.REGEX_PATTERN: SupportedValidationSpec(
        kind=SupportedValidation.REGEX_PATTERN,
        contract_field="dataFormat",
        parameters=("dataType", "dataFormat"),
        applies_to=frozenset({ContractDataType.REGEX}),
        description="Column must match a regular-expression pattern.",
    ),
    SupportedValidation.LIST_OF_VALUES: SupportedValidationSpec(
        kind=SupportedValidation.LIST_OF_VALUES,
        contract_field="lovCheckType",
        parameters=("lovCheckType", "lovValidationRequired", "allowedValues"),
        applies_to=frozenset({ContractDataType.DROPDOWN, ContractDataType.TEXT}),
        description="Value must belong to a controlled list (dropdown/LOV).",
    ),
    SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED: SupportedValidationSpec(
        kind=SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED,
        contract_field="specialCharNotAllowed",
        parameters=("specialCharNotAllowed",),
        description="Per-character allow/deny rule with count bounds.",
    ),
    SupportedValidation.STRING_CHECK: SupportedValidationSpec(
        kind=SupportedValidation.STRING_CHECK,
        contract_field="stringCheckRule",
        parameters=("stringCheckRule",),
        description="Rich string assertions (Equals/Contains/Ends/Regex/...).",
    ),
    SupportedValidation.CONDITIONAL: SupportedValidationSpec(
        kind=SupportedValidation.CONDITIONAL,
        contract_field="conditionalValidationRule",
        parameters=("conditionalValidationRule",),
        description="Cross-field if/then conditional validation.",
    ),
    SupportedValidation.REQUIRED_IN_ERROR_FILE: SupportedValidationSpec(
        kind=SupportedValidation.REQUIRED_IN_ERROR_FILE,
        contract_field="columnRequiredInErrorFile",
        parameters=("columnRequiredInErrorFile",),
        keyword="Y",
        description="Echo this column into the error report on failure.",
    ),
}


def get_spec(kind: SupportedValidation) -> SupportedValidationSpec:
    """Return the catalogue spec for a supported-validation kind."""
    return _CATALOG[kind]


def all_specs() -> list[SupportedValidationSpec]:
    """Return every supported-validation spec (stable catalogue order)."""
    return list(_CATALOG.values())


def is_supported(kind: SupportedValidation) -> bool:
    """Whether a validation kind is representable by the existing API."""
    return kind in _CATALOG


# ---------------------------------------------------------------------------
# Canonical DataType -> contract ContractDataType (Sprint 3 task 10)
# ---------------------------------------------------------------------------
_DATATYPE_MAP: dict[DataType, ContractDataType] = {
    DataType.STRING: ContractDataType.TEXT,
    DataType.INTEGER: ContractDataType.NUMBER,
    DataType.DECIMAL: ContractDataType.NUMBER,
    DataType.BOOLEAN: ContractDataType.TEXT,
    DataType.DATE: ContractDataType.DATE,
    DataType.DATETIME: ContractDataType.DATE,
    # DataType.UNKNOWN intentionally absent -> triggers an unsupported note.
}


def map_data_type(data_type: DataType) -> Optional[ContractDataType]:
    """Map a canonical :class:`DataType` to a contract :class:`ContractDataType`.

    Returns ``None`` when no supported contract type applies (e.g. UNKNOWN),
    which the analyzer records as an unsupported requirement (task 21).
    """
    return _DATATYPE_MAP.get(data_type)


# ---------------------------------------------------------------------------
# Canonical DetectedFormat -> contract dataFormat mask / dataType override
# (Sprint 3 tasks 11-14, 17). Value is (contract_data_type, data_format_mask).
# A ``None`` mask means the type alone expresses the rule (e.g. Email).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FormatMapping:
    """Mapping of a detected value-format onto the contract representation."""

    data_type: ContractDataType
    data_format: Optional[str]
    validation: SupportedValidation
    supported: bool = True
    reason: str = ""


_FORMAT_MAP: dict[DetectedFormat, FormatMapping] = {
    DetectedFormat.DATE_ISO: FormatMapping(
        ContractDataType.DATE, "yyyy-MM-dd", SupportedValidation.DATE_FORMAT
    ),
    DetectedFormat.DATE_US: FormatMapping(
        ContractDataType.DATE, "MM/dd/yyyy", SupportedValidation.DATE_FORMAT
    ),
    DetectedFormat.DATE_EU: FormatMapping(
        ContractDataType.DATE, "dd/MM/yyyy", SupportedValidation.DATE_FORMAT
    ),
    DetectedFormat.DATE_COMPACT: FormatMapping(
        ContractDataType.DATE, "yyyyMMdd", SupportedValidation.DATE_FORMAT
    ),
    DetectedFormat.DATETIME_ISO: FormatMapping(
        ContractDataType.DATE, "yyyy-MM-dd HH:mm:ss", SupportedValidation.DATE_FORMAT
    ),
    DetectedFormat.EMAIL: FormatMapping(
        ContractDataType.EMAIL, None, SupportedValidation.EMAIL_FORMAT
    ),
    DetectedFormat.UUID: FormatMapping(
        ContractDataType.REGEX,
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        SupportedValidation.REGEX_PATTERN,
    ),
    DetectedFormat.INTEGER: FormatMapping(
        ContractDataType.NUMBER, None, SupportedValidation.NUMERIC_FORMAT
    ),
    DetectedFormat.DECIMAL: FormatMapping(
        ContractDataType.NUMBER, None, SupportedValidation.NUMERIC_FORMAT
    ),
    DetectedFormat.CURRENCY: FormatMapping(
        ContractDataType.NUMBER, None, SupportedValidation.NUMERIC_FORMAT
    ),
    # PERCENTAGE has no faithful API representation (no percent dataType/mask).
    DetectedFormat.PERCENTAGE: FormatMapping(
        ContractDataType.NUMBER, None, SupportedValidation.NUMERIC_FORMAT,
        supported=False,
        reason="Percentage format is not representable by a supported API validation.",
    ),
}


def map_format(detected: DetectedFormat) -> Optional[FormatMapping]:
    """Map a canonical :class:`DetectedFormat` to a contract representation.

    Returns ``None`` for ``NONE``/``UNKNOWN`` (no format rule inferred).
    """
    return _FORMAT_MAP.get(detected)


__all__ = [
    "SupportedValidationSpec",
    "FormatMapping",
    "get_spec",
    "all_specs",
    "is_supported",
    "map_data_type",
    "map_format",
]
