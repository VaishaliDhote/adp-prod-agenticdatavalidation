# ============================================================================
# Deliverable 5 - Supported transformation catalogue (tasks 14-16). REUSABLE.
# The FIXED set of transformations the existing API can represent, plus the
# deterministic mapping from canonical source->target column differences onto
# those supported transformations. This catalogue is the single source of truth
# for "what transformation is supported" and is shared by the analyzer
# (Sprint 5) and later by the Transformation IR -> JSON compiler (Sprint 6).
# ============================================================================
"""
transformation_analysis.catalog
================================
The supported-transformation catalogue and canonical -> contract mapping tables.

Provenance
----------
Every entry mirrors an operation of the ``transformation-rules`` object
(contract file 4) or the ``dataset-transformation-rules`` object (file 3) as
measured across all 692 sample JSON files
(``documents_plan/contract_analysis/04_*`` and ``03_*``). Nothing here is
invented: each :class:`SupportedTransformationSpec` names the exact contract
field it populates, the exact operation keyword inside ``configurations`` when
one applies, and any pinned keyword value (casing/spelling preserved verbatim,
including the intentional ``aggrigation`` typo).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from canonical.enums import DataType, DetectedFormat
from contract.enums import ContractDataType
from transformation_analysis.enums import SupportedTransformation


# ---------------------------------------------------------------------------
# Catalogue entry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SupportedTransformationSpec:
    """One supported transformation the existing API can represent.

    Attributes:
        kind: The internal :class:`SupportedTransformation` token.
        contract_field: The exact ``transformation-rules`` /
            ``dataset-transformation-rules`` key this operation populates
            (e.g. ``"dataTransformationRules"``, ``"defaultValue"``,
            ``"specialCharToBeRemoved"``, ``"lovCheckType"``,
            ``"targetColumnName"``, ``"sourceColumnName"``, ``"rule"``).
        config_op: The exact operation keyword inside a
            ``dataTransformationRules[].configurations`` object (e.g.
            ``"date_format"``, ``"Case_Conversion"``) when the operation lives
            there; ``None`` for mapping-level or ``defaultValue``/blob ops.
        parameters: Names of the parameters this operation carries (task 12).
        keyword: The exact fixed API keyword when the contract pins one
            (e.g. ``"cnt"`` for aggregation), else ``None``.
        dataset_level: ``True`` when the operation is a dataset-level derived
            field (contract file 3) rather than a per-column rule (file 4).
        description: Human-readable purpose.
    """

    kind: SupportedTransformation
    contract_field: str
    config_op: Optional[str] = None
    parameters: tuple[str, ...] = ()
    keyword: Optional[str] = None
    dataset_level: bool = False
    description: str = ""


# ---------------------------------------------------------------------------
# THE CATALOGUE - complete set of supported transformations (files 3 & 4)
# ---------------------------------------------------------------------------
_CATALOG: dict[SupportedTransformation, SupportedTransformationSpec] = {
    SupportedTransformation.DIRECT_MAPPING: SupportedTransformationSpec(
        kind=SupportedTransformation.DIRECT_MAPPING,
        contract_field="sourceColumnName",
        parameters=("sourceColumnName",),
        description="Pass-through copy of a source column into a target column.",
    ),
    SupportedTransformation.RENAME: SupportedTransformationSpec(
        kind=SupportedTransformation.RENAME,
        contract_field="targetColumnName",
        parameters=("targetColumnName",),
        description="Target column carries source values under a different name.",
    ),
    SupportedTransformation.DATE_FORMAT: SupportedTransformationSpec(
        kind=SupportedTransformation.DATE_FORMAT,
        contract_field="dataTransformationRules",
        config_op="date_format",
        parameters=("source_date_format", "target_date_format"),
        description="Reformat a date from a source mask to a target mask.",
    ),
    SupportedTransformation.TARGET_FORMAT: SupportedTransformationSpec(
        kind=SupportedTransformation.TARGET_FORMAT,
        contract_field="dataTransformationRules",
        config_op="target_format",
        parameters=("targetType", "targetFormat", "targetDecimalAllowed"),
        description="Render a value in a target type/number format.",
    ),
    SupportedTransformation.ARITHMETIC: SupportedTransformationSpec(
        kind=SupportedTransformation.ARITHMETIC,
        contract_field="dataTransformationRules",
        config_op="arithmetic_operations",
        parameters=("columns", "operations"),
        description="Combine columns/values with ADDITION or MULTIPLICATION.",
    ),
    SupportedTransformation.LENGTH_FORMAT: SupportedTransformationSpec(
        kind=SupportedTransformation.LENGTH_FORMAT,
        contract_field="dataTransformationRules",
        config_op="length_format",
        parameters=("target_length_size", "target_filler"),
        description="Pad or truncate a value to a fixed length.",
    ),
    SupportedTransformation.TRIM: SupportedTransformationSpec(
        kind=SupportedTransformation.TRIM,
        contract_field="dataTransformationRules",
        config_op="trim",
        parameters=("position", "columns"),
        description="Trim whitespace from the start/end of a value.",
    ),
    SupportedTransformation.CASE_CONVERSION: SupportedTransformationSpec(
        kind=SupportedTransformation.CASE_CONVERSION,
        contract_field="dataTransformationRules",
        config_op="Case_Conversion",
        parameters=("Case",),
        description="Convert a value to UpperCase or LowerCase.",
    ),
    SupportedTransformation.SPLIT: SupportedTransformationSpec(
        kind=SupportedTransformation.SPLIT,
        contract_field="dataTransformationRules",
        config_op="split_operations",
        parameters=("operations", "seperator"),
        description="Extract a substring (direction/position/length) of a value.",
    ),
    SupportedTransformation.REMOVE_CHARACTERS: SupportedTransformationSpec(
        kind=SupportedTransformation.REMOVE_CHARACTERS,
        contract_field="specialCharToBeRemoved",
        parameters=("remove_characters",),
        description="Strip unwanted characters from a value before output.",
    ),
    SupportedTransformation.DEFAULT_VALUE: SupportedTransformationSpec(
        kind=SupportedTransformation.DEFAULT_VALUE,
        contract_field="defaultValue",
        parameters=("defaultValue",),
        description="Supply a constant/default value for the target column.",
    ),
    SupportedTransformation.FIELD_CONCAT: SupportedTransformationSpec(
        kind=SupportedTransformation.FIELD_CONCAT,
        contract_field="defaultValue",
        config_op="field_concat",
        parameters=("operations",),
        description="Concatenate one or more source columns with pre/post text.",
    ),
    SupportedTransformation.DATE_STAMP: SupportedTransformationSpec(
        kind=SupportedTransformation.DATE_STAMP,
        contract_field="defaultValue",
        config_op="date_operations",
        parameters=("type", "format"),
        description="Generate a current_date / current_time stamp value.",
    ),
    SupportedTransformation.CONDITIONAL: SupportedTransformationSpec(
        kind=SupportedTransformation.CONDITIONAL,
        contract_field="defaultValue",
        config_op="conditional",
        parameters=("conditional", "else"),
        description="If/then/else mapping producing the target value.",
    ),
    SupportedTransformation.LOV_MAPPING: SupportedTransformationSpec(
        kind=SupportedTransformation.LOV_MAPPING,
        contract_field="lovCheckType",
        parameters=("lovCheckType", "lovValidationRequired"),
        description="Map/validate a value against a named controlled list (LOV).",
    ),
    SupportedTransformation.DATASET_AGGREGATION: SupportedTransformationSpec(
        kind=SupportedTransformation.DATASET_AGGREGATION,
        contract_field="rule",
        config_op="aggrigation",  # intentional source typo preserved
        parameters=("field", "operation"),
        keyword="cnt",
        dataset_level=True,
        description="Dataset-level derived field: count of a dataset (aggrigation).",
    ),
    SupportedTransformation.DATASET_DATE_STAMP: SupportedTransformationSpec(
        kind=SupportedTransformation.DATASET_DATE_STAMP,
        contract_field="rule",
        config_op="date_operations",
        parameters=("type", "format"),
        dataset_level=True,
        description="Dataset-level derived field: current-date stamp.",
    ),
}


def get_spec(kind: SupportedTransformation) -> SupportedTransformationSpec:
    """Return the catalogue spec for a supported-transformation kind."""
    return _CATALOG[kind]


def all_specs() -> list[SupportedTransformationSpec]:
    """Return every supported-transformation spec (stable catalogue order)."""
    return list(_CATALOG.values())


def is_supported(kind: SupportedTransformation) -> bool:
    """Whether a transformation kind is representable by the existing API."""
    return kind in _CATALOG


# ---------------------------------------------------------------------------
# Canonical DataType -> contract ContractDataType (target type, task 12)
# ---------------------------------------------------------------------------
_DATATYPE_MAP: dict[DataType, ContractDataType] = {
    DataType.STRING: ContractDataType.TEXT,
    DataType.INTEGER: ContractDataType.NUMBER,
    DataType.DECIMAL: ContractDataType.NUMBER,
    DataType.BOOLEAN: ContractDataType.TEXT,
    DataType.DATE: ContractDataType.DATE,
    DataType.DATETIME: ContractDataType.DATE,
}


def map_data_type(data_type: DataType) -> Optional[ContractDataType]:
    """Map a canonical :class:`DataType` to a contract :class:`ContractDataType`.

    Returns ``None`` when no supported contract type applies (e.g. UNKNOWN).
    """
    return _DATATYPE_MAP.get(data_type)


# ---------------------------------------------------------------------------
# Canonical date DetectedFormat -> contract date mask (source/target masks for
# the date_format operation, task 12). Only date-family formats are mapped.
# ---------------------------------------------------------------------------
_DATE_MASK_MAP: dict[DetectedFormat, str] = {
    DetectedFormat.DATE_ISO: "yyyy-MM-dd",
    DetectedFormat.DATE_US: "MM/dd/yyyy",
    DetectedFormat.DATE_EU: "dd/MM/yyyy",
    DetectedFormat.DATETIME_ISO: "yyyy-MM-dd HH:mm:ss",
}


def date_mask(detected: DetectedFormat) -> Optional[str]:
    """Return the contract date mask for a detected date format, or ``None``."""
    return _DATE_MASK_MAP.get(detected)


def is_date_format(detected: DetectedFormat) -> bool:
    """Whether a detected format is a date/datetime family format."""
    return detected in _DATE_MASK_MAP


# ---------------------------------------------------------------------------
# Number-family formats (used to decide TARGET_FORMAT for numeric columns).
# ---------------------------------------------------------------------------
_NUMBER_FORMATS = frozenset(
    {DetectedFormat.INTEGER, DetectedFormat.DECIMAL, DetectedFormat.CURRENCY}
)


def is_number_format(detected: DetectedFormat) -> bool:
    """Whether a detected format is a numeric-family format."""
    return detected in _NUMBER_FORMATS


__all__ = [
    "SupportedTransformationSpec",
    "get_spec",
    "all_specs",
    "is_supported",
    "map_data_type",
    "date_mask",
    "is_date_format",
    "is_number_format",
]
