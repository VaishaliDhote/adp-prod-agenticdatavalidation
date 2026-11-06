# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs;
# strip any pandas data-mutation helpers.
# ============================================================================
"""
Structural Pivot Utility
==========================
Detects whether a PIVOT transformation has been applied between
input_df and output_df and builds the phase_1_structure config block.

Detection logic is taken directly from the structural_transformation notebook.
Column mapping (optional) is used to infer index / columns / values fields.
"""

import re
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Cleaning-column suffix patterns  (these are NOT pivot artefacts)
# ──────────────────────────────────────────────────────────────────────────────

_CLEANING_SUFFIX_RE = re.compile(
    r"(_Cleaned|_Corrected|_Standardized|_Filled|_Replaced|_Fixed|_Trimmed"
    r"|_Normalized|_Mapped|_Encoded|_Parsed|_Validated|_Deduplicated"
    r"|_cleaned|_corrected|_standardized|_filled|_replaced|_fixed|_trimmed"
    r"|_normalized|_mapped|_encoded|_parsed|_validated|_deduplicated)$"
)


def _is_cleaning_column(col: str) -> bool:
    return bool(_CLEANING_SUFFIX_RE.search(str(col)))


# ──────────────────────────────────────────────────────────────────────────────
# Core detection  (notebook cell: detect_pivot_transformation)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_pivot(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    threshold: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """
    Detect PIVOT: Convert long format to wide format with aggregation.

    A true pivot's fingerprint is that new output column names come from
    **unique values** of a categorical input column.  Without that signal
    the detector will not fire, regardless of score.

    Scoring:
    +0.40  new output columns match unique values of an input categorical col  ← KEY
    +0.20  output has MORE columns than input
    +0.15  output has FEWER rows than input
    +0.15  input column whose values map → new headers disappeared from output
    +0.10  row reduction factor < 1

    Veto (returns None immediately):
    • ≥ 50 % of new output columns match a cleaning-suffix pattern

    Returns a raw detection dict (with indicators list) or None.
    """
    result: Dict[str, Any] = {
        "transformation": "pivot",
        "confidence": 0.0,
        "details": {"indicators": [], "score_components": {}},
    }

    if input_df.empty or output_df.empty:
        return None

    input_rows, input_cols = input_df.shape
    output_rows, output_cols = output_df.shape

    input_col_set  = set(str(c) for c in input_df.columns)
    output_col_set = set(str(c) for c in output_df.columns)
    new_output_cols = [c for c in output_df.columns if str(c) not in input_col_set]

    # ── Veto: majority of new columns are cleaning artefacts ─────────────────
    if new_output_cols:
        cleaning_count = sum(1 for c in new_output_cols if _is_cleaning_column(str(c)))
        if cleaning_count / len(new_output_cols) >= 0.5:
            return None   # ← hard veto; this is data cleaning, not a pivot

    score = 0.0
    indicators: List[str] = []

    # +0.40 – KEY SIGNAL: new output column names == unique values of an input col
    pivot_source_col = None
    new_col_set = set(str(c) for c in new_output_cols)
    if new_col_set:
        for col in input_df.columns:
            unique_vals = set(str(v) for v in input_df[col].dropna().unique())
            if unique_vals and unique_vals.issubset(new_col_set):
                score += 0.40
                indicators.append("values_became_columns")
                pivot_source_col = str(col)
                break

    # Without the key signal the transformation is not a pivot
    if "values_became_columns" not in indicators:
        return None

    # +0.20 – more output columns
    if output_cols > input_cols:
        score += 0.20
        indicators.append("more_columns")

    # +0.15 – fewer output rows
    if output_rows < input_rows:
        score += 0.15
        indicators.append("fewer_rows")

    # +0.15 – pivot source column disappeared from output
    if pivot_source_col and pivot_source_col not in output_col_set:
        score += 0.15
        indicators.append("source_col_disappeared")

    # +0.10 – row reduction factor
    if output_rows > 0 and (output_rows / input_rows) < 1:
        score += 0.10
        indicators.append("row_reduction")

    result["confidence"] = min(score, 1.0)
    result["details"]["indicators"] = indicators
    result["details"]["score_components"] = {
        "values_became_columns": 0.40 if "values_became_columns" in indicators else 0,
        "more_columns":          0.20 if "more_columns"          in indicators else 0,
        "fewer_rows":            0.15 if "fewer_rows"            in indicators else 0,
        "source_col_disappeared":0.15 if "source_col_disappeared" in indicators else 0,
        "row_reduction":         0.10 if "row_reduction"          in indicators else 0,
    }

    if result["confidence"] >= threshold:
        return result
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Rule inference helpers
# ──────────────────────────────────────────────────────────────────────────────

def _infer_pivot_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """
    Infer pivot rule parameters from the DataFrames and optional column_mapping.

    Strategy (in priority order):
    1. If column_mapping supplies explicit keys "index", "columns", "values",
       "aggregation" — use them directly.
    2. Otherwise auto-infer:
       - index   : input columns whose values still appear in output index/first cols
       - columns : input column whose unique values became new output column headers
       - values  : numeric input columns that were aggregated
       - aggregation: default "sum"
    """
    # ── 1. explicit mapping ──────────────────────────────────────────────────
    if column_mapping:
        rule: Dict[str, Any] = {}
        for key in ("index", "columns", "values", "aggregation"):
            val = column_mapping.get(key)
            if val is not None:
                # normalise to list where expected
                if key in ("index", "columns", "values") and isinstance(val, str):
                    val = [val]
                rule[key] = val
        if rule:
            rule.setdefault("aggregation", "sum")
            return [rule]

    # ── 2. auto-inference ────────────────────────────────────────────────────
    input_col_set  = set(str(c) for c in input_df.columns)
    output_col_set = set(str(c) for c in output_df.columns)

    new_output_cols = [c for c in output_df.columns if str(c) not in input_col_set]
    new_col_set = set(str(c) for c in new_output_cols)

    # Columns that disappeared AND whose unique values match new output headers
    pivot_col_candidates = [
        col for col in input_df.columns
        if str(col) not in output_col_set
        and set(str(v) for v in input_df[col].dropna().unique()).issubset(new_col_set)
    ]

    # Numeric input columns → values (aggregated)
    numeric_input_cols = input_df.select_dtypes(include=[np.number]).columns.tolist()

    # Remaining input columns that are still present in output → index columns
    remaining_input_cols = [
        col for col in input_df.columns
        if str(col) in output_col_set and str(col) not in pivot_col_candidates
    ]

    rule = {
        "index":       remaining_input_cols or [str(input_df.columns[0])],
        "columns":     [str(c) for c in pivot_col_candidates] or (
                           [str(new_output_cols[0])] if new_output_cols else []
                       ),
        "values":      [str(c) for c in numeric_input_cols] or list(input_col_set - output_col_set),
        "aggregation": "sum",
    }
    return [rule]


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def build_pivot_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]] = None,
    threshold: float = 0.6,
) -> Dict[str, Any]:
    """
    Detect pivot and return the phase_1_structure 'pivot' config block.

    Args:
        input_df:       Input DataFrame (long format).
        output_df:      Output DataFrame (wide / pivot format).
        column_mapping: Optional dict to explicitly specify pivot parameters:
                        {
                          "index":       "product"   | ["product"],
                          "columns":     "month"     | ["month"],
                          "values":      "sales"     | ["sales"],
                          "aggregation": "sum"
                        }
                        When omitted the rule is inferred automatically.
        threshold:      Confidence threshold (default 0.6).

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {
                        "index":       [...],
                        "columns":     [...],
                        "values":      [...],
                        "aggregation": "sum"
                    }
                ]
            }
        }
    """
    detection = _detect_pivot(input_df, output_df, threshold=threshold)
    applied = detection is not None

    rules: List[Dict[str, Any]] = []
    if applied:
        rules = _infer_pivot_rules(input_df, output_df, column_mapping)

    return {
        "applied": applied,
        "details": {
            "rules": rules,
        },
    }
