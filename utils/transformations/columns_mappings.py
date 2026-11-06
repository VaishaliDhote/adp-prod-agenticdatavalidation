# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Column mapping extraction (source-to-target). REUSABLE.
# Rework: attach source/target column UUIDs and feed the rule inventory.
# ============================================================================
import pandas as pd
from typing import Dict, Any
from states.final_analysis_state import FinalAnalysisState

def detect_column_mapping(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    sample_size: int = 5
) -> Dict[str, Any]:
    """
    Detect column mappings and return full context for LLM.

    Returns:
    {
        "output_columns": [...],
        "input_columns": [...],
        "matched_columns": {output: input},
        "unmatched_columns": [...],
        "sample_data": {
            "input": {...},
            "output": {...}
        }
    }
    """

    print(f"output columns: {output_df.columns.tolist()}")
    print("\n🔍 Detecting column mappings (output → input)...\n")

    # -------------------------
    # Step 0: Detect & fix transposed output
    # -------------------------
    # A transposed sheet has 'Unnamed: 0' as first column (blank header)
    # and integer column names for the rest — the actual headers are in
    # the first column's values. Un-transpose it.
    first_col = output_df.columns[0]
    if str(first_col) == "Unnamed: 0" and all(isinstance(c, int) for c in output_df.columns[1:]):
        output_df = output_df.set_index(first_col).T.reset_index(drop=True)
        output_df.columns.name = None
        print(f"⚠️  Transposed output detected — un-transposed. New columns: {output_df.columns.tolist()}")

    # -------------------------
    # Step 1: Init
    # -------------------------
    final_mapping = {out_col: "no_match" for out_col in output_df.columns}

    # -------------------------
    # Step 1: Direct match
    # -------------------------
    for out_col in output_df.columns:
        if out_col in input_df.columns:
            final_mapping[out_col] = out_col
            print(f"✔ Direct match: '{out_col}' → '{out_col}'")

    # -------------------------
    # Step 2: Data-based match
    # -------------------------
    for out_col in output_df.columns:

        if final_mapping[out_col] != "no_match":
            continue

        for in_col in input_df.columns:
            try:
                in_values = set(input_df[in_col].dropna().astype(str).unique())
                out_values = set(output_df[out_col].dropna().astype(str).unique())

                if len(out_values) > 0 and out_values.issubset(in_values):

                    sample_n = min(5, len(output_df))
                    in_sample = set(input_df[in_col].dropna().astype(str).head(sample_n))
                    out_sample = set(output_df[out_col].dropna().astype(str).head(sample_n))

                    if len(in_sample.intersection(out_sample)) > 0:
                        final_mapping[out_col] = in_col
                        print(f"✔ Matched by data: '{out_col}' → '{in_col}'")
                        break

            except Exception:
                continue

    # -------------------------
    # Step 3: Split matched/unmatched
    # -------------------------
    matched_columns = {k: v for k, v in final_mapping.items() if v != "no_match"}
    unmatched_columns = [k for k, v in final_mapping.items() if v == "no_match"]

    # -------------------------
    # Step 4: Sample Data (LIMITED + CLEAN)
    # -------------------------
    sample_size = min(sample_size, len(input_df), len(output_df))

    def get_sample(df, cols):
        return {
            col: df[col].dropna().astype(str).head(sample_size).tolist()
            for col in cols if col in df.columns
        }

    sample_data = {
        "input": get_sample(input_df, input_df.columns),
        "output": get_sample(output_df, output_df.columns)
    }

    # -------------------------
    # Step 5: Final Context
    # -------------------------
    context = {
        "output_columns": list(output_df.columns),
        "input_columns": list(input_df.columns),
        "matched_columns": matched_columns,
        "unmatched_columns": unmatched_columns,
        "sample_data": sample_data
    }

    print("\n📦 Context prepared for LLM:")
    print(context)

    return context

def _extract_column_mapping(file_analysis_state: FinalAnalysisState) -> dict:
    """
    Extracts column mappings from the filestate based on the mapping type.
    
    Args:
        file_analysis_state (FinalAnalysisState): The entire file analysis state.
    
    Returns:
        dict: A dictionary mapping output columns to their respective input columns or derived/multisource status.
    """
    # print(f"file_analysis_state in agent: {file_analysis_state}")
    output_to_input_mapping = {}   
    
    # Navigate to column mappings in the filestate
    # Handle both structures: 
    #   1. column_mappings is already a list (output_sheets array)
    #   2. column_mappings is a dict with "output_sheets" key
    raw_column_mappings = file_analysis_state.get("column_mappings", [])
    if isinstance(raw_column_mappings, list):
        column_mappings = raw_column_mappings
    elif isinstance(raw_column_mappings, dict):
        column_mappings = raw_column_mappings.get("output_sheets", [])
    else:
        column_mappings = []
    
    print(f"Extracting column mappings from filestate: found {len(column_mappings)} sheet mappings")
    for sheet_mapping in column_mappings:
        print(f"Processing sheet mapping: {sheet_mapping.get('sheet_name', 'Unknown Sheet')}")
        for column_mapping in sheet_mapping.get("column_mappings", []):
            output_column = column_mapping.get("output_column")
            mapping_type = column_mapping.get("mapping_type")

            if mapping_type == "single_source":
                # Map output column to the input column name
                source_groups = column_mapping.get("source_groups", [])
                if source_groups:
                    input_column = source_groups[0]["columns"][0].get("column")
                    output_to_input_mapping[output_column] = input_column
            elif mapping_type == "derived_column":
                # Mark as derived column
                output_to_input_mapping[output_column] = "DERIVED COLUMN"
            elif mapping_type == "multisource":
                # Mark as multisource column
                output_to_input_mapping[output_column] = "MULTISOURCE COLUMN"
            else:
                # Handle unknown mapping types
                output_to_input_mapping[output_column] = "UNKNOWN MAPPING TYPE"

    return output_to_input_mapping