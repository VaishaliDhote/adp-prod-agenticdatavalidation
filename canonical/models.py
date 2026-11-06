# ============================================================================
# Deliverable 1 - Canonical Data Model (tasks 22-26). REUSABLE.
# Pydantic v2 models representing files, sheets, columns, data characteristics,
# and source-to-target relationships. This unified model is the consistent
# input for Profiling, Rule Identification, IR creation, and JSON compilation
# in later deliverables. Every entity carries a stable UUID so downstream IR /
# JSON generation can reference files/sheets/columns deterministically.
# ============================================================================
"""
canonical.models
================
The canonical input/output analysis model.

Model hierarchy::

    CanonicalAnalysisModel            # task 22 - unified top-level model
      ├── input_files:  [CanonicalFile]   # task 23
      │     └── sheets: [CanonicalSheet]  # task 24
      │           └── columns: [CanonicalColumn]  # task 25
      ├── output_files: [CanonicalFile]
      └── relationships: [SourceTargetRelationship]  # task 26

Design notes
------------
* Pydantic v2 ``BaseModel`` is used (matching ``schemas/requests.py``) for
  validation and clean JSON/dict serialization.
* UUIDs are generated deterministically at construction time via
  ``default_factory`` and are the canonical reference mechanism (tasks 16, 26).
* Ordering is explicit: sheets carry ``order_index`` and columns carry
  ``position`` so original file ordering is never lost (tasks 7-9, 20).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from canonical.enums import (
    ColumnChangeType,
    DataType,
    DerivationType,
    DetectedFormat,
    FileFormat,
    IoRole,
    RelationshipType,
)


def _new_uuid() -> str:
    """Generate a fresh hex UUID used as a stable canonical identifier."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Column-level model (task 25)
# ---------------------------------------------------------------------------
class ColumnStats(BaseModel):
    """Basic per-column statistics captured during ingestion (task 10)."""

    non_null_count: int = Field(0, ge=0, description="Number of non-null cells")
    null_count: int = Field(0, ge=0, description="Number of null/empty cells")
    unique_count: int = Field(0, ge=0, description="Number of distinct values")
    min_length: Optional[int] = Field(
        None, description="Minimum string length observed (string columns)"
    )
    max_length: Optional[int] = Field(
        None, description="Maximum string length observed (string columns)"
    )
    min_numeric: Optional[float] = Field(
        None,
        description=(
            "Minimum numeric value observed across the WHOLE column (numeric "
            "columns only). Lets downstream analysis emit a reliable minValue "
            "bound instead of one derived from a small sample."
        ),
    )
    max_numeric: Optional[float] = Field(
        None,
        description=(
            "Maximum numeric value observed across the WHOLE column (numeric "
            "columns only). Companion to min_numeric for a reliable maxValue."
        ),
    )
    max_integer_digits: Optional[int] = Field(
        None,
        description=(
            "Largest number of integer-part digits observed across the WHOLE "
            "column (numeric columns). Drives the numeric dataFormat mask."
        ),
    )
    max_decimal_digits: Optional[int] = Field(
        None,
        description=(
            "Largest number of fractional digits observed across the WHOLE "
            "column (numeric columns). Drives the numeric dataFormat mask."
        ),
    )
    inferred_pattern: Optional[str] = Field(
        None,
        description=(
            "Deterministically-derived anchored regex the WHOLE column conforms "
            "to (e.g. '^[0-9]{7}-[0-9]{2,3}$'), computed at ingestion when every "
            "value shares one structural shape. Lets analysis emit a Regex "
            "dataType + dataFormat instead of an unconstrained Text column."
        ),
    )
    distinct_values: Optional[list[Any]] = Field(
        None,
        description=(
            "Complete set of distinct non-null values, captured during "
            "ingestion only for low-cardinality columns. Populated when the "
            "column is a list-of-values / dropdown candidate so downstream "
            "analysis can emit the full allowedValues set (not just a sample)."
        ),
    )


class CanonicalColumn(BaseModel):
    """Canonical representation of a single column (Sprint 1, task 25).

    Includes the UUID, name, position, detected logical type and format, the
    raw pandas dtype, statistics, and a small set of sample values used by
    downstream profiling and relationship detection.
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable column identifier")
    name: str = Field(..., description="Column name as it appears in the sheet")
    position: int = Field(..., ge=0, description="Zero-based column index/order (tasks 8-9)")

    data_type: DataType = Field(
        DataType.UNKNOWN, description="Detected logical data type (task 11)"
    )
    detected_format: DetectedFormat = Field(
        DetectedFormat.NONE, description="Detected value-format pattern (task 12)"
    )
    raw_dtype: str = Field("object", description="Underlying pandas/numpy dtype string")

    stats: ColumnStats = Field(default_factory=ColumnStats, description="Column statistics")
    sample_values: list[Any] = Field(
        default_factory=list, description="Representative non-null sample values"
    )

    # Back-reference to the owning sheet (populated by the builder).
    sheet_uuid: Optional[str] = Field(
        None, description="UUID of the parent sheet for deterministic references"
    )


# ---------------------------------------------------------------------------
# Sheet-level model (task 24)
# ---------------------------------------------------------------------------
class CanonicalSheet(BaseModel):
    """Canonical representation of a single sheet (Sprint 1, task 24).

    Preserves the sheet's original ordering within its parent workbook
    (``order_index``, task 7) and the ordered collection of its columns.
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable sheet identifier")
    name: str = Field(..., description="Sheet name")
    order_index: int = Field(..., ge=0, description="Zero-based sheet order in the file (task 7)")

    row_count: int = Field(0, ge=0, description="Number of data rows in the sheet")
    columns: list[CanonicalColumn] = Field(
        default_factory=list, description="Ordered list of columns (task 8)"
    )

    # Back-reference to the owning file (populated by the builder).
    file_uuid: Optional[str] = Field(
        None, description="UUID of the parent file for deterministic references"
    )

    def column_names(self) -> list[str]:
        """Return column names in their original order."""
        return [c.name for c in self.columns]


