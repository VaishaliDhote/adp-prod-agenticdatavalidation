# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Aggregation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Extended Aggregation Transformation Utilities
==============================================
Additional window and aggregation functions for Phase 5.

Functions added:
  build_lag_lead_config           → LAG / LEAD (previous/next row values)
  build_advanced_ranking_config   → DENSE_RANK, PERCENT_RANK, NTILE, ROW_NUMBER
  build_value_window_config       → FIRST_VALUE, LAST_VALUE, NTH_VALUE
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd


# Import shared utilities from main aggregations module
from .aggregations import (
    _norm,
    _numeric_cols,
    _categorical_cols,
    _coerce_numeric,
    _val_close,
    _remap_output,
    _to_output_col,
    _detect_partition,
)


# ─────────────────────────────────────────────────────────────────────────────
# 5.5  WINDOW – LAG / LEAD
# ─────────────────────────────────────────────────────────────────────────────

def _detect_lag_lead_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect LAG (previous row value) and LEAD (next row value) window functions.
    
    Detection paths
    ---------------
    Path A – Name-based: columns containing 'lag', 'lead', 'prev', 'next', 'shift'
    Path B – Value-based: new columns whose values match shifted input values
    
    Edge cases
    ----------
    - Multi-period shifts (lag 2, lead 3, etc.)
    - Partitioned lag/lead (resets per group)
    - NaN values for rows without previous/next values
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) != len(input_df):
        return []
    
    in_cols = set(input_df.columns)
    num_cols_in = _numeric_cols(input_df)
    cat_cols = _categorical_cols(input_df)
    rules: List[Dict[str, Any]] = []
    confirmed: Set[str] = set()
    
    lag_keywords = ["lag", "prev", "previous", "prior", "shift_down"]
    lead_keywords = ["lead", "next", "following", "shift_up"]
    
    # ── Path A: name hints ────────────────────────────────────────────────────
    for col in output_df.columns:
        if col in in_cols:
            continue
        col_n = _norm(col)
        
        is_lag = any(kw in col_n for kw in lag_keywords)
        is_lead = any(kw in col_n for kw in lead_keywords)
        
        if not (is_lag or is_lead):
            continue
        
        # Find source column
        best_src: Optional[str] = None
        for src in num_cols_in:
            src_n = _norm(src)
            if any(tok in col_n for tok in src_n.split("_") if len(tok) >= 3):
                best_src = src
                break
        
        if best_src is None and num_cols_in:
            best_src = num_cols_in[0]
        
        # Extract period from name (e.g., "lag_2_sales" → 2)
        period_match = re.search(r'(?:lag|lead|shift)_(\d+)', col_n)
        periods = int(period_match.group(1)) if period_match else 1
        
        partition = _detect_partition(input_df, output_df, col, cat_cols)
        
        rules.append({
            "column": best_src or col,
            "periods": periods,
            "direction": "lag" if is_lag else "lead",
            "partition_by": partition,
        })
        confirmed.add(col)
    
    # ── Path B: value verification ────────────────────────────────────────────
    for col in output_df.columns:
        if col in in_cols or col in confirmed:
            continue
        if not pd.api.types.is_numeric_dtype(output_df[col]):
            continue
        
        out_vals = _coerce_numeric(output_df[col]).reset_index(drop=True)
        
        for src in num_cols_in:
            src_vals = _coerce_numeric(input_df[src]).reset_index(drop=True)
            
            # Try different shift periods
            for periods in range(1, min(6, len(src_vals))):
                # Test LAG
                shifted_lag = src_vals.shift(periods)
                n = min(len(shifted_lag), len(out_vals))
                matches_lag = sum(
                    _val_close(shifted_lag.iloc[i], out_vals.iloc[i])
                    for i in range(n)
                    if not pd.isna(out_vals.iloc[i])
                )
                total = out_vals.notna().sum()
                if total > 0 and matches_lag / total >= 0.7:
                    partition = _detect_partition(input_df, output_df, col, cat_cols)
                    rules.append({
                        "column": src,
                        "periods": periods,
                        "direction": "lag",
                        "partition_by": partition,
                    })
                    confirmed.add(col)
                    break
                
                # Test LEAD
                shifted_lead = src_vals.shift(-periods)
                matches_lead = sum(
                    _val_close(shifted_lead.iloc[i], out_vals.iloc[i])
                    for i in range(n)
                    if not pd.isna(out_vals.iloc[i])
                )
                if total > 0 and matches_lead / total >= 0.7:
                    partition = _detect_partition(input_df, output_df, col, cat_cols)
                    rules.append({
                        "column": src,
                        "periods": periods,
                        "direction": "lead",
                        "partition_by": partition,
                    })
                    confirmed.add(col)
                    break
            
            if col in confirmed:
                break
    
    return rules


