# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure (task 21).
# Pydantic v2 data models for the exact DVTS payroll configuration JSON contract.
#
# These models reproduce the API contract VERBATIM: exact key names, exact key
# CASING (including kebab-case section names and intentional source typos such
# as ``isSkipValidaitons`` and ``aggrigation``), exact per-section key ORDER,
# string-typed numerics (``minLengthAllowed`` et al.), and the ``"<null>"``
# string sentinel. All facts are taken from the authoritative measured analyses
# in ``documents_plan/contract_analysis`` (files 1-5, over 692 sample files).
#
# FIDELITY
# --------
# * Field definition order == the stored key order for each section (Pydantic v2
#   preserves declaration order in ``model_dump``). ``validation-rules`` is
#   declared alphabetically because that is how the API stores it.
# * ``populate_by_name=True`` + ``alias`` map kebab-case section keys.
# * ``extra="forbid"`` on the 5 section objects strictly enforces the fixed key
#   set (the corpus shows no extra/missing keys), supporting contract validation.
# * Nested JSON-string fields are typed via ``contract.blobs`` and round-trip
#   through :func:`~contract.blobs.parse_blob` / :func:`~contract.blobs.dump_blob`.
# ============================================================================
"""
contract.models
===============
Exact JSON contract models.

Top-level shape (identical in every file)::

    ConfigurationContract
      ├── data: ContractData
      │     ├── column-relations:             [ColumnRelation]
      │     ├── configuration-data:           ConfigurationData
      │     ├── dataset-transformation-rules: [DatasetTransformationRule]
      │     ├── transformation-rules:         [TransformationRule]
      │     └── validation-rules:             [ValidationRule]
      ├── error:   None
      ├── message: None
      └── status:  "success"
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
)

from contract.blobs import (
    ConditionalValidationRuleBlob,
    DataExclusionEntry,
    DataRuleEntry,
    DataTransformationConfigEntry,
    DatasetRuleBlob,
    DefaultValueBlob,
    FilesInfoEntry,
    InputRuleEntry,
    OutputFileRuleEntry,
    OutputRuleEntry,
    RawBlob,
    SpecialCharNotAllowedEntry,
    SpecialCharRemovalEntry,
    StringCheckRuleBlob,
    dump_blob,
    parse_blob,
)
from contract.enums import ContractDataType, ValidationRuleType

# ---------------------------------------------------------------------------
# Blob field type factories
# ---------------------------------------------------------------------------
# Each embedded JSON-string field is declared as ``object`` carrying a
# BeforeValidator (parse the stored string into typed models) and a
# PlainSerializer (re-emit the JSON string). ``when_used="always"`` makes both
# ``model_dump`` and ``model_dump_json`` yield the contract's string form.


def _blob_type(model: type[BaseModel]):
    """Annotated type for a blob field whose content is ``model`` or ``[model]``."""

    return Annotated[
        Optional[object],
        BeforeValidator(lambda v, _m=model: parse_blob(v, _m)),
        PlainSerializer(dump_blob, return_type=Optional[str], when_used="always"),
    ]


def _parse_uuid_array(value: Any) -> Any:
    """Parse a ``dependsOn`` style JSON array-of-uuids string into ``list[str]``."""

    if value is None or isinstance(value, (list, RawBlob)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return RawBlob(raw=value)
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return RawBlob(raw=value)
    return value


def _dump_uuid_array(value: Any) -> Optional[str]:
    """Serialize a ``dependsOn`` ``list[str]`` back to a JSON-array string."""

    if value is None:
        return None
    if isinstance(value, RawBlob):
        return value.raw
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


UuidArrayBlob = Annotated[
    Optional[object],
    BeforeValidator(_parse_uuid_array),
    PlainSerializer(_dump_uuid_array, return_type=Optional[str], when_used="always"),
]


class _ContractSection(BaseModel):
    """Base for the 5 top-level section objects: strict, fixed key set."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------------
