# ============================================================================
# Deliverable 3 - Validation Rule Identification (Sprint 3). REUSABLE.
# Controlled vocabularies for the validation-analysis layer.
#
# Deliverable 3 ONLY IDENTIFIES which validation rules a future input file must
# satisfy - it never enforces them (the existing Java/API engine does that).
# These enums are the stable tokens the analyzer emits so Deliverable 4
# (Validation IR -> exact validation JSON) can branch on fixed values instead
# of free-form strings.
# ============================================================================
"""
validation_analysis.enums
==========================
Controlled vocabularies for validation rule identification.

* :class:`SupportedValidation` - the CLOSED catalogue of validation kinds the
  existing API can represent (derived from the ``validation-rules`` contract,
  ``documents_plan/contract_analysis/05_validation_rules_key_analysis.txt``).
  Every identified requirement is classified as exactly one of these (when
  supported) or flagged unsupported.
* :class:`SupportStatus` - whether an identified requirement maps onto a
  supported API validation (Sprint 3 tasks 20/21).
* :class:`RequirementSource` - how the requirement was inferred, for
  explainability and auditing.

All members are string-valued (``str, Enum``) so they serialize to stable
tokens in JSON and pipeline dumps.
"""
from __future__ import annotations

from enum import Enum


class SupportedValidation(str, Enum):
    """Closed catalogue of validation kinds supported by the existing API.

    Each member corresponds to one constraint expressible in a
    ``validation-rules`` object (contract file 5). The string value is the
    stable internal token; the exact contract field/keyword each maps to lives
    in :mod:`validation_analysis.catalog`.
    """

    # -- Presence / cardinality --------------------------------------------
    MANDATORY = "mandatory"                       # isMandatory = "Y"
    UNIQUE = "unique"                             # uniqueValueInColumn = "Y"

    # -- Type -------------------------------------------------------------
    DATA_TYPE = "data_type"                       # dataType (Text/Number/Date/...)

    # -- Length / range ----------------------------------------------------
    MIN_LENGTH = "min_length"                     # minLengthAllowed
    MAX_LENGTH = "max_length"                     # maxLengthAllowed
    MIN_VALUE = "min_value"                       # minValue (numeric)
    MAX_VALUE = "max_value"                       # maxValue (numeric)

    # -- Format / pattern --------------------------------------------------
    DATE_FORMAT = "date_format"                   # dataType=Date + dataFormat mask
    NUMERIC_FORMAT = "numeric_format"             # dataType=Number + dataFormat spec
    EMAIL_FORMAT = "email_format"                 # dataType=Email
    REGEX_PATTERN = "regex_pattern"               # dataType=Regex + dataFormat regex

    # -- Allowed values ----------------------------------------------------
    LIST_OF_VALUES = "list_of_values"             # lovCheckType / lovValidationRequired

    # -- Character-level ---------------------------------------------------
    SPECIAL_CHAR_NOT_ALLOWED = "special_char_not_allowed"  # specialCharNotAllowed

    # -- Rich string / cross-field ----------------------------------------
    STRING_CHECK = "string_check"                 # stringCheckRule
    CONDITIONAL = "conditional"                   # conditionalValidationRule

    # -- Error-file echo ---------------------------------------------------
    REQUIRED_IN_ERROR_FILE = "required_in_error_file"  # columnRequiredInErrorFile


class SupportStatus(str, Enum):
    """Whether an identified requirement maps to a supported API validation.

    Sprint 3 task 20 (mark supported) and task 21 (detect unsupported).
    """

    SUPPORTED = "supported"       # a matching exact API validation exists
    UNSUPPORTED = "unsupported"   # no representable API validation -> flagged


class RequirementSource(str, Enum):
    """How a validation requirement was inferred (for explainability).

    The analyzer is fully deterministic (no LLM); this records which signal in
    the canonical model produced the requirement.
    """

    STRUCTURE = "structure"           # column name/position/count (tasks 3-5)
    NULLABILITY = "nullability"       # null/non-null counts (tasks 6-9)
    DATA_TYPE = "data_type"           # detected logical type (task 10)
    FORMAT = "format"                 # detected value-format pattern (tasks 11-14)
    CARDINALITY = "cardinality"       # unique/duplicate ratios (tasks 15, 14-LOV)
    LENGTH = "length"                 # observed string-length bounds (task 16)
    VALUE_RANGE = "value_range"       # observed numeric min/max (task 12)
    PATTERN = "pattern"               # regex/character patterns (task 17)
    LLM = "llm"                       # domain-knowledge detection by the LLM detector


__all__ = [
    "SupportedValidation",
    "SupportStatus",
    "RequirementSource",
]
