# ============================================================================
# Deliverable 1 - Canonical Data Model. Controlled vocabularies (enums).
# REUSABLE. Aligns file/sheet/column/relationship classifications so the
# downstream Profiling / Rule-Identification / IR layers (Deliverables 3-6)
# consume stable, deterministic values instead of free-form strings.
# ============================================================================
"""
canonical.enums
===============
Controlled vocabularies for the canonical input/output analysis model.

These enums are intentionally string-valued (``str, Enum``) so that every
member serializes to a stable, human-readable token in the generated JSON
and in YAML pipeline dumps. Downstream deliverables branch on these values,
so treat the string values as part of the internal contract.
"""
from __future__ import annotations

from enum import Enum


class FileFormat(str, Enum):
    """Supported input/output file formats (Sprint 1, task 2).

    Only ``XLSX``/``XLS`` are fully implemented in this deliverable. The
    remaining formats are declared here so the parser registry and canonical
    model can already reference them; their parsers ship as skeletons pending
    post-POC implementation.
    """

    CSV = "csv"
    XLSX = "xlsx"
    XLS = "xls"
    JSON = "json"
    XML = "xml"
    TXT = "txt"
    PDF = "pdf"

    @classmethod
    def from_extension(cls, extension: str) -> "FileFormat":
        """Resolve a :class:`FileFormat` from a filename extension.

        Args:
            extension: File extension with or without a leading dot
                (e.g. ``".xlsx"`` or ``"xlsx"``). Case-insensitive.

        Raises:
            ValueError: If the extension does not map to a supported format.
        """
        normalized = extension.lower().lstrip(".")
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported file extension: '{extension}'")


class IoRole(str, Enum):
    """Whether a file participates as a known input or an expected output."""

    INPUT = "input"
    OUTPUT = "output"


class DataType(str, Enum):
    """Detected logical data type of a column (Sprint 1, task 11).

    This is the *logical* type used for validation reasoning, distinct from
    the raw pandas dtype string that is also retained on the column model.
    """

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    UNKNOWN = "unknown"


class DetectedFormat(str, Enum):
    """Recognizable value-format pattern within a column (Sprint 1, task 12).

    Formats are candidate validation requirements (e.g. a date pattern may
    become a date-format validation rule in Deliverables 3/4).
    """

    NONE = "none"
    DATE_ISO = "date_iso"                # YYYY-MM-DD
    DATE_US = "date_us"                  # MM/DD/YYYY
    DATE_EU = "date_eu"                  # DD/MM/YYYY
    DATE_COMPACT = "date_compact"        # YYYYMMDD (no separators)
    DATETIME_ISO = "datetime_iso"        # YYYY-MM-DDTHH:MM:SS
    INTEGER = "integer"                  # bare integers
    DECIMAL = "decimal"                  # fixed/floating decimals
    CURRENCY = "currency"                # currency-prefixed/suffixed numbers
    PERCENTAGE = "percentage"            # trailing '%'
    EMAIL = "email"
    UUID = "uuid"
    BOOLEAN = "boolean"
    UNKNOWN = "unknown"


class RelationshipType(str, Enum):
    """Cardinality classification between two related entities (task 21)."""

    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"
    UNKNOWN = "unknown"


class DerivationType(str, Enum):
    """How a target sheet is derived from one or more source sheets (task 14)."""

    SINGLE_SOURCE = "single_source"  # one input sheet -> one output sheet
    JOIN = "join"                    # join/merge of multiple input sheets
    UNION = "union"                  # append/concatenate
    AGGREGATION = "aggregation"      # aggregated from one/more sheets
    FILTER = "filter"                # subset/filter of a sheet
    UNKNOWN = "unknown"


class ColumnChangeType(str, Enum):
    """Nature of a source-to-target column relationship (tasks 15, 17-19).

    Distinguishes how a target column relates to the input structure so the
    transformation-identification layer (Deliverable 5) can reason about it.
    """

    DIRECT = "direct"        # same column carried through unchanged
    RENAMED = "renamed"      # same values, different target name
    DERIVED = "derived"      # computed/constructed from source column(s)
    ADDED = "added"          # present in output, absent in input (task 17)
    REMOVED = "removed"      # present in input, absent in output (task 18)
    UNKNOWN = "unknown"
