# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Structural Unpivot Utility
============================
Detects whether an UNPIVOT transformation has been applied between
input_df and output_df and builds the phase_1_structure config block.

Detection logic is taken directly from the structural_transformation notebook.
Column mapping (optional) is used to infer id_columns / value_columns /
var_name / value_name fields.
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Core detection  (notebook cell: detect_unpivot_transformation)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_unpivot(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    threshold: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """
    Detect UNPIVOT: Convert wide format to long format.

    Scoring (from notebook):
    +0.25  output has FEWER columns than input
    +0.25  output has MORE rows than input
    +0.20  input columns share common prefixes/suffixes (wide-format indicator)
    +0.15  input has >= 2 numeric columns (multiple measures to unpivot)
    +0.15  output columns have repeated values (long-format indicator)

    Returns a raw detection dict or None.
    """
    result: Dict[str, Any] = {
        "transformation": "unpivot",
        "confidence": 0.0,
        "details": {"indicators": [], "similar_column_groups": 0},
    }

    if input_df.empty or output_df.empty:
        return None

    input_rows, input_cols = input_df.shape
    output_rows, output_cols = output_df.shape

    # Hard gate: unpivot (wide → long) always produces MORE rows.
    # If the output has fewer or equal rows, it cannot be an unpivot —
    # it is more likely an aggregation / groupby reduction.
    if output_rows <= input_rows:
        return None

    score = 0.0
    indicators: List[str] = []

    # +0.25 – fewer output columns
    if output_cols < input_cols:
        score += 0.25
        indicators.append("fewer_columns")

    # +0.25 – more output rows
    if output_rows > input_rows:
        score += 0.25
        indicators.append("more_rows")

    # +0.20 – similar column names in input (common prefixes → wide-format groups)
    input_col_names = [str(col).lower() for col in input_df.columns]
    common_prefixes: Dict[str, int] = {}
    for name in input_col_names:
        prefix = name.split("_")[0] if "_" in name else name[:3]
        common_prefixes[prefix] = common_prefixes.get(prefix, 0) + 1

    similar_group_count = sum(1 for cnt in common_prefixes.values() if cnt > 1)
    if similar_group_count > 0:
        score += 0.20
        indicators.append("similar_columns")

    result["details"]["similar_column_groups"] = similar_group_count

    # +0.15 – multiple numeric columns in input
    numeric_cols = input_df.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric_cols) >= 2:
        score += 0.15
        indicators.append("multiple_numeric")

    # +0.15 – repeated values in output columns (long-format indicator)
    repeated_out_count = sum(
        1
        for col in output_df.columns
        if len(output_df) > 0
        and output_df[col].nunique() / len(output_df) < 0.5
    )
    if repeated_out_count > 0:
        score += 0.15
        indicators.append("repeated_output_values")

    result["confidence"] = min(score, 1.0)
    result["details"]["indicators"] = indicators

    if result["confidence"] >= threshold:
        return result
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Rule inference helpers
# ──────────────────────────────────────────────────────────────────────────────

def _infer_unpivot_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Infer unpivot rule parameters from the DataFrames and optional column_mapping.

    Strategy (in priority order):
    1. If column_mapping supplies explicit keys "id_columns", "value_columns",
       "var_name", "value_name" — use them directly.
    2. Otherwise auto-infer:
       - id_columns    : columns present in BOTH input and output (carried forward)
       - value_columns : columns present ONLY in input (the ones being unpivoted)
       - var_name      : generic output column that holds former column headers
                         (detected by low-uniqueness + dtype=object in output)
       - value_name    : output column that holds the unpivoted numeric values
                         (detected as first numeric output column not in input)
    """
    # ── 1. explicit mapping ──────────────────────────────────────────────────
    if column_mapping:
        rule: Dict[str, Any] = {}
        for key in ("id_columns", "value_columns", "var_name", "value_name"):
            val = column_mapping.get(key)
            if val is not None:
                if key in ("id_columns", "value_columns") and isinstance(val, str):
                    val = [val]
                rule[key] = val
        if rule:
            rule.setdefault("var_name", "variable")
            rule.setdefault("value_name", "value")
            return [rule]

    # ── 2. auto-inference ────────────────────────────────────────────────────
    input_col_set  = set(str(c) for c in input_df.columns)
    output_col_set = set(str(c) for c in output_df.columns)

    # id_columns: columns that survived into output unchanged
    id_columns = [str(c) for c in input_df.columns if str(c) in output_col_set]

    # value_columns: columns from input that did NOT survive (were unpivoted)
    value_columns = [str(c) for c in input_df.columns if str(c) not in output_col_set]

    # var_name: new output column (not in input) that is categorical / low-uniqueness
    new_output_cols = [c for c in output_df.columns if str(c) not in input_col_set]
    var_name = "variable"
    value_name = "value"

    for col in new_output_cols:
        uq_ratio = output_df[col].nunique() / max(len(output_df), 1)
        if output_df[col].dtype == object and uq_ratio < 0.5:
            var_name = str(col)
        elif pd.api.types.is_numeric_dtype(output_df[col]):
            value_name = str(col)

    rule: Dict[str, Any] = {
        "id_columns":    id_columns or [str(input_df.columns[0])],
        "value_columns": value_columns or [str(c) for c in input_df.columns[1:]],
        "var_name":      var_name,
        "value_name":    value_name,
    }
    return [rule]


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def build_unpivot_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]] = None,
    threshold: float = 0.6,
) -> Dict[str, Any]:
    """
    Detect unpivot and return the phase_1_structure 'unpivot' config block.

    Args:
        input_df:       Input DataFrame (wide format).
        output_df:      Output DataFrame (long / unpivoted format).
        column_mapping: Optional dict to explicitly specify unpivot parameters:
                        {
                          "id_columns":    ["product"],
                          "value_columns": ["jan", "feb", "mar"],
                          "var_name":      "month",
                          "value_name":    "sales"
                        }
                        When omitted the rule is inferred automatically.
        threshold:      Confidence threshold (default 0.6).

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {
                        "id_columns":    [...],
                        "value_columns": [...],
                        "var_name":      "month",
                        "value_name":    "sales"
                    }
                ]
            }
        }
    """
    detection = _detect_unpivot(input_df, output_df, threshold=threshold)
    applied = detection is not None

    rules: List[Dict[str, Any]] = []
    if applied:
        rules = _infer_unpivot_rules(input_df, output_df, column_mapping)

    return {
        "applied": applied,
        "details": {
            "rules": rules,
        },
    }