# 1. column-relations  (17 keys, fixed order) - file 1
# ---------------------------------------------------------------------------
class ColumnRelation(_ContractSection):
    """One output column definition. ``uuid`` is the cross-section anchor.

    Every other section references a column by :attr:`uuid` (NOT :attr:`id`).
    """

    id: Optional[int] = Field(None, description="Backend surrogate PK (server-generated)")
    bu: str = Field(..., description="Business unit (identity triple)")
    platform: str = Field(..., description="Destination platform (identity triple)")
    dataCategory: str = Field(..., description="Business use-case (identity triple)")
    sourceKey: Optional[str] = Field(None, description="Source tab/line key; null for derived columns")
    columnName: str = Field(..., description="Logical/business column name")
    position: int = Field(..., description="1-based ordinal position within its sheet/scope")
    aliasName: str = Field(..., description="Alternate display name (defaults to columnName)")
    uuid: str = Field(..., description="Stable column identifier (UUID v4) - the anchor")
    required: str = Field(..., description="'Y'/'N' - column mandatory in the dataset")
    columnRequiredInErrorFile: Optional[str] = Field(None, description="'Y'/'N'/null - echo to error file")
    useremail: str = Field(..., description="Audit: owner email")
    userrole: str = Field(..., description="Audit: owner role")
    path: Optional[str] = Field(None, description="Navigation path for nested/XML sources")
    dataExclusionRules: _blob_type(DataExclusionEntry) = Field(  # type: ignore[valid-type]
        None, description="Sentinel-value exclusions (JSON-string array)"
    )
    dataType: ContractDataType = Field(..., description="Logical data type (Text/Number/Date/Email/Dropdown/Regex)")
    format: Optional[str] = Field(None, description="Date mask / regex / number-spec backing the dataType")


# ---------------------------------------------------------------------------
# 2. configuration-data  (18 keys, fixed order) - file 2
# ---------------------------------------------------------------------------
class ConfigurationData(_ContractSection):
    """File/dataset-level header of the whole configuration."""

    appCode: Optional[str] = Field(None, description="Hosting app/tenant ('ahub-*')")
    bu: str = Field(..., description="Business unit (identity triple)")
    dataCategory: str = Field(..., description="Business use-case (identity triple)")
    dataRules: _blob_type(DataRuleEntry) = Field(  # type: ignore[valid-type]
        None, description="Dataset filter/group/aggregate (JSON-string array)"
    )
    filesInfo: _blob_type(FilesInfoEntry) = Field(  # type: ignore[valid-type]
        None, description="Per-input-source parse rules (JSON-string array)"
    )
    id: Optional[int] = Field(None, description="Backend configuration PK (server-generated)")
    inputRules: _blob_type(InputRuleEntry) = Field(  # type: ignore[valid-type]
        None, description="Dataset graph + joins (JSON-string array)"
    )
    outputFileRules: _blob_type(OutputFileRuleEntry) = Field(  # type: ignore[valid-type]
        None, description="Output file/sheet/template spec (JSON-string array)"
    )
    outputRules: _blob_type(OutputRuleEntry) = Field(  # type: ignore[valid-type]
        None, description="Output row ordering/sort (JSON-string array)"
    )
    platform: str = Field(..., description="Destination platform (identity triple)")
    processSteps: Optional[Any] = Field(None, description="Reserved - always null")
    processType: Optional[str] = Field(None, description="'chunks'/'in_memory' execution strategy")
    source: Optional[str] = Field(None, description="Upstream capture tool (DataStudio/MyDC/RPA)")
    subDataCategory: Optional[Any] = Field(None, description="Reserved - always null")
    targetLocation: Optional[str] = Field(None, description="Destination kind (SFG/SharePoint/SharedDrive)")
    targetPath: Optional[str] = Field(None, description="Concrete output folder path")
    useremail: Optional[str] = Field(None, description="Audit: owner email")
    userrole: Optional[str] = Field(None, description="Audit: owner role")


# ---------------------------------------------------------------------------
# 3. dataset-transformation-rules  (11 keys, fixed order) - file 3
# ---------------------------------------------------------------------------
class DatasetTransformationRule(_ContractSection):
    """A dataset-level derived field (rare: 14 rows across the whole corpus)."""

    id: Optional[int] = Field(None, description="Local per-file sequence id (100..300)")
    bu: str = Field(..., description="Business unit (identity triple)")
    platform: str = Field(..., description="Destination platform (identity triple)")
    dataCategory: str = Field(..., description="Business use-case (identity triple)")
    dataSetId: str = Field(..., description="Target dataset uuid (from inputRules/dataRules)")
    fieldName: str = Field(..., description="Human name of the derived field")
    fieldType: Optional[str] = Field(None, description="'TRANSFORMATION' or null")
    fieldUUID: str = Field(..., description="Stable id of the new derived field (preserve)")
    rule: _blob_type(DatasetRuleBlob) = Field(  # type: ignore[valid-type]
        ..., description="Computation: aggrigation{cnt} | date_operations{...} (JSON-string)"
    )
    useremail: str = Field(..., description="Audit: owner email")
    userrole: str = Field(..., description="Audit: owner role")


