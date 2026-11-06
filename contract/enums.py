# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure.
# Controlled vocabularies (enums) for the DVTS payroll configuration contract.
#
# Every value below is a CLOSED vocabulary measured across all 692 sample JSON
# files (documents_plan/contract_analysis/*). Casing / spelling is preserved
# VERBATIM - including intentional source typos - because the existing API
# matches these tokens exactly (Sprint 2 tasks 5, 6 "exact key/keyword casing").
#
# Open-ended vocabularies (bu, platform, dataCategory, lovCheckType, appCode,
# emails, paths, targetPath, ...) are intentionally NOT enums: the corpus shows
# hundreds of values plus casing variants (e.g. "Payline"/"PAYLlNE",
# "DataStudio"/"Datastudio") that must round-trip untouched. Those stay plain
# ``str`` fields on the models.
# ============================================================================
"""
contract.enums
==============
Controlled keyword vocabularies for the exact JSON configuration contract.

These enums are string-valued (``str, Enum``) so each member serializes to the
exact token the API expects and so downstream deliverables (validation IR,
transformation IR, JSON compiler) can branch on stable values rather than
free-form strings.

Provenance
----------
All members are the *complete* observed value sets from the authoritative
analyses in ``documents_plan/contract_analysis``:

* ``ContractDataType``      -> file 1/4/5 ("dataType")
* ``ValidationRuleType``    -> file 5 ("validationRuleType")
* ``YesNoFlag``             -> Y/N/(null) flags across all files
* ``OutputFileType``        -> file 2 ("outputFileRules[].outputFileType")
* ``JoinRelationship``      -> file 2 ("inputRules[].references[].relationship")
* ``ProcessType``           -> file 2 ("processType")
* ``InputFileType``         -> file 2 ("filesInfo[].type")
* ``TransformationOp``      -> file 4 ("dataTransformationRules configurations")
* ``ArithmeticOperator``    -> file 4 ("arithmetic_operations")
* ``CaseConversion``        -> file 4 ("Case_Conversion")
* ``StringCheckConstraint`` -> file 5 ("stringCheckRule.conditions[].constraint")
* ``ConditionalConstraint`` -> file 4/5 (if/then constraints)
* ``LogicalOperator``       -> file 5 ("conditionalValidationRule ... operator")
* ``DatasetRuleKeyword``    -> file 3 ("rule")
"""
from __future__ import annotations

from enum import Enum


class ContractDataType(str, Enum):
    """Logical column data type. Complete set of 6 (file 1 KEY 16)."""

    TEXT = "Text"
    NUMBER = "Number"
    DATE = "Date"
    EMAIL = "Email"
    DROPDOWN = "Dropdown"
    REGEX = "Regex"


class ValidationRuleType(str, Enum):
    """Origin/scope of a validation rule. Exactly two values (file 5)."""

    CLIENT = "client"
    CAM = "CAM"


class YesNoFlag(str, Enum):
    """Three-state boolean-like flag stored as a 1-char string.

    Used by ``required``, ``isMandatory``, ``columnRequiredInErrorFile``,
    ``transformationRequired``, ``lovValidationRequired`` and
    ``uniqueValueInColumn``. The ``null`` third state is represented by the
    field being ``Optional`` (i.e. ``None``), never by an enum member.
    """

    YES = "Y"
    NO = "N"


class OutputFileType(str, Enum):
    """Output file kind (file 2, ``outputFileRules[].outputFileType``)."""

    XLSX = "xlsx"
    CSV = "csv"
    TXT = "txt"
    XML = "xml"
    PDF = "pdf"


class InputFileType(str, Enum):
    """Input source file kind (file 2, ``filesInfo[].type``)."""

    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"
    XML = "xml"
    TXT = "txt"
    MASTER = "master"
    FORM = "Form"
    MYDC = "MYDC"


class JoinRelationship(str, Enum):
    """Dataset join cardinality (file 2, ``inputRules[].references[]``)."""

    ONE_TO_ONE = "OnetoOne"
    ALL_TO_ALL = "AlltoAll"
    ALL_TO_ONE = "AlltoOne"
    MERGE = "Merge"


