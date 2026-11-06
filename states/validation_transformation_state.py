# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 6 - IR precursor: column/sheet mapping + result state. REUSABLE.
# Rework: repurpose into formal source/target references for the Transformation IR
# (tasks 3-4). Not a Python execution/validation engine.
# ============================================================================
from __future__ import annotations

from typing import List, TypedDict

from .filestate import FileState
from .final_analysis_state import ColumnMapping, SheetMapping


class ValidationResult(TypedDict, total=False):
    """Result of validating a transformation for a specific output column."""

    sheet: str
    column: str
    response: str  # LLM response validating the transformation


class ValidationTransformationState(TypedDict, total=False):
    """State storing the results of validation transformations.

    This includes the original file state, mappings, and validation results per column.
    """

    # The raw structural information about files/sheets/columns
    file_state: FileState

    # Column mappings from analysis
    column_mappings: List[ColumnMapping]

    # Sheet mappings from analysis
    sheet_mappings: List[SheetMapping]

    # Validation results for each output column
    validation_results: List[ValidationResult]