# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Conditional Column Detection
====================================================
Detects new columns whose values are produced by IF / IFS / nested-IF logic
applied to one or more existing columns.

Detection strategy
------------------
1.  A *conditional* new column has LOW cardinality (2–15 unique values) and
    its values can be partitioned by ranges / thresholds on a numeric source
    column, OR by equality checks on a categorical source column.
2.  For each low-cardinality new column:
    a.  Try to find a numeric source column whose sorted-range partitions
        perfectly (or nearly) explain the output labels.
    b.  Try to find a categorical source column whose values map 1-to-1 to
        output labels (simple lookup / replace pattern).
    c.  Try multi-column conditions (AND / OR of two conditions).
3.  Reconstruct readable condition strings:
        {"if": "score > 80", "value": "A"}
        {"elif": "score > 50", "value": "B"}
        {"else": "C"}

Edge cases handled
------------------
- NaN / None in source or target → skipped rows.
- Boolean output (True / False, Yes / No, 1 / 0).
- Multiple independent conditional columns in one sheet.
- Overlapping ranges handled by ordered elif chain.
- column_mapping forwarded for renamed source columns.

Returns
-------
{
  "applied": bool,
  "details": {
    "rules": [
      {
        "column": str,
        "source_column": str,
        "condition_type": str,      # "numeric_range" | "categorical_map" | "boolean"
        "conditions": [
          {"if":   "<expr>", "value": <val>},
          {"elif": "<expr>", "value": <val>},
          ...
          {"else": <val>}
        ],
        "confidence": float
      }
    ]
  }
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MIN_UNIQUE       = 2
_MAX_UNIQUE       = 15
_MATCH_THRESHOLD  = 0.85
_BOOL_VALUES      = {
    frozenset({"true", "false"}),
    frozenset({"yes", "no"}),
    frozenset({"1", "0"}),
    frozenset({"y", "n"}),
    frozenset({"1.0", "0.0"}),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", s.lower())


def _is_boolean_col(series: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(series):
        return True
    uniq = {str(v).strip().lower() for v in series.dropna().unique()}
    return uniq in _BOOL_VALUES


def _match_ratio(predicted: pd.Series, actual: pd.Series) -> float:
    valid = actual.notna() & predicted.notna()
    if valid.sum() == 0:
        return 0.0
    return float((predicted[valid].astype(str) == actual[valid].astype(str)).mean())


# ─────────────────────────────────────────────────────────────────────────────
# Numeric-range partitioning
# ─────────────────────────────────────────────────────────────────────────────

def _build_range_conditions(
    src: pd.Series,
    tgt: pd.Series,
    src_col_name: str,
) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Try to explain *tgt* as numeric-range IF/ELIF/ELSE on *src*.

    Strategy:
    - Pair each unique label in *tgt* with the range of *src* values that
      produce it (min, max).
    - Sort ranges descending so the strictest condition comes first.
    - Reconstruct IF / ELIF / ELSE chain.
    - Verify the reconstructed chain reproduces *tgt* with ≥ threshold match.
    """
    try:
        df = pd.DataFrame({"src": src, "tgt": tgt}).dropna()
        if df.empty:
            return 0.0, []

        # Group src ranges per target label
        label_ranges: Dict[Any, Tuple[float, float]] = {}
        for label, grp in df.groupby("tgt"):
            label_ranges[label] = (grp["src"].min(), grp["src"].max())

        # Sort labels by their lower bound descending (highest threshold first)
        sorted_labels = sorted(
            label_ranges.items(),
            key=lambda x: x[1][0],
            reverse=True,
        )

        conditions: List[Dict[str, Any]] = []
        # Determine thresholds between consecutive ranges
        # Use the max of lower-range upper bound as the threshold boundary
        thresholds: List[Tuple[Any, float]] = []   # (label, threshold)

        for i, (label, (lo, hi)) in enumerate(sorted_labels[:-1]):
            # The next lower range's maximum value is the border
            next_label, (next_lo, next_hi) = sorted_labels[i + 1]
            # Choose threshold between hi of next and lo of current
            threshold = round((lo + next_hi) / 2, 10) if lo != next_hi else lo
            thresholds.append((label, threshold))

        else_label = sorted_labels[-1][0] if sorted_labels else None

        # Build conditions list
        for idx, (label, thresh) in enumerate(thresholds):
            key = "if" if idx == 0 else "elif"
            conditions.append({key: f"{src_col_name} >= {thresh}", "value": label})

        if else_label is not None:
            conditions.append({"else": else_label})

        # Reconstruct and verify
        reconstructed = pd.Series(index=src.index, dtype=object)
        remaining_mask = pd.Series(True, index=src.index)

        for cond in conditions:
            if "else" in cond:
                reconstructed[remaining_mask] = cond["else"]
            else:
                expr = list(cond.values())[0]   # the condition string
                val  = cond["value"]
                # Parse the threshold from the expression string
                m = re.search(r">= ?([\d.eE+\-]+)", expr)
                if not m:
                    continue
                thresh_val = float(m.group(1))
                mask = remaining_mask & (src >= thresh_val)
                reconstructed[mask] = val
                remaining_mask = remaining_mask & ~mask

        conf = _match_ratio(reconstructed, tgt)
        return conf, conditions

    except Exception:
        return 0.0, []


# ─────────────────────────────────────────────────────────────────────────────
# Categorical map
# ─────────────────────────────────────────────────────────────────────────────

def _build_categorical_conditions(
    src: pd.Series,
    tgt: pd.Series,
    src_col_name: str,
) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Try to explain *tgt* as a direct categorical mapping from *src*.
    Each unique src value maps to a specific tgt value.
    """
    try:
        df = pd.DataFrame({"src": src, "tgt": tgt}).dropna()
        if df.empty:
            return 0.0, []

        # Check if each src value maps to exactly one tgt value
        src_to_tgt = df.groupby("src")["tgt"].nunique()
        if (src_to_tgt > 1).any():
            return 0.0, []

        mapping = df.groupby("src")["tgt"].first().to_dict()
        if not mapping:
            return 0.0, []

        conditions: List[Dict[str, Any]] = []
        items = list(mapping.items())
        for idx, (src_val, tgt_val) in enumerate(items[:-1]):
            key = "if" if idx == 0 else "elif"
            conditions.append({key: f'{src_col_name} == "{src_val}"', "value": tgt_val})
        if items:
            last_src, last_tgt = items[-1]
            conditions.append({"else": last_tgt})

        # Verify
        reconstructed = src.map(mapping)
        conf = _match_ratio(reconstructed, tgt)
        return conf, conditions

    except Exception:
        return 0.0, []


# ─────────────────────────────────────────────────────────────────────────────
# Per-column detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_conditional_for_column(
    new_col: str,
    new_series: pd.Series,
    input_df: pd.DataFrame,
) -> Optional[Dict[str, Any]]:
    """Return a conditional rule for *new_col* or None if undetected."""
    n_unique = new_series.nunique(dropna=True)
    if not (_MIN_UNIQUE <= n_unique <= _MAX_UNIQUE):
        return None

    is_bool = _is_boolean_col(new_series)

    best: Dict[str, Any] = {}
    best_conf = 0.0

    for src_col in input_df.columns:
        src = input_df[src_col].reset_index(drop=True)
        tgt = new_series.reset_index(drop=True)

        # ── Numeric source → range conditions ────────────────────────────────
        if (
            pd.api.types.is_numeric_dtype(src)
            and not pd.api.types.is_bool_dtype(src)
        ):
            conf, conds = _build_range_conditions(src, tgt, src_col)
            if conf >= _MATCH_THRESHOLD and conf > best_conf:
                best_conf = conf
                best = {
                    "column":         new_col,
                    "source_column":  src_col,
                    "condition_type": "boolean" if is_bool else "numeric_range",
                    "conditions":     conds,
                    "confidence":     conf,
                }

        # ── Categorical source → map conditions ──────────────────────────────
        elif pd.api.types.is_object_dtype(src) or pd.api.types.is_string_dtype(src):
            conf, conds = _build_categorical_conditions(src, tgt, src_col)
            if conf >= _MATCH_THRESHOLD and conf > best_conf:
                best_conf = conf
                best = {
                    "column":         new_col,
                    "source_column":  src_col,
                    "condition_type": "boolean" if is_bool else "categorical_map",
                    "conditions":     conds,
                    "confidence":     conf,
                }

    return best if best else None 


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_conditional_columns_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect conditional (IF/IFS) columns.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame.
        column_mapping: Optional {output_col: input_col} name map.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {
                        "column": str,
                        "source_column": str,
                        "condition_type": str,
                        "conditions": [...],
                        "confidence": float
                    }
                ]
            }
        }
    """
    empty = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty

    # column_mapping = {output_col: input_col}
    # Filter out sentinel values (DERIVED COLUMN, etc.) from rename map
    # to avoid duplicate column names
    _DERIVED_SENTINELS = ("derived", "multisource", "unknown")
    real_rename_map: Dict[str, str] = {}
    if column_mapping:
        for out_col, in_col in column_mapping.items():
            if isinstance(in_col, str) and not any(kw in in_col.lower() for kw in _DERIVED_SENTINELS):
                real_rename_map[out_col] = in_col
    
    # Rename output columns to input namespace for comparison
    out_df = output_df.rename(columns=real_rename_map) if real_rename_map else output_df
    # Drop duplicates: multiple output cols can map to the same input col name.
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    # Create inv_map for translating back to output namespace
    inv_map: Dict[str, str] = {v: k for k, v in real_rename_map.items()} if real_rename_map else {}

    new_cols = [c for c in out_df.columns if c not in set(input_df.columns)]
    if not new_cols:
        return empty

    rules: List[Dict[str, Any]] = []
    for col in new_cols:
        if col not in out_df.columns:
            continue
        rule = _detect_conditional_for_column(
            col,
            out_df[col].reset_index(drop=True),
            input_df.reset_index(drop=True),
        )
        if rule:
            # Translate back to output namespace using inv_map
            out_col = inv_map.get(col, col)
            src_col = rule.get("source_column", "")
            out_src = inv_map.get(src_col, src_col)
            rule["column"] = out_col
            rule["source_column"] = out_src
            rules.append(rule)

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