class ProcessType(str, Enum):
    """Engine execution strategy (file 2, ``processType``)."""

    CHUNKS = "chunks"
    IN_MEMORY = "in_memory"


class TransformationOp(str, Enum):
    """Per-column transform operation keys inside ``configurations`` (file 4).

    Preserve exact spelling/casing; the mix of snake_case and Pascal/mixed case
    (``Case_Conversion``) is intentional and required by the API.
    """

    DATE_FORMAT = "date_format"
    TARGET_FORMAT = "target_format"
    ARITHMETIC_OPERATIONS = "arithmetic_operations"
    LENGTH_FORMAT = "length_format"
    TRIM = "trim"
    CASE_CONVERSION = "Case_Conversion"
    SPLIT_OPERATIONS = "split_operations"
    REMOVE_CHARACTERS = "remove_characters"


class ArithmeticOperator(str, Enum):
    """Operators inside ``arithmetic_operations`` (file 4)."""

    ADDITION = "ADDITION"
    MULTIPLICATION = "MULTIPLICATION"


class CaseConversion(str, Enum):
    """Values for ``Case_Conversion.Case`` (file 4)."""

    UPPER = "UpperCase"
    LOWER = "LowerCase"


class StringCheckConstraint(str, Enum):
    """Constraints inside ``stringCheckRule.conditions[]`` (file 5).

    Complete set of 8. ``<null>`` is a literal sentinel token used by the API,
    not JSON null - preserve the angle brackets verbatim.
    """

    NULL = "<null>"
    CONTAINS = "Contains"
    ENDS = "Ends"
    EQUALS = "Equals"
    GREATER_THAN = "GreaterThan"
    LESS_THAN = "LessThan"
    NOT_EQUALS = "NotEquals"
    REGEX = "Regex"


class ConditionalConstraint(str, Enum):
    """Constraints inside ``if[]`` predicates.

    ``conditionalValidationRule`` (file 5) uses ``<notnull> / <null> / Equals``;
    ``defaultValue.conditional`` (file 4) uses lower-case ``equals / notequals``
    plus the same sentinels. Both families are represented here so validators
    can accept either casing where the contract legitimately uses it.
    """

    NOT_NULL = "<notnull>"
    NULL = "<null>"
    EQUALS = "Equals"
    NOT_EQUALS = "NotEquals"
    EQUALS_LOWER = "equals"
    NOT_EQUALS_LOWER = "notequals"


class LogicalOperator(str, Enum):
    """Boolean combiner for multi-predicate rules.

    ``conditionalValidationRule`` uses Title-case ``And``/``Or`` (file 5);
    ``stringCheckRule`` uses lower-case ``and`` (file 5). Both are kept so the
    exact stored casing round-trips.
    """

    AND = "And"
    OR = "Or"
    AND_LOWER = "and"
    OR_LOWER = "or"


class DatasetRuleKeyword(str, Enum):
    """Top-level keyword of a ``dataset-transformation-rules`` ``rule`` blob.

    Note the intentional source misspelling ``aggrigation`` (file 3) - the API
    expects that exact spelling.
    """

    AGGRIGATION = "aggrigation"
    DATE_OPERATIONS = "date_operations"


class AggregationOperation(str, Enum):
    """Operation inside a dataset ``aggrigation`` rule (file 3)."""

    COUNT = "cnt"


class DateOperationType(str, Enum):
    """``type`` inside ``date_operations`` (files 3, 4)."""

    CURRENT_DATE = "current_date"
    CURRENT_TIME = "current_time"


__all__ = [
    "AggregationOperation",
    "ArithmeticOperator",
    "CaseConversion",
    "ConditionalConstraint",
    "ContractDataType",
    "DateOperationType",
    "DatasetRuleKeyword",
    "InputFileType",
    "JoinRelationship",
    "LogicalOperator",
    "OutputFileType",
    "ProcessType",
    "StringCheckConstraint",
    "TransformationOp",
    "ValidationRuleType",
    "YesNoFlag",
]