# ---------------------------------------------------------------------------
# File-level model (task 23)
# ---------------------------------------------------------------------------
class CanonicalFile(BaseModel):
    """Canonical representation of a single file (Sprint 1, task 23).

    Captures identity, format, input/output role, size, arbitrary parser
    metadata, and the ordered collection of sheets contained in the file.
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable file identifier")
    name: str = Field(..., description="File name (basename)")
    path: str = Field(..., description="Filesystem path the file was ingested from")
    file_format: FileFormat = Field(..., description="Detected file format (task 2)")
    io_role: IoRole = Field(..., description="Input or expected-output role")

    size_bytes: Optional[int] = Field(None, ge=0, description="File size in bytes (task 5)")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional parser/file-level metadata"
    )
    sheets: list[CanonicalSheet] = Field(
        default_factory=list, description="Ordered list of sheets (task 4)"
    )

    def sheet_names(self) -> list[str]:
        """Return sheet names in their original order (task 7)."""
        return [s.name for s in self.sheets]


# ---------------------------------------------------------------------------
# Source-to-target relationship model (task 26)
# ---------------------------------------------------------------------------
class EntityRef(BaseModel):
    """A UUID-based reference to a file/sheet/column in the canonical model.

    Names are carried alongside the UUIDs purely for human-readable output;
    the UUIDs are the authoritative deterministic references (task 16).
    """

    file_uuid: Optional[str] = Field(None, description="Referenced file UUID")
    file_name: Optional[str] = Field(None, description="Referenced file name")
    sheet_uuid: Optional[str] = Field(None, description="Referenced sheet UUID")
    sheet_name: Optional[str] = Field(None, description="Referenced sheet name")
    column_uuid: Optional[str] = Field(None, description="Referenced column UUID")
    column_name: Optional[str] = Field(None, description="Referenced column name")


class SourceTargetRelationship(BaseModel):
    """A structured source-to-target relationship (Sprint 1, task 26).

    Represents relationships between source (input) and target (output)
    files, sheets, and columns. The ``level`` field indicates the granularity
    ("file", "sheet", or "column"). Cardinality and derivation classifications
    let downstream transformation reasoning operate on consistent identifiers
    (tasks 13-21).
    """

    uuid: str = Field(default_factory=_new_uuid, description="Stable relationship identifier")
    level: str = Field(..., description="Granularity: 'file', 'sheet', or 'column'")

    sources: list[EntityRef] = Field(
        default_factory=list, description="One or more source (input) references"
    )
    targets: list[EntityRef] = Field(
        default_factory=list, description="One or more target (output) references"
    )

    relationship_type: RelationshipType = Field(
        RelationshipType.UNKNOWN, description="Cardinality classification (task 21)"
    )
    derivation_type: Optional[DerivationType] = Field(
        None, description="Sheet derivation classification (task 14)"
    )
    change_type: Optional[ColumnChangeType] = Field(
        None, description="Column change classification (tasks 15, 17-19)"
    )

    join_keys: list[dict[str, EntityRef]] = Field(
        default_factory=list, description="Identified join key column pairs, if any"
    )
    confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Deterministic-heuristic confidence score"
    )
    explanation: Optional[str] = Field(
        None, description="How the relationship was inferred"
    )


# ---------------------------------------------------------------------------
# Top-level unified analysis model (task 22)
# ---------------------------------------------------------------------------
class CanonicalAnalysisModel(BaseModel):
    """Unified canonical input/output analysis model (Sprint 1, task 22).

    Ties files, sheets, columns, data characteristics, and source-to-target
    relationships into a single serializable model that becomes the consistent
    input for later deliverables.
    """

    session_id: Optional[str] = Field(None, description="Owning session identifier")
    input_files: list[CanonicalFile] = Field(
        default_factory=list, description="Known input files"
    )
    output_files: list[CanonicalFile] = Field(
        default_factory=list, description="Expected output files"
    )
    relationships: list[SourceTargetRelationship] = Field(
        default_factory=list, description="Source-to-target relationships (task 26)"
    )

    # -- Convenience lookup helpers -----------------------------------------
    def all_files(self) -> list[CanonicalFile]:
        """Return every file (inputs followed by outputs)."""
        return [*self.input_files, *self.output_files]

    def find_file(self, uuid: str) -> Optional[CanonicalFile]:
        """Return the file with the given UUID, or ``None``."""
        return next((f for f in self.all_files() if f.uuid == uuid), None)

    def find_sheet(self, uuid: str) -> Optional[CanonicalSheet]:
        """Return the sheet with the given UUID, or ``None``."""
        for file in self.all_files():
            for sheet in file.sheets:
                if sheet.uuid == uuid:
                    return sheet
        return None

    def find_column(self, uuid: str) -> Optional[CanonicalColumn]:
        """Return the column with the given UUID, or ``None``."""
        for file in self.all_files():
            for sheet in file.sheets:
                for column in sheet.columns:
                    if column.uuid == uuid:
                        return column
        return None