def build_lag_lead_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect LAG / LEAD window transformations.
    
    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "periods": int, "direction": "lag"|"lead",
           "partition_by": [str] | None}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}
    
    out_df = _remap_output(output_df, column_mapping)
    raw = _detect_lag_lead_rules(input_df, out_df)
    
    rules = [
        {
            "column": _to_output_col(r["column"], column_mapping),
            "periods": r["periods"],
            "direction": r["direction"],
            "partition_by": (
                [_to_output_col(c, column_mapping) for c in r["partition_by"]]
                if r["partition_by"] else None
            ),
        }
        for r in raw
    ]
    
    return {"applied": len(rules) > 0, "details": {"rules": rules}}


# ─────────────────────────────────────────────────────────────────────────────
# 5.6  WINDOW – DENSE RANK, PERCENT RANK, NTILE, ROW_NUMBER
# ─────────────────────────────────────────────────────────────────────────────

def _find_rank_source(
    col_name: str, 
    num_cols: List[str], 
    input_df: pd.DataFrame, 
    rank_series: pd.Series
) -> Optional[str]:
    """Find the most likely source column being ranked."""
    col_n = _norm(col_name)
    
    # Try name matching first
    for src in num_cols:
        src_n = _norm(src)
        if any(tok in col_n for tok in src_n.split("_") if len(tok) >= 3):
            return src
    
    # Fallback to first numeric column
    return num_cols[0] if num_cols else None


def _detect_advanced_ranking_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect DENSE_RANK, PERCENT_RANK, NTILE, ROW_NUMBER window functions.
    
    Detection paths
    ---------------
    - Dense rank: ranks without gaps (1,2,2,3 not 1,2,2,4)
    - Percent rank: percentile rank (0.0 to 1.0)
    - Ntile: divide rows into N equal buckets
    - Row number: sequential numbering within partition
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) != len(input_df):
        return []
    
    in_cols = set(input_df.columns)
    num_cols_in = _numeric_cols(input_df)
    cat_cols = _categorical_cols(input_df)
    rules: List[Dict[str, Any]] = []
    confirmed: Set[str] = set()
    
    dense_rank_keywords = ["dense_rank", "denserank", "dense_ranking"]
    percent_rank_keywords = ["percent_rank", "percentrank", "pct_rank", "percentile"]
    ntile_keywords = ["ntile", "quartile", "decile", "percentile_bucket", "bucket"]
    row_number_keywords = ["row_number", "row_num", "rownum", "sequence", "seq"]
    
    for col in output_df.columns:
        if col in in_cols:
            continue
        col_n = _norm(col)
        
        # Check for dense rank
        if any(kw in col_n for kw in dense_rank_keywords):
            best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
            partition = _detect_partition(input_df, output_df, col, cat_cols)
            rules.append({
                "rank_type": "dense_rank",
                "column": best_src,
                "partition_by": partition,
            })
            confirmed.add(col)
            continue
        
        # Check for percent rank
        if any(kw in col_n for kw in percent_rank_keywords):
            out_vals = _coerce_numeric(output_df[col]).dropna()
            if len(out_vals) > 0 and out_vals.min() >= 0 and out_vals.max() <= 1:
                best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
                partition = _detect_partition(input_df, output_df, col, cat_cols)
                rules.append({
                    "rank_type": "percent_rank",
                    "column": best_src,
                    "partition_by": partition,
                })
                confirmed.add(col)
                continue
        
        # Check for ntile
        if any(kw in col_n for kw in ntile_keywords):
            out_vals = _coerce_numeric(output_df[col]).dropna()
            if len(out_vals) > 0:
                n_buckets = int(out_vals.max())
                best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
                partition = _detect_partition(input_df, output_df, col, cat_cols)
                rules.append({
                    "rank_type": "ntile",
                    "column": best_src,
                    "n_buckets": n_buckets,
                    "partition_by": partition,
                })
                confirmed.add(col)
                continue
        
        # Check for row number
        if any(kw in col_n for kw in row_number_keywords):
            partition = _detect_partition(input_df, output_df, col, cat_cols)
            rules.append({
                "rank_type": "row_number",
                "partition_by": partition,
            })
            confirmed.add(col)
            continue
    
    return rules


