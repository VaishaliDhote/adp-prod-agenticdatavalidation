# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Structural Transpose Utility
==============================
Detects whether a TRANSPOSE transformation has been applied between
input_df and output_df and builds the phase_1_structure config block.

Detection logic is taken directly from the structural_transformation notebook.
"""

import pandas as pd
from typing import Any, Dict, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Core detection  (notebook cell: detect_transpose_transformation)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_transpose(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    threshold: float = 0.7,
) -> Optional[Dict[str, Any]]:
    """
    Detect TRANSPOSE: Swap rows and columns.

    Logic (from notebook):
    1. Check dimension swap — input rows ≈ output columns AND input cols ≈ output rows
       (10 % tolerance)
    2. Transpose input and compare cell-by-cell similarity to output.
    3. Confirm when similarity ≥ threshold.

    Returns a raw detection dict or None.
    """
    result = {
        "transformation": "transpose",
        "confidence": 0.0,
        "details": {},
    }

    if input_df.empty or output_df.empty:
        return None

    input_rows, input_cols = input_df.shape
    output_rows, output_cols = output_df.shape

    # Step 1 – dimension swap
    dimension_tolerance = 0.1
    if input_rows == 0 or output_cols == 0:
        return None

    row_col_ratio = abs(input_rows - output_cols) / max(input_rows, output_cols)
    col_row_ratio = abs(input_cols - output_rows) / max(input_cols, output_rows)

    if row_col_ratio > dimension_tolerance or col_row_ratio > dimension_tolerance:
        return None  # no dimension swap

    result["details"]["dimension_swap"] = True

    # Step 2 – content similarity after transpose
    try:
        transposed_input = input_df.T.reset_index()
        shape_match = transposed_input.shape == output_df.shape
        result["details"]["shape_match"] = shape_match

        check_rows = min(transposed_input.shape[0], output_df.shape[0])
        check_cols = min(transposed_input.shape[1], output_df.shape[1])

        matching_cells = 0
        total_cells = check_rows * check_cols

        for i in range(check_rows):
            for j in range(check_cols):
                try:
                    t_val = str(transposed_input.iloc[i, j]).strip().lower()
                    o_val = str(output_df.iloc[i, j]).strip().lower()
                    if t_val == o_val or (
                        pd.isna(transposed_input.iloc[i, j])
                        and pd.isna(output_df.iloc[i, j])
                    ):
                        matching_cells += 1
                except Exception:
                    pass

        if total_cells > 0:
            similarity = matching_cells / total_cells
            result["confidence"] = similarity
            result["details"]["matching_cells"] = matching_cells
            result["details"]["total_cells"] = total_cells
            result["details"]["partial_match"] = not shape_match

            if similarity >= threshold:
                return result

    except Exception as exc:
        result["details"]["error"] = str(exc)

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def build_transpose_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    threshold: float = 0.7,
) -> Dict[str, Any]:
    """
    Detect transpose and return the phase_1_structure 'transpose' config block.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame.
        column_mapping: Optional dict {input_col: output_col} — reserved for
                        future use (transpose does not require column mapping).
        threshold:      Similarity threshold to confirm detection (default 0.7).

    Returns:
        {
            "applied": bool,
            "details": {"enabled": bool}
        }
    """
    detection = _detect_transpose(input_df, output_df, threshold=threshold)
    applied = detection is not None

    return {
        "applied": applied,
        "details": {
            "enabled": applied,
        },
    }
