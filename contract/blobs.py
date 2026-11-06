# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure.
# Typed sub-models for the "nested JSON-string" fields (Sprint 2 task 14).
#
# Several contract fields are stored as a STRING whose content is a whole
# serialized JSON document (e.g. configuration-data.outputFileRules,
# transformation-rules.dataTransformationRules,
# validation-rules.conditionalValidationRule). This module models the
# documented structure of each such blob and provides the parse-on-load /
# dump-on-write helpers used by the section models in ``contract.models``.
#
# FIDELITY STRATEGY
# -----------------
# * Every blob model sets ``extra="allow"`` so rare / undocumented nested keys
#   observed in the 692-file corpus survive a parse -> serialize round-trip
#   without data loss. The documented keys are typed explicitly for validation
#   and IDE support; the long tail is preserved verbatim.
# * ``dump_blob`` re-serializes with ``by_alias=True`` and compact separators so
#   the emitted string is semantically identical to the source. Byte-identical
#   whitespace is intentionally NOT guaranteed (the API consumes these strings
#   as JSON, so semantic equality is the contract-correct fidelity criterion).
# * Values that are legitimately non-JSON sentinels (``""``, ``"[]"`` observed
#   in ``specialCharToBeRemoved``; the literal ``"<null>"`` in
#   ``targetFileName``) are preserved raw via :class:`RawBlob`.
# ============================================================================
"""
contract.blobs
==============
Typed models + (de)serialization helpers for the contract's embedded
JSON-string fields.

Public helpers
--------------
``parse_blob(value, model)``      -> ``None | model | list[model] | RawBlob``
``dump_blob(value)``              -> ``None | str`` (re-serialized JSON string)
"""
from __future__ import annotations

import json
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Base + raw fallback
# ---------------------------------------------------------------------------


