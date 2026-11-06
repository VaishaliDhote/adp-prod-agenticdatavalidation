"""
execution/execution_phase_6_aggregation.py
============================================
PHASE 6 — Aggregation
-----------------------
Why AFTER enrichment? Aggregate over already-enriched columns.

Operations (in order):
  28. GroupBy (ALL rules sequentially)      ← config["phase_5_aggregation"]["groupby"]
  29. Conditional aggregation               ← config["phase_5_aggregation"]["conditional_aggregation"]
  30. Running totals                        ← config["phase_5_aggregation"]["running_total"]
  31. Ranking                               ← config["phase_5_aggregation"]["ranking"]
  32. LAG / LEAD (NEW)                      ← config["phase_5_aggregation"]["lag_lead"]
  33. Advanced Ranking (NEW)                ← config["phase_5_aggregation"]["advanced_ranking"]
  34. Value Windows (NEW)                   ← config["phase_5_aggregation"]["value_windows"]

Config key : "phase_5_aggregation"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from utils.execution_helpers import col_name, col_exists


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply all aggregation operations defined in config["phase_5_aggregation"].

    Args:
        df     : DataFrame produced by Phase 5.
        config : Full merged config dict.

    Returns:
        Aggregated / enriched DataFrame.
    """
    phase          = config.get("phase_5_aggregation", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 6] No aggregation config found — skipping.")
        return df

    df = df.copy()

    # ── 28. GroupBy ───────────────────────────────────────────────────────────
    groupby_cfg = phase.get("groupby", {})
    print(f"  [Phase 6] GroupBy config: applied={groupby_cfg.get('applied')}")
    if groupby_cfg.get("applied"):
        already = groupby_cfg.get("details", {}).get("already_aggregated", False)
        if already:
            print("[Phase 6] GroupBy skipped — already_aggregated flag is True.")
        else:
            df = _apply_groupby(df, groupby_cfg, on_missing_col)
    else:
        print("  [Phase 6] GroupBy not applied (applied=False in config)")

    # ── 29. Conditional aggregation ───────────────────────────────────────────
    cond_agg_cfg = phase.get("conditional_aggregation", {})
    if cond_agg_cfg.get("applied"):
        df = _apply_conditional_agg(df, cond_agg_cfg, on_missing_col)

    # ── 30. Running totals ────────────────────────────────────────────────────
    running_cfg = phase.get("running_total", {})
    if running_cfg.get("applied"):
        df = _apply_running_total(df, running_cfg, on_missing_col)    # ── 31. Ranking ───────────────────────────────────────────────────────────
    ranking_cfg = phase.get("ranking", {})
    if ranking_cfg.get("applied"):
        df = _apply_ranking(df, ranking_cfg, on_missing_col)

    # ── 32. LAG / LEAD (NEW) ──────────────────────────────────────────────────
    lag_lead_cfg = phase.get("lag_lead", {})
    if lag_lead_cfg.get("applied"):
        df = _apply_lag_lead(df, lag_lead_cfg, on_missing_col)

    # ── 33. Advanced Ranking (NEW) ────────────────────────────────────────────
    adv_ranking_cfg = phase.get("advanced_ranking", {})
    if adv_ranking_cfg.get("applied"):
        df = _apply_advanced_ranking(df, adv_ranking_cfg, on_missing_col)

    # ── 34. Value Windows (NEW) ───────────────────────────────────────────────
    value_window_cfg = phase.get("value_windows", {})
    if value_window_cfg.get("applied"):
        df = _apply_value_windows(df, value_window_cfg, on_missing_col)

    print(f"[Phase 6] Aggregation complete. Shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PANDAS_AGG_MAP: Dict[str, str] = {
    "sum":            "sum",
    "avg":            "mean",
    "mean":           "mean",
    "count":          "count",
    "count_distinct": "nunique",
    "min":            "min",
    "max":            "max",
    "median":         "median",
    "stddev":         "std",
    "std":            "std",
    "var":            "var",
    "first":          "first",
    "last":           "last",
}


# ── 28. GroupBy ───────────────────────────────────────────────────────────────

