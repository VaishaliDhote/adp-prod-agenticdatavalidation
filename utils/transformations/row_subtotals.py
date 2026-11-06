# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Row Subtotals Utility
======================
Detects ADD ROWS (TOTALS / SUBTOTALS) transformations applied between
input_df and output_df.

Detection logic:
  1. Output has MORE rows than input → possible subtotal / total rows added.
  2. Identify extra rows (rows in output that have no exact match in input).
  3. For each extra row, check whether its numeric values equal the SUM/AVG/
     COUNT of a group of input rows (grouped by a categorical column).
  4. Also detect a grand-total row at the end of the output.

Supported aggregation functions: sum, avg, count, min, max
"""

import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional, Set, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_AGG_FUNCS = ["sum", "avg", "count", "min", "max"]


def _numeric_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
            and not pd.api.types.is_bool_dtype(df[c])]


def _categorical_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns
            if pd.api.types.is_string_dtype(df[c])
            or pd.api.types.is_object_dtype(df[c])]


def _input_row_keys(input_df: pd.DataFrame, common_cols: List[str]) -> Set[Tuple]:
    return {
        tuple(str(v) for v in row)
        for row in input_df[common_cols].values
    }


def _is_extra_row(row: pd.Series, input_keys: Set[Tuple], common_cols: List[str]) -> bool:
    key = tuple(str(row[c]) for c in common_cols if c in row.index)
    return key not in input_keys


def _agg_value(series: pd.Series, func: str) -> float:
    nums = pd.to_numeric(series, errors="coerce").dropna()
    if len(nums) == 0:
        return float("nan")
    if func == "sum":
        return float(nums.sum())
    if func == "avg":
        return float(nums.mean())
    if func == "count":
        return float(nums.count())
    if func == "min":
        return float(nums.min())
    if func == "max":
        return float(nums.max())
    return float("nan")


def _values_match(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Core detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_subtotal_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect subtotal / grand-total rows added to the output.

    Returns a list of rule dicts:
        {"group_by": str, "operation": str, "column": str}
    or for grand-total (no group_by):
        {"group_by": None, "operation": str, "column": str}
    """
    if len(output_df) <= len(input_df):
        return []   # No extra rows → no subtotals added

    common_cols = [c for c in input_df.columns if c in output_df.columns]
    if not common_cols:
        return []

    num_cols  = _numeric_cols(input_df)
    cat_cols  = _categorical_cols(input_df)

    if not num_cols:
        return []

    # Build key set from input rows (for "extra row" detection)
    input_keys = _input_row_keys(input_df, common_cols)

    # Separate extra rows from normal rows in output
    extra_rows = [
        row for _, row in output_df.iterrows()
        if _is_extra_row(row, input_keys, common_cols)
    ]

    if not extra_rows:
        return []

    rules: List[Dict[str, Any]] = []
    confirmed_combos: Set[Tuple] = set()   # (group_by_col, agg_col, func)

    for extra_row in extra_rows:
        for group_col in cat_cols:
            if group_col not in output_df.columns:
                continue
            group_val = extra_row.get(group_col)
            if pd.isna(group_val):
                continue

            # Get the input rows belonging to this group
            group_mask = input_df[group_col].astype(str) == str(group_val).replace(" SUBTOTAL", "").strip()
            group_df   = input_df[group_mask]

            if len(group_df) == 0:
                # Might be a label like "Engineering SUBTOTAL" → try prefix match
                prefix = str(group_val).split()[0] if group_val else ""
                group_mask = input_df[group_col].astype(str).str.startswith(prefix)
                group_df   = input_df[group_mask]

            if len(group_df) == 0:
                continue

            for agg_col in num_cols:
                if agg_col not in extra_row.index:
                    continue
                row_val = extra_row[agg_col]
                if pd.isna(row_val):
                    continue

                for func in _AGG_FUNCS:
                    combo = (group_col, agg_col, func)
                    if combo in confirmed_combos:
                        continue
                    expected = _agg_value(group_df[agg_col], func)
                    if not np.isnan(expected) and _values_match(row_val, expected):
                        rules.append({
                            "group_by":  group_col,
                            "operation": func,
                            "column":    agg_col,
                        })
                        confirmed_combos.add(combo)
                        break   # One func per (group_col, agg_col) pair

    # Check for a grand-total row (all input rows aggregated, no group)
    if not rules:
        for extra_row in extra_rows:
            for agg_col in num_cols:
                if agg_col not in extra_row.index:
                    continue
                row_val = extra_row[agg_col]
                if pd.isna(row_val):
                    continue
                for func in _AGG_FUNCS:
                    expected = _agg_value(input_df[agg_col], func)
                    if not np.isnan(expected) and _values_match(row_val, expected):
                        combo = (None, agg_col, func)
                        if combo not in confirmed_combos:
                            rules.append({
                                "group_by":  None,
                                "operation": func,
                                "column":    agg_col,
                            })
                            confirmed_combos.add(combo)
                        break

    return rules


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_subtotals_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect subtotal / total row additions and return the phase_2_rows
    'subtotal_rows' config block.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (with subtotal rows added).
        column_mapping: Optional {output_col: input_col} name mapping.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {"group_by": str | None, "operation": str, "column": str}
                ]
            }
        }
    """
    # Sentinel values for derived columns
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    
    # column_mapping is {output_col: input_col} - use directly to rename output→input
    if column_mapping:
        rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
        out_df = output_df.rename(columns=rename_map)
        # Drop duplicate column names that arise when multiple output cols map to
        # the same input col (e.g. Full_Name + Reconstructed_Full_Name → Full_Name).
        out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    else:
        out_df = output_df

    raw_rules = _detect_subtotal_rules(input_df, out_df)

    # Build inverse mapping {input_col: output_col} to translate detected names to output namespace
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}

    # Remap column names back to output namespace
    rules: List[Dict[str, Any]] = []
    for r in raw_rules:
        group_by = r["group_by"]
        agg_col  = r["column"]
        group_by = inv_map.get(group_by, group_by) if group_by else group_by
        agg_col  = inv_map.get(agg_col, agg_col)
        rules.append({
            "group_by":  group_by,
            "operation": r["operation"],
            "column":    agg_col,
        })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