class _BlobModel(BaseModel):
    """Base for every embedded-JSON model: preserve undocumented keys."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RawBlob(BaseModel):
    """Fallback wrapper preserving a blob value that is not clean JSON.

    Used for legitimate non-JSON sentinels such as the empty string or ``"[]"``
    seen in ``specialCharToBeRemoved``, so the exact stored token round-trips.
    """

    model_config = ConfigDict(frozen=True)
    raw: str = Field(..., description="Original stored string, preserved verbatim")


# ---------------------------------------------------------------------------
# Shared building blocks (used by several sections)
# ---------------------------------------------------------------------------


class DataExclusionConfigValues(_BlobModel):
    """A single sentinel-value mapping inside ``dataExclusionRules``."""

    sourceValue: Optional[str] = Field(None, description="Sentinel source value treated as empty")
    proxyValue: Optional[str] = Field(None, description="Replacement value (usually null)")


class DataExclusionConfigurations(_BlobModel):
    """``configurations`` object of a ``dataExclusionRules`` entry."""

    values: list[DataExclusionConfigValues] = Field(default_factory=list)


class DataExclusionEntry(_BlobModel):
    """One ``dataExclusionRules`` array element (files 1 & 5)."""

    dataSetId: Optional[str] = Field(None, description="Source dataset uuid")
    configurations: Optional[DataExclusionConfigurations] = None


class DataTransformationConfigEntry(_BlobModel):
    """One ``dataTransformationRules`` array element (files 4 & 5).

    The ``configurations`` object holds one or more transform operations
    (``date_format``, ``target_format``, ``arithmetic_operations``,
    ``length_format``, ``trim``, ``Case_Conversion``, ``split_operations``).
    These are kept as a free-form object (``extra="allow"``) because operations
    combine freely and each has its own shape; :class:`TransformConfigurations`
    documents the known operation keys.
    """

    dataSetId: Optional[str] = Field(None, description="Source dataset uuid")
    configurations: dict[str, Any] = Field(
        default_factory=dict, description="One or more transform operations"
    )


class SpecialCharRemovalConfig(_BlobModel):
    """``configurations`` of a ``specialCharToBeRemoved`` entry (file 4)."""

    remove_characters: Optional[str] = Field(
        None, description="Characters to strip from the value before output"
    )


class SpecialCharRemovalEntry(_BlobModel):
    """One ``specialCharToBeRemoved`` array element (file 4)."""

    dataSetId: Optional[str] = Field(None, description="Source dataset uuid")
    configurations: Optional[SpecialCharRemovalConfig] = None


# ---------------------------------------------------------------------------
# configuration-data blobs (file 2)
# ---------------------------------------------------------------------------


class FilesInfoEntry(_BlobModel):
    """One ``filesInfo`` array element: how an input source is parsed."""

    sourceKey: Optional[str] = Field(None, description="Source tab/line key")
    type: Optional[str] = Field(None, description="Input file type (csv/xlsx/json/xml/txt/master/Form/MYDC)")
    processing: Optional[str] = Field(None, description="header/position/delimeter (typo preserved)")
    headerIndex: Optional[int] = Field(None, description="Header row index (-1 = none)")
    delimeter: Optional[str] = Field(None, description="Field delimiter for csv/txt (typo key preserved)")
    batchSize: Optional[str] = Field(None, description="Chunked read size, e.g. '1000'")
    primaryIdentifier: Optional[str] = Field(None, description="Key column of the source")
    groupIdentifier: Optional[str] = Field(None, description="Grouping column uuid for multi-line records")
    masterType: Optional[str] = Field(None, description="Master-data kind")
    processingType: Optional[str] = Field(None, description="e.g. 'PECI'")
    filterData: Optional[str] = Field(None, description="'Y' when filtering applies")
    template: Optional[Any] = Field(None, description="Fixed-width/XML layout template (nested)")


class InputRuleReference(_BlobModel):
    """A join/merge edge inside ``inputRules[].references[]``."""

    sourceKey: Optional[str] = None
    type: Optional[str] = Field(None, description="'source' or 'dataset'")
    relationship: Optional[str] = Field(None, description="OnetoOne/AlltoAll/AlltoOne/Merge")
    headerIndex: Optional[int] = None
    dataRowNumber: Optional[int] = None
    keyIdentifiers: list[Any] = Field(default_factory=list, description="Key columns used to match rows")


class InputRuleEntry(_BlobModel):
    """One ``inputRules`` array element: a source and how it joins."""

    sourceKey: Optional[str] = None
    type: Optional[str] = Field(None, description="'source' or 'dataset'")
    dataSetId: Optional[str] = Field(None, description="Dataset uuid this source becomes")
    dataSetName: Optional[str] = Field(None, description="Friendly dataset name (DS1, DS2, ...)")
    headerIndex: Optional[int] = None
    sequence: Optional[Union[str, int]] = Field(None, description="Processing order")
    outputProcessType: Optional[str] = Field(None, description="'Default' or 'Mode1'")
    references: list[InputRuleReference] = Field(default_factory=list)


class OutputFileRuleEntry(_BlobModel):
    """One ``outputFileRules`` array element: how an output file is built.

    This is the largest/most variable embedded structure in the contract
    (xlsx/csv/txt/xml/pdf variants, up to 131 KB). Only the stable top-level
    keys are typed; every nested variant is preserved via ``extra="allow"``.
    """

    outputFileType: Optional[str] = Field(None, description="xlsx/csv/txt/xml/pdf")
    dataSetName: Optional[str] = Field(None, description="Dataset uuid that feeds this file")
    fileName: Optional[Any] = Field(None, description="Output filename build spec")
    sheets: Optional[list[Any]] = Field(None, description="Per-output-sheet spec (xlsx)")
    fields: Optional[list[Any]] = Field(None, description="Flat field list ({field=uuid, name})")
    documentId: Optional[str] = Field(None, description="Template document reference uuid")


class DataRuleEntry(_BlobModel):
    """One ``dataRules`` array element: dataset filter/group/aggregate."""

    dataSetId: Optional[str] = None
    dataSetName: Optional[str] = None
    filters: Optional[Any] = Field(None, description="Row filter conditions")
    groupBy: Optional[Any] = Field(None, description="Grouping / aggregation spec")


class OutputRuleEntry(_BlobModel):
    """One ``outputRules`` array element: output row ordering (file 2)."""

    ruleType: Optional[str] = Field(None, description="Only 'sort' observed")
    order: Optional[str] = Field(None, description="'asc' or 'desc'")
    column: Optional[str] = Field(None, description="Column uuid to sort by")
    dataSetId: Optional[str] = None
    dataSetName: Optional[str] = None


# ---------------------------------------------------------------------------
# transformation-rules blobs (file 4)
# ---------------------------------------------------------------------------


class DefaultValueBlob(_BlobModel):
    """``defaultValue`` blob: const / date / split / concat / conditional.

    All shape families (``defaultValue``, ``date_operations``,
    ``split_operations``, ``field_concat``, ``conditional`` + ``else``) are
    optional and preserved; ``extra="allow"`` keeps any unlisted keys.
    """

    defaultValue: Optional[Any] = Field(None, description="Static constant value (may be null/empty)")
    date_operations: Optional[Any] = Field(None, description="Generated date/time value spec")
    split_operations: Optional[Any] = Field(None, description="Substring-of-source spec")
    field_concat: Optional[Any] = Field(None, description="Concatenation of source columns")
    conditional: Optional[list[Any]] = Field(None, description="if/then branches")
    else_: Optional[Any] = Field(None, alias="else", description="Fallback branch")


# ---------------------------------------------------------------------------
# validation-rules blobs (file 5)
# ---------------------------------------------------------------------------


class SpecialCharNotAllowedEntry(_BlobModel):
    """One ``specialCharNotAllowed`` array element (file 5)."""

    character: Optional[str] = Field(None, description="Disallowed character (or '<space>' token)")
    min: Optional[int] = Field(None, description="Minimum allowed count (0 or 1)")
    max: Optional[int] = Field(None, description="Maximum allowed count (0, 1, or 20)")


class StringCheckCondition(_BlobModel):
    """A single condition inside a full-form ``stringCheckRule`` (file 5)."""

    constraint: Optional[str] = Field(None, description="Contains/Ends/Equals/GreaterThan/LessThan/NotEquals/Regex/<null>")
    value: Optional[str] = Field(None, description="Constraint operand")


class StringCheckRuleBlob(_BlobModel):
    """``stringCheckRule`` blob: shorthand ``{Equals: ...}`` OR full form.

    The shorthand form (e.g. ``{"Equals": "Mounika1"}``) is captured via
    ``extra="allow"``; the full form uses ``operator`` + ``conditions``.
    """

    operator: Optional[str] = Field(None, description="'and' combiner (full form)")
    conditions: Optional[list[StringCheckCondition]] = Field(None, description="Full-form conditions")


class ConditionalIfPredicate(_BlobModel):
    """A predicate inside ``conditionalValidationRule.conditions[].if[]``."""

    lhs: Optional[str] = Field(None, description="Left operand, usually '{{<uuid>}}'")
    constraint: Optional[str] = Field(None, description="<notnull>/<null>/Equals/...")
    rhs: Optional[str] = Field(None, description="Right operand / comparison value")


class ConditionalThenValidation(_BlobModel):
    """A validation enforced when the if-branch is true."""

    lhs: Optional[str] = Field(None, description="Target column '{{<uuid>}}'")
    operation: Optional[str] = Field(None, description="e.g. 'Mandatory'")
    rhs: Optional[str] = Field(None, description="e.g. 'Y'/'N'")


class ConditionalThen(_BlobModel):
    """The ``then`` clause of a conditional validation condition."""

    validations: Optional[list[ConditionalThenValidation]] = None


class ConditionalCondition(_BlobModel):
    """One entry of ``conditionalValidationRule.conditions[]`` (file 5)."""

    if_: Optional[list[ConditionalIfPredicate]] = Field(None, alias="if")
    operator: Optional[str] = Field(None, description="'And'/'Or' when multiple predicates")
    then: Optional[ConditionalThen] = None


class ConditionalValidationRuleBlob(_BlobModel):
    """``conditionalValidationRule`` blob: if/then cross-field rules (file 5)."""

    conditions: Optional[list[ConditionalCondition]] = None


# ---------------------------------------------------------------------------
# dataset-transformation-rules blob (file 3)
# ---------------------------------------------------------------------------


class DatasetAggregationRule(_BlobModel):
    """``aggrigation`` computation (typo preserved) (file 3)."""

    field: Optional[str] = Field(None, description="Source column uuid to aggregate")
    operation: Optional[str] = Field(None, description="Aggregate operation, e.g. 'cnt'")


class DatasetDateOperationRule(_BlobModel):
    """``date_operations`` computation (file 3)."""

    type: Optional[str] = Field(None, description="e.g. 'current_date'")
    format: Optional[str] = Field(None, description="Date mask")


class DatasetRuleBlob(_BlobModel):
    """``rule`` blob of a dataset-transformation-rule (file 3)."""

    aggrigation: Optional[DatasetAggregationRule] = Field(None, description="Count/aggregate (typo key preserved)")
    date_operations: Optional[DatasetDateOperationRule] = Field(None, description="Current-date stamp")


# ---------------------------------------------------------------------------
# (de)serialization helpers
# ---------------------------------------------------------------------------


def parse_blob(value: Any, model: type[BaseModel]) -> Any:
    """Parse a stored blob value into a typed model (or list of models).

    Args:
        value: The raw stored value - ``None``, a JSON string, an already
            parsed ``dict``/``list``, or an existing model instance.
        model: The target :class:`~pydantic.BaseModel` subclass for each object.

    Returns:
        ``None`` when the value is null; a ``model`` instance for a JSON object;
        a ``list[model]`` for a JSON array; or a :class:`RawBlob` when the value
        is a non-JSON sentinel (e.g. ``""``) so nothing is lost.
    """
    if value is None:
        return None
    if isinstance(value, RawBlob):
        return value
    if isinstance(value, BaseModel):
        return value

    parsed: Any = value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return RawBlob(raw=value)
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return RawBlob(raw=value)

    if isinstance(parsed, list):
        return [model.model_validate(item) if isinstance(item, dict) else item for item in parsed]
    if isinstance(parsed, dict):
        return model.model_validate(parsed)
    # A JSON scalar (e.g. a bare string/number) - preserve as raw.
    return RawBlob(raw=value if isinstance(value, str) else json.dumps(parsed))


def dump_blob(value: Any) -> Optional[str]:
    """Serialize a typed blob (or list) back to a JSON string for the contract.

    Uses ``by_alias=True`` and compact separators so the emitted string is a
    faithful, semantically-identical JSON representation. Returns ``None`` for
    null blobs and the preserved raw token for :class:`RawBlob`.
    """
    if value is None:
        return None
    if isinstance(value, RawBlob):
        return value.raw
    if isinstance(value, BaseModel):
        payload: Any = value.model_dump(by_alias=True, exclude_unset=True)
    elif isinstance(value, list):
        payload = [
            item.model_dump(by_alias=True, exclude_unset=True)
            if isinstance(item, BaseModel)
            else item
            for item in value
        ]
    else:
        payload = value
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "RawBlob",
    "parse_blob",
    "dump_blob",
    # shared
    "DataExclusionEntry",
    "DataExclusionConfigurations",
    "DataExclusionConfigValues",
    "DataTransformationConfigEntry",
    "SpecialCharRemovalEntry",
    "SpecialCharRemovalConfig",
    # configuration-data
    "FilesInfoEntry",
    "InputRuleEntry",
    "InputRuleReference",
    "OutputFileRuleEntry",
    "DataRuleEntry",
    "OutputRuleEntry",
    # transformation-rules
    "DefaultValueBlob",
    # validation-rules
    "SpecialCharNotAllowedEntry",
    "StringCheckRuleBlob",
    "StringCheckCondition",
    "ConditionalValidationRuleBlob",
    "ConditionalCondition",
    "ConditionalIfPredicate",
    "ConditionalThen",
    "ConditionalThenValidation",
    # dataset-transformation-rules
    "DatasetRuleBlob",
    "DatasetAggregationRule",
    "DatasetDateOperationRule",
]
