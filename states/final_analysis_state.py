# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - Canonical Input/Output & Source-to-Target Model. REUSABLE.
# Rework: formalize source-to-target relationship model and column UUID mapping
# to feed the Validation/Transformation IR (Deliverables 4/6).
# ============================================================================
from __future__ import annotations

from typing import Dict, List, Literal, Optional, TypedDict

from .filestate import FileState


class ColumnInputRef(TypedDict, total=False):
    """Reference to an input column that contributes to an output column."""

    file_name: str
    sheet_name: str
    column_name: str


class ColumnMapping(TypedDict, total=False):
    """Semantic mapping between a single output column and one or more input columns.

    This is the result of your column-mapping step (LLM-assisted).
    """

    # Output side
    output_file: str
    output_sheet: str
    output_column: str

    # One or more contributing input columns
    inputs: List[ColumnInputRef]

    # Optional description of how the inputs are combined (sum, join key, etc.)
    transformation: Optional[str]

    # Optional confidence score from 0.0 to 1.0
    confidence: Optional[float]


class SheetMapping(TypedDict, total=False):
    """How one output sheet is derived from multiple input sheets."""

    output_file: str
    output_sheet: str

    # All input sheets that contribute to this output sheet
    input_sheets: List[ColumnInputRef]

    # High-level description inferred from column mappings and file state
    derivation_type: Optional[Literal[
        "single_source",  # one input sheet
        "join",           # join/merge of multiple input sheets
        "union",          # append/concatenate
        "aggregation",    # aggregated from one/more sheets
        "filter",         # subset/filter of a sheet
        "unknown",
    ]]

    description: Optional[str]


class SheetRelationship(TypedDict, total=False):
    """Relationship classification between two sheets based on join/usage."""

    left_file: str
    left_sheet: str

    right_file: str
    right_sheet: str

    # Cardinality between the two sheets
    relationship_type: Literal[
        "one_to_one",
        "one_to_many",
        "many_to_one",
        "many_to_many",
        "unknown",
    ]

    # Optional join key columns, if identified
    join_keys: Optional[List[Dict[str, ColumnInputRef]]]

    # Optional explanation of how this relationship was inferred
    explanation: Optional[str]


class FinalAnalysisState(TypedDict, total=False):
    """Overall analysis state tying together file state and mapping results.

    This can be passed into / out of your workflow and serialized as JSON.
    """

    # The raw structural information about files/sheets/columns
    file_state: FileState

    # Step 1 result: semantic column mappings
    column_mappings: List[ColumnMapping]

    # Step 2 result: inferred sheet mappings (which sheets form each output sheet)
    sheet_mappings: List[SheetMapping]

    # Step 3 result: relationships and cardinalities between sheets
    relationships: List[SheetRelationship]


# Small example FileState for understanding how analysis ties back to files.
example_file_state: FileState = {
    "input_files": {
        "input_file_1.xlsx": {
            "path": "data/input_files/<session_id>/input_file_1.xlsx",
            "io_type": "input",
            "sheets": {
                "sheet_1": {
                    "df_ref": None,
                    "row_count": 3,
                    "columns": {
                        "col_1": {
                            "name": "col_1",
                            "dtype": "int64",
                            "stats": {
                                "non_null_count": 3,
                                "unique_count": 3,
                            },
                        },
                        "col_2": {
                            "name": "col_2",
                            "dtype": "object",
                            "stats": {
                                "non_null_count": 3,
                                "unique_count": 2,
                            },
                        },
                    },
                    "sheet_summary": "sheet_1 contains basic example data.",
                }
            },
        }
    },
    "output_files": {
        "output_file_1.xlsx": {
            "path": "data/output_files/<session_id>/output_file_1.xlsx",
            "io_type": "output",
            "sheets": {
                "output_sheet_1": {
                    "df_ref": None,
                    "row_count": 3,
                    "columns": {
                        "out_col_1": {
                            "name": "out_col_1",
                            "dtype": "int64",
                            "stats": {
                                "non_null_count": 3,
                                "unique_count": 3,
                            },
                        },
                        "out_col_2": {
                            "name": "out_col_2",
                            "dtype": "float64",
                            "stats": {
                                "non_null_count": 3,
                                "unique_count": 3,
                            },
                        },
                    },
                    "sheet_summary": "output_sheet_1 contains derived example metrics.",
                }
            },
        }
    },
}

# Small example: how one output sheet's columns map to input columns.
example_column_mappings: List[ColumnMapping] = [
    {
        "output_file": "output_file_1.xlsx",
        "output_sheet": "output_sheet_1",
        "output_column": "out_col_1",
        "inputs": [
            {
                "file_name": "input_file_1.xlsx",
                "sheet_name": "sheet_1",
                "column_name": "col_1",
            }
        ],
        "transformation": "Direct copy of col_1.",
        "confidence": 0.99,
    },
    {
        "output_file": "output_file_1.xlsx",
        "output_sheet": "output_sheet_1",
        "output_column": "out_col_2",
        "inputs": [
            {
                "file_name": "input_file_1.xlsx",
                "sheet_name": "sheet_1",
                "column_name": "col_2",
            }
        ],
        "transformation": "Derived numeric value from col_2.",
        "confidence": 0.9,
    },
]

# Small example: which input sheets form each output sheet.
example_sheet_mappings: List[SheetMapping] = [
    {
        "output_file": "output_file_1.xlsx",
        "output_sheet": "output_sheet_1",
        "input_sheets": [
            {
                "file_name": "input_file_1.xlsx",
                "sheet_name": "sheet_1",
                "column_name": "col_1",
            }
        ],
        "derivation_type": "single_source",
        "description": "output_sheet_1 is derived only from sheet_1 of input_file_1.xlsx.",
    }
]

# Small example: relationship between two sheets.
example_relationships: List[SheetRelationship] = [
    {
        "left_file": "input_file_1.xlsx",
        "left_sheet": "sheet_1",
        "right_file": "input_file_2.xlsx",
        "right_sheet": "sheet_2",
        "relationship_type": "one_to_many",
        "join_keys": [
            {
                "left": {
                    "file_name": "input_file_1.xlsx",
                    "sheet_name": "sheet_1",
                    "column_name": "col_1",
                },
                "right": {
                    "file_name": "input_file_2.xlsx",
                    "sheet_name": "sheet_2",
                    "column_name": "col_a",
                },
            }
        ],
        "explanation": "Each row in sheet_1 links to multiple rows in sheet_2 using col_1 = col_a.",
    }
]

# Small example FinalAnalysisState, combining everything above.
example_final_analysis_state: FinalAnalysisState = {
    "file_state": example_file_state,
    "column_mappings": example_column_mappings,
    "sheet_mappings": example_sheet_mappings,
    "relationships": example_relationships,
}