def build_advanced_ranking_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect advanced ranking functions: DENSE_RANK, PERCENT_RANK, NTILE, ROW_NUMBER.
    
    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"rank_type": "dense_rank"|"percent_rank"|"ntile"|"row_number",
           "column": str | None, "n_buckets": int | None,
           "partition_by": [str] | None}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}
    
    out_df = _remap_output(output_df, column_mapping)
    raw = _detect_advanced_ranking_rules(input_df, out_df)
    
    rules = []
    for r in raw:
        rule = {
            "rank_type": r["rank_type"],
            "partition_by": (
                [_to_output_col(c, column_mapping) for c in r["partition_by"]]
                if r.get("partition_by") else None
            ),
        }
        if "column" in r:
            rule["column"] = _to_output_col(r["column"], column_mapping)
        if "n_buckets" in r:
            rule["n_buckets"] = r["n_buckets"]
        rules.append(rule)
    
    return {"applied": len(rules) > 0, "details": {"rules": rules}}


# ─────────────────────────────────────────────────────────────────────────────
# 5.7  WINDOW – FIRST VALUE, LAST VALUE, NTH VALUE
# ─────────────────────────────────────────────────────────────────────────────

def _detect_value_window_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect FIRST_VALUE, LAST_VALUE, NTH_VALUE window functions.
    
    These functions return values from specific rows within a window/partition.
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) != len(input_df):
        return []
    
    in_cols = set(input_df.columns)
    num_cols_in = _numeric_cols(input_df)
    cat_cols = _categorical_cols(input_df)
    rules: List[Dict[str, Any]] = []
    
    first_keywords = ["first_value", "first_val", "firstval"]
    last_keywords = ["last_value", "last_val", "lastval"]
    nth_keywords = ["nth_value", "nth_val", "nthval"]
    
    for col in output_df.columns:
        if col in in_cols:
            continue
        col_n = _norm(col)
        
        # Check for first value
        if any(kw in col_n for kw in first_keywords):
            best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
            partition = _detect_partition(input_df, output_df, col, cat_cols)
            rules.append({
                "value_type": "first_value",
                "column": best_src,
                "partition_by": partition,
            })
            continue
        
        # Check for last value
        if any(kw in col_n for kw in last_keywords):
            best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
            partition = _detect_partition(input_df, output_df, col, cat_cols)
            rules.append({
                "value_type": "last_value",
                "column": best_src,
                "partition_by": partition,
            })
            continue
        
        # Check for nth value
        if any(kw in col_n for kw in nth_keywords):
            n_match = re.search(r'(\d+)', col_n)
            n = int(n_match.group(1)) if n_match else 1
            best_src = _find_rank_source(col_n, num_cols_in, input_df, output_df[col])
            partition = _detect_partition(input_df, output_df, col, cat_cols)
            rules.append({
                "value_type": "nth_value",
                "column": best_src,
                "n": n,
                "partition_by": partition,
            })
            continue
    
    return rules


def build_value_window_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect window value functions: FIRST_VALUE, LAST_VALUE, NTH_VALUE.
    
    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"value_type": "first_value"|"last_value"|"nth_value",
           "column": str, "n": int | None, "partition_by": [str] | None}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}
    
    out_df = _remap_output(output_df, column_mapping)
    raw = _detect_value_window_rules(input_df, out_df)
    
    rules = []
    for r in raw:
        rule = {
            "value_type": r["value_type"],
            "column": _to_output_col(r["column"], column_mapping),
            "partition_by": (
                [_to_output_col(c, column_mapping) for c in r["partition_by"]]
                if r.get("partition_by") else None
            ),
        }
        if "n" in r:
            rule["n"] = r["n"]
        rules.append(rule)
    
    return {"applied": len(rules) > 0, "details": {"rules": rules}}