# ---------------------------------------------------------------------------
# 4. transformation-rules  (20 keys, fixed order) - file 4
# ---------------------------------------------------------------------------
class TransformationRule(_ContractSection):
    """Column-level mapping + transformation (one row per target column)."""

    id: Optional[int] = Field(None, description="Backend row PK (server-generated)")
    bu: str = Field(..., description="Business unit (identity triple)")
    platform: str = Field(..., description="Destination platform (identity triple)")
    dataCategory: str = Field(..., description="Business use-case (identity triple)")
    subDataCategory: Optional[Any] = Field(None, description="Reserved - always null")
    sourceColumnName: str = Field(..., description="SOURCE column uuid (despite the '...Name' suffix)")
    dataType: Optional[ContractDataType] = Field(None, description="Target type; null => inherit from source")
    dataFormat: Optional[str] = Field(None, description="Target format (date mask / regex / number spec)")
    targetColumnName: str = Field(..., description="Output column NAME")
    targetFileName: str = Field(..., description="'<null>' sentinel | dataSetId uuid | JSON object string")
    columnSequence: Optional[int] = Field(None, description="Output ordering override; null => natural order")
    defaultValue: _blob_type(DefaultValueBlob) = Field(  # type: ignore[valid-type]
        None, description="const/date/split/concat/conditional value (JSON-string)"
    )
    dataTransformationRules: _blob_type(DataTransformationConfigEntry) = Field(  # type: ignore[valid-type]
        None, description="Per-column transform ops (JSON-string array)"
    )
    specialCharToBeRemoved: _blob_type(SpecialCharRemovalEntry) = Field(  # type: ignore[valid-type]
        None, description="Characters to strip (JSON-string array; may be ''/'[]')"
    )
    lovCheckType: Optional[str] = Field(None, description="LOV list name (dropdown lookup)")
    transformationRequired: Optional[str] = Field(None, description="'Y'/'N'/null - transform on/off")
    lovValidationRequired: Optional[str] = Field(None, description="'Y'/'N'/null - enforce LOV")
    dependsOn: UuidArrayBlob = Field(  # type: ignore[valid-type]
        None, description="Prerequisite column uuids (JSON-string array)"
    )
    useremail: str = Field(..., description="Audit: owner email")
    userrole: str = Field(..., description="Audit: owner role")


