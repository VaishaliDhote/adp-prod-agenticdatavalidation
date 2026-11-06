# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Structural Hierarchy Transform Utility
=========================================
Detects whether a FLATTEN or CREATE_HIERARCHICAL transformation has been
applied between input_df and output_df and builds the phase_1_structure
config block.

Detection logic is taken directly from the structural_transformation notebook.
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Shared constants
# ──────────────────────────────────────────────────────────────────────────────

_SUBTOTAL_KEYWORDS = [
    "subtotal", "total", "grand total", "grand_total", "sub-total",
    "sub_total", "summary", "sub total", "sum", "aggregate",
    "overall", "combined", "consolidated",
]


def _count_subtotal_rows(df: pd.DataFrame) -> int:
    """Count rows that contain any subtotal/summary keyword."""
    count = 0
    for _, row in df.iterrows():
        row_str = " ".join(str(v).lower() for v in row if pd.notna(v))
        if any(kw in row_str for kw in _SUBTOTAL_KEYWORDS):
            count += 1
    return count


# ──────────────────────────────────────────────────────────────────────────────
# Core detection – FLATTEN  (notebook: detect_flatten_hierarchical_transformation)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_flatten(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    threshold: float = 0.65,
) -> Optional[Dict[str, Any]]:
    """
    Detect FLATTEN: Remove grouping/subtotals to create a flat table.

    Scoring (from notebook):
    +0.25  output has FEWER rows than input
    +0.30  subtotal/summary rows found in input
    +0.25  output has NO subtotal rows (input had some)
    +0.10  output columns are a subset of input columns
    +0.10  input null-ratio > 1.5× output null-ratio (grouping via nulls removed)

    Returns raw detection dict or None.
    """
    result: Dict[str, Any] = {
        "transformation": "flatten",
        "confidence": 0.0,
        "details": {"indicators": []},
    }

    if input_df.empty or output_df.empty:
        return None

    input_rows, input_cols = input_df.shape
    output_rows, output_cols = output_df.shape

    # FLATTEN always reduces rows
    if output_rows >= input_rows:
        return None

    score = 0.0
    indicators: List[str] = []

    # +0.25 – row reduction
    score += 0.25
    indicators.append("row_reduction")

    # +0.30 / +0.25 – subtotal rows in input / absent in output
    subtotal_count_in  = _count_subtotal_rows(input_df)
    subtotal_count_out = _count_subtotal_rows(output_df)

    result["details"]["subtotal_count_input"]  = subtotal_count_in
    result["details"]["subtotal_count_output"] = subtotal_count_out

    if subtotal_count_in > 0:
        score += 0.30
        indicators.append("subtotal_rows")

    if subtotal_count_out == 0 and subtotal_count_in > 0:
        score += 0.25
        indicators.append("no_output_subtotals")

    # +0.10 – output columns ⊆ input columns
    in_cols_lower  = set(str(c).lower() for c in input_df.columns)
    out_cols_lower = set(str(c).lower() for c in output_df.columns)
    if out_cols_lower.issubset(in_cols_lower):
        score += 0.10
        indicators.append("column_consistency")

    # +0.10 – null ratio decrease (grouping rows removed)
    null_ratio_in  = (
        input_df.isna().sum().sum() / (input_rows * input_cols)
        if input_rows * input_cols > 0 else 0
    )
    null_ratio_out = (
        output_df.isna().sum().sum() / (output_rows * output_cols)
        if output_rows * output_cols > 0 else 0
    )
    if null_ratio_in > null_ratio_out * 1.5:
        score += 0.10
        indicators.append("null_reduction")

    result["confidence"] = min(score, 1.0)
    result["details"]["indicators"] = indicators

    if result["confidence"] >= threshold:
        return result
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Core detection – CREATE HIERARCHICAL  (notebook: detect_create_hierarchical_transformation)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_create_hierarchical(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    threshold: float = 0.65,
) -> Optional[Dict[str, Any]]:
    """
    Detect CREATE HIERARCHICAL: Add grouping levels and subtotal rows.

    Scoring (from notebook):
    +0.25  output has MORE rows than input
    +0.30  subtotal/summary rows found in output
    +0.20  new columns added in output (grouping columns)
    +0.15  output null-ratio > 1.5× input null-ratio (indentation/grouping structure)
    +0.10  output has >= input numeric columns (data preserved)

    Returns raw detection dict or None.
    """
    result: Dict[str, Any] = {
        "transformation": "create_hierarchical",
        "confidence": 0.0,
        "details": {"indicators": []},
    }

    if input_df.empty or output_df.empty:
        return None

    input_rows, input_cols = input_df.shape
    output_rows, output_cols = output_df.shape

    # CREATE HIERARCHICAL always adds rows
    if output_rows <= input_rows:
        return None

    score = 0.0
    indicators: List[str] = []

    # +0.25 – row increase
    score += 0.25
    indicators.append("row_increase")

    # +0.30 – subtotal rows present in output
    subtotal_count = _count_subtotal_rows(output_df)
    result["details"]["subtotal_count"] = subtotal_count

    if subtotal_count > 0:
        score += 0.30
        indicators.append("subtotal_rows")

    # +0.20 – new columns in output (grouping columns)
    in_cols_lower  = set(str(c).lower() for c in input_df.columns)
    out_cols_lower = set(str(c).lower() for c in output_df.columns)
    new_cols = out_cols_lower - in_cols_lower
    result["details"]["new_columns_count"] = len(new_cols)

    if new_cols:
        score += 0.20
        indicators.append("new_grouping_columns")

    # +0.15 – null ratio increase (indentation / grouping structure)
    null_ratio_in  = (
        input_df.isna().sum().sum() / (input_rows * input_cols)
        if input_rows * input_cols > 0 else 0
    )
    null_ratio_out = (
        output_df.isna().sum().sum() / (output_rows * output_cols)
        if output_rows * output_cols > 0 else 0
    )
    if null_ratio_out > null_ratio_in * 1.5:
        score += 0.15
        indicators.append("null_increase")

    # +0.10 – numeric columns preserved
    n_numeric_in  = input_df.select_dtypes(include=[np.number]).shape[1]
    n_numeric_out = output_df.select_dtypes(include=[np.number]).shape[1]
    if n_numeric_out >= n_numeric_in and n_numeric_in > 0:
        score += 0.10
        indicators.append("data_preservation")

    result["confidence"] = min(score, 1.0)
    result["details"]["indicators"] = indicators

    if result["confidence"] >= threshold:
        return result
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def build_hierarchy_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]] = None,
    flatten_threshold: float = 0.65,
    create_threshold: float = 0.65,
) -> Dict[str, Any]:
    """
    Detect hierarchy transformation (flatten OR create_hierarchical) and return
    the phase_1_structure 'hierarchy_transform' config block.

    Detection priority:
    - If output has FEWER rows than input  → try FLATTEN first.
    - If output has MORE rows than input   → try CREATE_HIERARCHICAL first.
    - The detected type is stored in details["type"].

    Args:
        input_df:          Input DataFrame.
        output_df:         Output DataFrame.
        column_mapping:    Optional dict — currently reserved for future use.
                           Accepted keys: "type" ("flatten" | "create_hierarchical")
                           to force a specific sub-type.
        flatten_threshold:    Confidence threshold for flatten (default 0.65).
        create_threshold:     Confidence threshold for create_hierarchical (default 0.65).

    Returns:
        {
            "applied": bool,
            "details": {
                "type": "flatten" | "create_hierarchical" | None
            }
        }
    """
    forced_type = (column_mapping or {}).get("type")

    hierarchy_type: Optional[str] = None

    if forced_type == "flatten":
        det = _detect_flatten(input_df, output_df, threshold=flatten_threshold)
        if det:
            hierarchy_type = "flatten"
    elif forced_type == "create_hierarchical":
        det = _detect_create_hierarchical(input_df, output_df, threshold=create_threshold)
        if det:
            hierarchy_type = "create_hierarchical"
    else:
        # Auto-detect based on row count direction
        input_rows  = len(input_df)
        output_rows = len(output_df)

        if output_rows < input_rows:
            det = _detect_flatten(input_df, output_df, threshold=flatten_threshold)
            if det:
                hierarchy_type = "flatten"
        elif output_rows > input_rows:
            det = _detect_create_hierarchical(input_df, output_df, threshold=create_threshold)
            if det:
                hierarchy_type = "create_hierarchical"

    applied = hierarchy_type is not None

    return {
        "applied": applied,
        "details": {
            "type": hierarchy_type,
        },
    }
