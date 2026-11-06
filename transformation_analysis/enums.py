# ============================================================================
# Deliverable 5 - Transformation Rule Identification (Sprint 5). REUSABLE.
# Controlled vocabularies for the transformation-analysis layer.
#
# Deliverable 5 ONLY IDENTIFIES which transformations are required to turn the
# known input into the known/expected output - it never executes them (the
# existing Java/API engine does that). These enums are the stable tokens the
# analyzer emits so Deliverable 6 (Transformation IR -> exact transformation
# JSON) can branch on fixed values instead of free-form strings.
# ============================================================================
"""
transformation_analysis.enums
==============================
Controlled vocabularies for transformation rule identification.

* :class:`SupportedTransformation` - the CLOSED catalogue of transformation
  operations the existing API can represent (derived from the
  ``transformation-rules`` and ``dataset-transformation-rules`` contract
  sections, ``documents_plan/contract_analysis/04_*`` and ``03_*``). Every
  identified transformation is classified as exactly one of these (when
  supported) or flagged unsupported.
* :class:`TransformationCategory` - how a target column relates to the input
  (direct / renamed / derived / transformed / multi-transform), mirroring
  Sprint 5 tasks 5-9.
* :class:`SupportStatus` - whether an identified transformation maps onto a
  supported API transformation (Sprint 5 tasks 15-16).
* :class:`TransformationSignal` - which comparison signal produced the
  requirement, for explainability and auditing.

All members are string-valued (``str, Enum``) so they serialize to stable
tokens in JSON and pipeline dumps.
"""
from __future__ import annotations

from enum import Enum


class SupportedTransformation(str, Enum):
    """Closed catalogue of transformation operations supported by the API.

    Each member corresponds to one operation expressible in a
    ``transformation-rules`` object (contract file 4) or a
    ``dataset-transformation-rules`` object (contract file 3). The string value
    is the stable internal token; the exact contract field / operation keyword
    each maps to lives in :mod:`transformation_analysis.catalog`.
    """

    # -- Mapping (column-to-column linkage, no value change) ---------------
    DIRECT_MAPPING = "direct_mapping"     # sourceColumnName=uuid, pass-through
    RENAME = "rename"                     # targetColumnName differs from source

    # -- Value transforms (dataTransformationRules configurations, file 4) --
    DATE_FORMAT = "date_format"           # date_format {source->target mask}
    TARGET_FORMAT = "target_format"       # target_format {targetType/Format}
    ARITHMETIC = "arithmetic"             # arithmetic_operations (ADD/MULT)
    LENGTH_FORMAT = "length_format"       # length_format (pad/truncate)
    TRIM = "trim"                         # trim {position}
    CASE_CONVERSION = "case_conversion"   # Case_Conversion {UpperCase/LowerCase}
    SPLIT = "split"                       # split_operations (substring)
    REMOVE_CHARACTERS = "remove_characters"  # specialCharToBeRemoved

    # -- Value generation (defaultValue families, file 4) ------------------
    DEFAULT_VALUE = "default_value"       # defaultValue constant
    FIELD_CONCAT = "field_concat"         # defaultValue.field_concat
    DATE_STAMP = "date_stamp"             # date_operations (current_date/time)
    CONDITIONAL = "conditional"           # defaultValue.conditional if/then/else

    # -- Controlled-list mapping -------------------------------------------
    LOV_MAPPING = "lov_mapping"           # lovCheckType (code/description list)

    # -- Dataset-level derived field (dataset-transformation-rules, file 3) -
    DATASET_AGGREGATION = "dataset_aggregation"  # aggrigation {cnt}
    DATASET_DATE_STAMP = "dataset_date_stamp"    # dataset date_operations


class TransformationCategory(str, Enum):
    """How a target column relates to the input structure (Sprint 5 tasks 5-9).

    Distinguishes the overall nature of a target column so the inventory and the
    Sprint 6 compiler can reason about it deterministically.
    """

    DIRECT = "direct"                  # pass-through copy (task 5)
    RENAMED = "renamed"                # same values, different name (task 6)
    DERIVED = "derived"                # generated/constructed, no 1:1 source (task 7)
    TRANSFORMED = "transformed"        # one value transform applied (task 8)
    MULTI_TRANSFORM = "multi_transform"  # chained/multiple transforms (task 9)


class SupportStatus(str, Enum):
    """Whether an identified transformation maps to a supported API operation.

    Sprint 5 task 15 (map to supported) and task 16 (detect unsupported).
    """

    SUPPORTED = "supported"       # a matching exact API transformation exists
    UNSUPPORTED = "unsupported"   # no representable API transformation -> flagged


class TransformationSignal(str, Enum):
    """Which deterministic comparison produced a transformation requirement.

    The analyzer is fully deterministic (no LLM); this records the signal in the
    canonical source/target comparison that produced the requirement.
    """

    RELATIONSHIP = "relationship"     # canonical source-to-target link (tasks 3-4,10-11)
    NAME = "name"                     # column name difference (rename)
    DATA_TYPE = "data_type"           # source vs target logical type shift
    FORMAT = "format"                 # date/number value-format shift
    LENGTH = "length"                 # observed string-length shift
    CASE = "case"                     # letter-case shift
    WHITESPACE = "whitespace"         # leading/trailing whitespace shift
    STRUCTURE = "structure"           # added/derived column (no 1:1 source)
    VALUE = "value"                   # value/content pattern


__all__ = [
    "SupportedTransformation",
    "TransformationCategory",
    "SupportStatus",
    "TransformationSignal",
]