def _apply_groupby(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Apply GROUP BY rules sequentially.

    Supports two config formats:
    
    Format 1 (simple):
      {"group_by": [col,...], "aggregations": {"col": "func", ...}}
    
    Format 2 (detailed - from LLM):
      {"group_columns": [col,...], "aggregations": {"out_col": {"agg_column": "src", "function": "func"}, ...}}

    Multiple rules: each rule produces its own aggregation — they are then
    merged back on their shared group_by keys.
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    if not rules:
        return df

    result_frames: List[pd.DataFrame] = []

    for rule in rules:
        # Support both "group_by" and "group_columns" keys
        group_by_refs = rule.get("group_by", rule.get("group_columns", []))
        agg_map       = rule.get("aggregations", {})

        group_cols = [col_name(c) for c in group_by_refs]
        group_cols = [c for c in group_cols if c and col_exists(df, c, on_missing, "Phase 6")]

        if not group_cols:
            print(f"  [Phase 6] GroupBy skipped: no valid group_by columns. Config had: {group_by_refs}")
            continue

        # Build pandas named aggregation dict
        # Uses pd.NamedAgg to allow multiple aggregations on same source column
        # Supports multiple LLM config formats:
        #   Format 1: {"Total_Sales": {"agg_column": "Sales", "function": "sum"}}
        #   Format 2: {"Total_Sales": {"source_column": "Sales", "aggfunc": "sum"}}
        #   Format 3: {"Sales": "sum"} (out_col is source column)
        named_aggs: Dict[str, pd.NamedAgg] = {}
        
        for out_col, agg_spec in agg_map.items():
            if isinstance(agg_spec, dict):
                # Format 1/2: dict with column and function keys (support multiple key names)
                src_col = (
                    agg_spec.get("agg_column") or 
                    agg_spec.get("source_column") or 
                    agg_spec.get("column") or 
                    ""
                )
                func = (
                    agg_spec.get("function") or 
                    agg_spec.get("aggfunc") or 
                    "count"
                )
            else:
                # Format 3: column: function (out_col is the source column)
                src_col = out_col
                func = str(agg_spec)
            
            src_col = col_name(src_col) if isinstance(src_col, dict) else str(src_col)
            
            if src_col not in df.columns:
                if on_missing == "skip":
                    print(f"  [Phase 6] Agg source column '{src_col}' not found — skipping '{out_col}'.")
                continue
            
            pandas_func = _PANDAS_AGG_MAP.get(str(func).lower(), func)
            named_aggs[out_col] = pd.NamedAgg(column=src_col, aggfunc=pandas_func)

        if not named_aggs:
            print("  [Phase 6] GroupBy skipped: no valid aggregation columns.")
            continue

        try:
            grouped = df.groupby(group_cols, sort=False, as_index=False).agg(**named_aggs)
            result_frames.append(grouped)
            print(f"  [Phase 6] GroupBy on {group_cols}: {len(named_aggs)} aggs → {len(grouped)} groups")
            print(f"  [Phase 6] Output columns: {list(grouped.columns)}")
        except Exception as exc:
            print(f"  [Phase 6] GroupBy failed: {exc}")

    if not result_frames:
        return df

    # If only one rule → replace df directly
    if len(result_frames) == 1:
        return result_frames[0]

    # Multiple rules → merge all results on shared columns
    merged = result_frames[0]
    for frame in result_frames[1:]:
        shared = [c for c in merged.columns if c in frame.columns]
        if shared:
            merged = merged.merge(frame, on=shared, how="outer")
        else:
            merged = pd.concat([merged, frame], axis=1)

    return merged


# ── 29. Conditional aggregation ───────────────────────────────────────────────

def _apply_conditional_agg(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Add new columns computed from SUMIF / COUNTIF style conditional aggregates.

    Rule shape:
      {"column": output_col, "condition": "expr_string", "operation": "sum"|...}

    The condition is evaluated against the original df to build a boolean mask.
    The aggregate is computed on the masked subset and assigned as a scalar
    new column (broadcast to all rows).
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        out_col   = col_name(rule.get("column", ""))
        condition = rule.get("condition", "")
        operation = str(rule.get("operation", "sum")).lower()

        if not out_col:
            continue

        try:
            if condition and condition.strip():
                # Try pandas eval; fall back to full-column aggregate
                mask = df.eval(condition)
                subset = df[mask]
            else:
                subset = df

            pandas_func = _PANDAS_AGG_MAP.get(operation, "sum")

            # Find a numeric column to aggregate (first numeric col not in group)
            numeric_cols = [c for c in subset.columns if pd.api.types.is_numeric_dtype(subset[c])]
            if not numeric_cols:
                print(f"  [Phase 6] Conditional agg '{out_col}': no numeric column — skipping.")
                continue

            agg_col    = numeric_cols[0]
            agg_result = getattr(pd.to_numeric(subset[agg_col], errors="coerce"), pandas_func)()
            df[out_col] = agg_result
            print(f"  [Phase 6] Conditional agg '{out_col}' = {operation}({agg_col}) where '{condition}'")

        except Exception as exc:
            print(f"  [Phase 6] Conditional agg '{out_col}' failed: {exc}")

    return df


# ── 30. Running totals ────────────────────────────────────────────────────────

def _apply_running_total(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Add running / cumulative total columns.

    Rule shape:
      {"column": src_col, "order_by": col|None,
       "partition_by": [col,...]|None}

    Output column name: "{src_col}_running_total"
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        src_col       = col_name(rule.get("column", ""))
        order_by_ref  = rule.get("order_by")
        partition_refs = rule.get("partition_by") or []

        if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
            continue

        order_by  = col_name(order_by_ref)  if order_by_ref   else None
        partition = [col_name(c) for c in partition_refs] if partition_refs else []
        partition = [c for c in partition if c in df.columns]
        out_col   = f"{src_col}_running_total"

        try:
            numeric = pd.to_numeric(df[src_col], errors="coerce")

            # Sort if order_by is specified
            if order_by and order_by in df.columns:
                sort_idx = df[order_by].argsort()
                numeric  = numeric.iloc[sort_idx].reset_index(drop=True)

            if partition:
                df[out_col] = df.groupby(partition, sort=False)[src_col].transform(
                    lambda s: pd.to_numeric(s, errors="coerce").cumsum()
                )
            else:
                df[out_col] = numeric.cumsum()

            print(f"  [Phase 6] Running total '{src_col}' → '{out_col}'"
                  + (f" (partitioned by {partition})" if partition else ""))

        except Exception as exc:
            print(f"  [Phase 6] Running total '{src_col}' failed: {exc}")

    return df


# ── 31. Ranking ───────────────────────────────────────────────────────────────

def _apply_ranking(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Add rank columns.

    Rule shape:
      {"column": src_col, "order": "asc"|"desc",
       "method": "min"|"dense"|"first"|"average",
       "partition_by": [col,...]|None}

    Output column name: "{src_col}_rank"
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        src_col        = col_name(rule.get("column", ""))
        order          = str(rule.get("order",  "desc")).lower()
        method         = str(rule.get("method", "min")).lower()
        partition_refs = rule.get("partition_by") or []

        if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
            continue

        partition = [col_name(c) for c in partition_refs] if partition_refs else []
        partition = [c for c in partition if c in df.columns]
        ascending = order == "asc"
        out_col   = f"{src_col}_rank"

        try:
            if partition:
                df[out_col] = df.groupby(partition, sort=False)[src_col].rank(
                    method=method, ascending=ascending, na_option="keep"
                )
            else:
                df[out_col] = pd.to_numeric(df[src_col], errors="coerce").rank(
                    method=method, ascending=ascending, na_option="keep"
                )
            print(f"  [Phase 6] Rank '{src_col}' → '{out_col}' ({order}, method={method}"
                  + (f", partitioned by {partition})" if partition else ")"))

        except Exception as exc:
            print(f"  [Phase 6] Ranking '{src_col}' failed: {exc}")

    return df


# ── 32. LAG / LEAD (NEW) ──────────────────────────────────────────────────────

def _apply_lag_lead(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """Add LAG (previous row value) or LEAD (next row value) columns."""
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        src_col        = col_name(rule.get("column", ""))
        periods        = int(rule.get("periods", 1))
        direction      = str(rule.get("direction", "lag")).lower()
        partition_refs = rule.get("partition_by") or []

        if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
            continue

        partition = [col_name(c) for c in partition_refs] if partition_refs else []
        partition = [c for c in partition if c in df.columns]
        
        shift_periods = periods if direction == "lag" else -periods
        out_col = f"{src_col}_{direction}_{periods}"

        try:
            if partition:
                df[out_col] = df.groupby(partition, sort=False)[src_col].shift(shift_periods)
            else:
                df[out_col] = df[src_col].shift(shift_periods)
            
            print(f"  [Phase 6] {direction.upper()} '{src_col}' -> '{out_col}' (periods={periods}"
                  + (f", partitioned by {partition})" if partition else ")"))

        except Exception as exc:
            print(f"  [Phase 6] {direction.upper()} '{src_col}' failed: {exc}")

    return df


# ── 33. Advanced Ranking (NEW) ────────────────────────────────────────────────

def _apply_advanced_ranking(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """Add advanced ranking columns: DENSE_RANK, PERCENT_RANK, NTILE, ROW_NUMBER."""
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        rank_type      = str(rule.get("rank_type", "")).lower()
        src_col_ref    = rule.get("column")
        n_buckets      = rule.get("n_buckets", 4)
        partition_refs = rule.get("partition_by") or []

        src_col = col_name(src_col_ref) if src_col_ref else None
        partition = [col_name(c) for c in partition_refs] if partition_refs else []
        partition = [c for c in partition if c in df.columns]

        try:
            if rank_type == "dense_rank":
                if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
                    continue
                out_col = f"{src_col}_dense_rank"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].rank(
                        method="dense", ascending=False, na_option="keep"
                    )
                else:
                    df[out_col] = pd.to_numeric(df[src_col], errors="coerce").rank(
                        method="dense", ascending=False, na_option="keep"
                    )
                print(f"  [Phase 6] DENSE_RANK '{src_col}' -> '{out_col}'"
                      + (f" (partitioned by {partition})" if partition else ""))

            elif rank_type == "percent_rank":
                if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
                    continue
                out_col = f"{src_col}_percent_rank"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].rank(
                        method="min", ascending=False, na_option="keep", pct=True
                    )
                else:
                    df[out_col] = pd.to_numeric(df[src_col], errors="coerce").rank(
                        method="min", ascending=False, na_option="keep", pct=True
                    )
                print(f"  [Phase 6] PERCENT_RANK '{src_col}' -> '{out_col}'"
                      + (f" (partitioned by {partition})" if partition else ""))

            elif rank_type == "ntile":
                if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
                    continue
                out_col = f"{src_col}_ntile_{n_buckets}"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].apply(
                        lambda x: pd.qcut(x.rank(method="first"), q=n_buckets, labels=False, duplicates="drop") + 1
                    )
                else:
                    df[out_col] = pd.qcut(
                        pd.to_numeric(df[src_col], errors="coerce").rank(method="first"),
                        q=n_buckets, labels=False, duplicates="drop"
                    ) + 1
                print(f"  [Phase 6] NTILE '{src_col}' -> '{out_col}' (n={n_buckets}"
                      + (f", partitioned by {partition})" if partition else ")"))

            elif rank_type == "row_number":
                out_col = "row_number" if not partition else f"{'_'.join(partition)}_row_number"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False).cumcount() + 1
                else:
                    df[out_col] = list(range(1, len(df) + 1))
                print(f"  [Phase 6] ROW_NUMBER -> '{out_col}'"
                      + (f" (partitioned by {partition})" if partition else ""))

        except Exception as exc:
            print(f"  [Phase 6] {rank_type.upper()} failed: {exc}")

    return df


# ── 34. Value Windows (NEW) ───────────────────────────────────────────────────

def _apply_value_windows(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """Add window value columns: FIRST_VALUE, LAST_VALUE, NTH_VALUE."""
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        value_type     = str(rule.get("value_type", "")).lower()
        src_col        = col_name(rule.get("column", ""))
        n_value        = rule.get("n", 1)
        partition_refs = rule.get("partition_by") or []

        if not src_col or not col_exists(df, src_col, on_missing, "Phase 6"):
            continue

        partition = [col_name(c) for c in partition_refs] if partition_refs else []
        partition = [c for c in partition if c in df.columns]

        try:
            if value_type == "first_value":
                out_col = f"{src_col}_first_value"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].transform("first")
                else:
                    df[out_col] = df[src_col].iloc[0]
                print(f"  [Phase 6] FIRST_VALUE '{src_col}' -> '{out_col}'"
                      + (f" (partitioned by {partition})" if partition else ""))

            elif value_type == "last_value":
                out_col = f"{src_col}_last_value"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].transform("last")
                else:
                    df[out_col] = df[src_col].iloc[-1]
                print(f"  [Phase 6] LAST_VALUE '{src_col}' -> '{out_col}'"
                      + (f" (partitioned by {partition})" if partition else ""))

            elif value_type == "nth_value":
                out_col = f"{src_col}_nth_value_{n_value}"
                if partition:
                    df[out_col] = df.groupby(partition, sort=False)[src_col].transform(
                        lambda x: x.iloc[n_value - 1] if len(x) >= n_value else np.nan
                    )
                else:
                    nth_val = df[src_col].iloc[n_value - 1] if len(df) >= n_value else np.nan
                    df[out_col] = nth_val
                print(f"  [Phase 6] NTH_VALUE '{src_col}' -> '{out_col}' (n={n_value}"
                      + (f", partitioned by {partition})" if partition else ")"))

        except Exception as exc:
            print(f"  [Phase 6] {value_type.upper()} '{src_col}' failed: {exc}")

    return df