# ---------------------------------------------------------------------------
# 5. validation-rules  (30 keys, ALPHABETICAL order as stored) - file 5
# ---------------------------------------------------------------------------
class ValidationRule(_ContractSection):
    """Per-column data-quality constraints (one row per validated column).

    Keys are declared alphabetically because the API stores this section's keys
    in alphabetical order (Sprint 2 "exact key order").
    """

    bu: str = Field(..., description="Business unit (identity triple)")
    columnRequiredInErrorFile: Optional[str] = Field(None, description="'Y'/'N'/null - echo to error file")
    conditionalValidationRule: _blob_type(ConditionalValidationRuleBlob) = Field(  # type: ignore[valid-type]
        None, description="if/then cross-field mandatory rules (JSON-string)"
    )
    dataCategory: str = Field(..., description="Business use-case (identity triple)")
    dataExclusionRules: _blob_type(DataExclusionEntry) = Field(  # type: ignore[valid-type]
        None, description="Sentinel-as-empty values (JSON-string array)"
    )
    dataFormat: Optional[str] = Field(None, description="Pattern the value must match (date/regex/number)")
    dataTransformationRules: _blob_type(DataTransformationConfigEntry) = Field(  # type: ignore[valid-type]
        None, description="Normalise-before-check ops (JSON-string array)"
    )
    dataType: ContractDataType = Field(..., description="Validated type (selects meaningful constraints)")
    dependsOn: UuidArrayBlob = Field(  # type: ignore[valid-type]
        None, description="Prerequisite column uuids (JSON-string array)"
    )
    errorMessageInfo: Optional[str] = Field(None, description="Custom user-facing error text (plain string)")
    id: Optional[int] = Field(None, description="Backend row PK (server-generated)")
    isMandatory: Optional[str] = Field(None, description="'Y'/'N'/null - value must be non-empty")
    isSkipValidaitons: bool = Field(False, description="Master skip switch (typo key preserved; always false)")
    lovCheckType: Optional[str] = Field(None, description="LOV list name (dropdown validation)")
    lovValidationRequired: Optional[str] = Field(None, description="'Y'/'N'/null - enforce LOV")
    maxLengthAllowed: Optional[str] = Field(None, description="Max string length (STRING integer)")
    maxValue: Optional[str] = Field(None, description="Numeric ceiling (STRING number)")
    minLengthAllowed: Optional[str] = Field(None, description="Min string length (STRING integer)")
    minValue: Optional[str] = Field(None, description="Numeric floor (STRING number)")
    platform: str = Field(..., description="Destination platform (identity triple)")
    sourceColumn: str = Field(..., description="Validated column uuid (the key link)")
    sourceColumnName: Optional[Any] = Field(None, description="Reserved - always null (use sourceColumn)")
    specialCharNotAllowed: _blob_type(SpecialCharNotAllowedEntry) = Field(  # type: ignore[valid-type]
        None, description="[{character,min,max}] per-char rule (JSON-string array)"
    )
    specialCharToBeRemoved: _blob_type(SpecialCharRemovalEntry) = Field(  # type: ignore[valid-type]
        None, description="Pre-clean characters (JSON-string array)"
    )
    stringCheckRule: _blob_type(StringCheckRuleBlob) = Field(  # type: ignore[valid-type]
        None, description="Equals/Contains/Ends/Regex/... assertions (JSON-string)"
    )
    subDataCategory: Optional[Any] = Field(None, description="Reserved - always null")
    transformationRequired: Optional[str] = Field(None, description="'Y'/'N'/null - transform in validation")
    uniqueValueInColumn: Optional[str] = Field(None, description="'Y'/'N'/null - no-duplicates flag")
    useremail: str = Field(..., description="Audit: owner email")
    userrole: str = Field(..., description="Audit: owner role")
    validationRuleType: ValidationRuleType = Field(..., description="'client' or 'CAM'")


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------
class ContractData(BaseModel):
    """The ``data`` object holding the 5 sections in their fixed order."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    column_relations: list[ColumnRelation] = Field(
        default_factory=list, alias="column-relations", description="Output column catalog"
    )
    configuration_data: ConfigurationData = Field(
        ..., alias="configuration-data", description="File/dataset-level header"
    )
    dataset_transformation_rules: list[DatasetTransformationRule] = Field(
        default_factory=list,
        alias="dataset-transformation-rules",
        description="Dataset-level derived fields",
    )
    transformation_rules: list[TransformationRule] = Field(
        default_factory=list, alias="transformation-rules", description="Column-level mapping/transform"
    )
    validation_rules: list[ValidationRule] = Field(
        default_factory=list, alias="validation-rules", description="Per-column constraints"
    )


class ConfigurationContract(BaseModel):
    """Root API envelope of a complete configuration document.

    Parse with either aliases (``column-relations``) or field names; emit the
    exact contract shape with ``model_dump(by_alias=True)`` /
    ``model_dump_json(by_alias=True)``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    data: ContractData = Field(..., description="The 5 configuration sections")
    error: Optional[Any] = Field(None, description="Always null on success")
    message: Optional[Any] = Field(None, description="Always null on success")
    status: str = Field("success", description="Envelope status, e.g. 'success'")

    @classmethod
    def model_validate_json_document(cls, document: str) -> "ConfigurationContract":
        """Parse a raw JSON document string into a contract instance."""

        return cls.model_validate(json.loads(document))

    def to_contract_dict(self) -> dict[str, Any]:
        """Return the exact contract ``dict`` (kebab-case keys, blob strings)."""

        return self.model_dump(by_alias=True)

    def to_contract_json(self, *, indent: Optional[int] = None) -> str:
        """Return the exact contract JSON document string."""

        return self.model_dump_json(by_alias=True, indent=indent)


__all__ = [
    "ColumnRelation",
    "ConfigurationData",
    "DatasetTransformationRule",
    "TransformationRule",
    "ValidationRule",
    "ContractData",
    "ConfigurationContract",
]
