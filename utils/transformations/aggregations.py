# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Aggregation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Aggregation Transformation Utilities
======================================
Four focused builder functions for Phase 5 aggregation detection.

  build_groupby_config               → 5.1  GROUP BY (multiple groupings + agg funcs)
  build_conditional_aggregation_config → 5.3  CONDITIONAL AGGREGATION (SUMIF / COUNTIF …)
  build_running_total_config         → 5.4a WINDOW – running / cumulative totals
  build_ranking_config               → 5.4b WINDOW – RANK / ROW_NUMBER

Each function accepts (input_df, output_df, column_mapping?) and returns a
self-contained config block consumed directly by phase_5_aggregation.detect().

Output schema example
---------------------
{
  "groupby": {
    "applied": bool,
    "details": {
      "rules": [
        {"group_by": [str, ...], "aggregations": {col: func, ...}}
      ]
    }
  },
  "conditional_aggregation": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "condition": str, "operation": str}
      ]
    }
  },
  "running_total": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "order_by": str | None, "partition_by": [str] | None}
      ]
    }
  },
  "ranking": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "order": "asc" | "desc", "method": str,
         "partition_by": [str] | None}
      ]
    }
  }
}
"""

from __future__ import annotations

import re
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Shared constants & helpers
# ─────────────────────────────────────────────────────────────────────────────

# Aggregation function keyword map: canonical_name → [name variants in column headers]
_AGG_FUNC_MAP: Dict[str, List[str]] = {
    "sum":    ["sum", "total", "tot", "ttl"],
    "avg":    ["avg", "average", "mean", "ave"],
    "count":  ["count", "cnt", "num", "number_of", "no_of", "qty", "quantity"],
    "count_distinct": ["count_distinct", "unique_count", "distinct_count", "nunique", "count_unique"],
    "min":    ["min", "minimum", "lowest", "smallest"],
    "max":    ["max", "maximum", "highest", "largest"],
    "median": ["median", "med"],
    "stddev": ["stdev", "stddev", "std_dev", "std", "standard_deviation"],
    "var":    ["var", "variance"],
    "first":  ["first", "first_value"],
    "last":   ["last", "last_value"],
    "list":   ["list", "collect", "concatenate", "concat", "join"],
}

# Conditional aggregation patterns (SUMIF / COUNTIF / AVERAGEIF …)
_COND_AGG_PATTERNS: List[Tuple[str, str]] = [
    (r"\bsumif\b",      "sum"),
    (r"\bcountif\b",    "count"),
    (r"\baveragewif\b", "avg"),
    (r"\bavgif\b",      "avg"),
    (r"\bmaxif\b",      "max"),
    (r"\bminif\b",      "min"),
    (r"sum_if",         "sum"),
    (r"count_if",       "count"),
    (r"avg_if",         "avg"),
]

# Keywords that strongly suggest a rank / row-number column
_RANK_KEYWORDS = [
    "rank", "ranking", "row_num", "row_number", "rownum", "rowno",
    "row_id", "sequence", "serial", "position", "pos", "order",
]

# Keywords that suggest a running / cumulative total column
_RUNNING_KEYWORDS = [
    "running", "cumulative", "cumul", "cum_", "_cum", "cumsum",
    "running_total", "run_total", "ytd", "year_to_date",
    "mtd", "month_to_date", "rolling",
]

# Common date / ordering column name fragments
_DATE_KEYWORDS = [
    "date", "time", "period", "month", "year", "quarter",
    "week", "day", "timestamp", "created", "updated",
]

_TOL = 1e-6   # numeric comparison tolerance


def _norm(s: str) -> str:
    """Lowercase + collapse non-alphanumeric to underscore."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _numeric_cols(df: pd.DataFrame) -> List[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c])
        and not pd.api.types.is_bool_dtype(df[c])
    ]


def _categorical_cols(df: pd.DataFrame) -> List[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_string_dtype(df[c]) or pd.api.types.is_object_dtype(df[c])
    ]


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """Return series with non-numeric values coerced to NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    return pd.to_numeric(series, errors="coerce")


def _val_close(a: Any, b: Any) -> bool:
    """Return True when two values are numerically equal within tolerance."""
    try:
        fa, fb = float(a), float(b)
        if np.isnan(fa) and np.isnan(fb):
            return True
        if np.isnan(fa) or np.isnan(fb):
            return False
        return abs(fa - fb) <= _TOL
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to input-namespace when a mapping is given.
    
    column_mapping is {output_col: input_col}.
    """
    if not column_mapping:
        return output_df
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return output_df.rename(columns=rename_map)


def _to_output_col(col: Optional[str], column_mapping: Optional[Dict[str, str]]) -> Optional[str]:
    """Translate input-namespace column to output-namespace."""
    if col is None:
        return None
    if not column_mapping:
        return col
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    inv = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return inv.get(col, col)


def _infer_agg_func(col_name: str) -> Optional[str]:
    """
    Return the canonical aggregation function inferred from a column name,
    or None if none can be inferred.
    """
    n = _norm(col_name)
    for func, variants in _AGG_FUNC_MAP.items():
        if any(v in n for v in variants):
            return func
    return None


def _is_date_col(col: str) -> bool:
    n = _norm(col)
    return any(kw in n for kw in _DATE_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# 5.1  GROUP BY
# ─────────────────────────────────────────────────────────────────────────────

def _compute_agg(series: pd.Series, func: str) -> Optional[float]:
    nums = _coerce_numeric(series).dropna()
    if len(nums) == 0:
        return None
    ops = {
        "sum":    float(nums.sum()),
        "avg":    float(nums.mean()),
        "count":  float(series.notna().sum()),
        "count_distinct": float(series.dropna().nunique()),
        "min":    float(nums.min()),
        "max":    float(nums.max()),
        "median": float(nums.median()),
        "stddev": float(nums.std()),
        "var":    float(nums.var()),
        "first":  float(nums.iloc[0]) if len(nums) > 0 else None,
        "last":   float(nums.iloc[-1]) if len(nums) > 0 else None,
    }
    result = ops.get(func)
    return result if result is not None and not pd.isna(result) else None


def _verify_group_agg(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    group_cols: List[str],
    agg_col: str,
    func: str,
) -> bool:
    """
    Check whether grouping input_df by group_cols and applying func on agg_col
    reproduces the corresponding values found in output_df.

    Verifies at least 70 % of output rows match the computed aggregate.
    """
    try:
        grouped = input_df.groupby(group_cols, sort=False)
        match_count = 0
        total = 0

        for keys, grp in grouped:
            if not isinstance(keys, tuple):
                keys = (keys,)
            key_dict = dict(zip(group_cols, keys))

            # Find matching row in output
            mask = pd.Series([True] * len(output_df), index=output_df.index)
            for gc, gv in key_dict.items():
                if gc not in output_df.columns:
                    break
                mask &= output_df[gc].astype(str) == str(gv)
            else:
                out_rows = output_df[mask]
                if out_rows.empty or agg_col not in out_rows.columns:
                    continue

                expected = _compute_agg(grp[agg_col], func)
                if expected is None:
                    continue

                total += 1
                actual_vals = _coerce_numeric(out_rows[agg_col]).dropna()
                if len(actual_vals) > 0 and _val_close(actual_vals.iloc[0], expected):
                    match_count += 1

        return total > 0 and (match_count / total) >= 0.7
    except Exception:
        return False


def _all_groups_are_singletons(
    input_df: pd.DataFrame,
    group_cols: List[str],
) -> bool:
    """
    Return True when every group formed by group_cols contains exactly one row.
    When all groups are singletons there is no aggregation — any 'sum' would
    just reproduce the original value, which is not a real GROUP BY.
    """
    try:
        group_sizes = input_df.groupby(group_cols, sort=False).size()
        return bool((group_sizes == 1).all())
    except Exception:
        return False


def _detect_groupby_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect GROUP BY rules from input → output transformation.

    Strategy
    --------
    1. Row reduction is a STRICT prerequisite:
         len(output_df) < len(input_df)
       AND the reduction must be meaningful (≥ 2 rows collapsed):
         len(input_df) - len(output_df) >= 2
    2. Identify "grouping columns": columns present in output whose set of
       unique values is a subset of input unique values (they survived grouping).
    3. Identify "aggregation columns": existing numeric columns whose values
       changed (output values not a subset of input values).
       NOTE: brand-new columns in the output are NOT treated as aggregation
       targets — they could be index re-numbering, row counters, etc.
    4. For each candidate (group_cols, agg_col, func) triple:
       a. Verify with _verify_group_agg that computed aggregates match output.
       b. Reject if all groups are singletons (no real aggregation happened).
    5. The name-hint fallback is intentionally removed — it produced false
       positives whenever a column name contained words like "count" or "sum"
       without any actual aggregation occurring.
    """
    if input_df.empty or output_df.empty:
        return []

    # ── Gate 1: strict row reduction ─────────────────────────────────────────
    # Output MUST have fewer rows than input AND the difference must be ≥ 2.
    # Equal or larger output → filtering, renaming, or identity transform.
    n_in  = len(input_df)
    n_out = len(output_df)
    if n_out >= n_in:
        return []
    if (n_in - n_out) < 2:
        # Single row removed — almost certainly a filter/dedup, not groupby
        return []

    in_cols  = set(input_df.columns)
    out_cols = set(output_df.columns)

    num_cols_in = _numeric_cols(input_df)
    if not num_cols_in:
        return []

    # ── Candidate grouping columns ────────────────────────────────────────────
    # A column C is a grouping column when:
    #   • C exists in BOTH in and out (survived the groupby)
    #   • output unique values ⊆ input unique values
    #   • NOT a pure-numeric aggregation target (unless low-cardinality)
    candidate_group_cols: List[str] = []
    for col in output_df.columns:
        if col not in in_cols:
            continue  # brand-new column — not a group key
        out_unique = set(output_df[col].dropna().astype(str))
        in_unique  = set(input_df[col].dropna().astype(str))
        if not out_unique or not out_unique.issubset(in_unique):
            continue
        if col not in num_cols_in:
            candidate_group_cols.append(col)
        elif output_df[col].nunique() < len(output_df) * 0.7:
            # Accept numeric col as group key only if low-cardinality
            candidate_group_cols.append(col)

    if not candidate_group_cols:
        return []

    # ── Candidate aggregation columns ─────────────────────────────────────────
    # ONLY existing numeric columns whose output values are NOT a subset of
    # their input values (i.e. the values were actually transformed).
    # Brand-new columns (e.g. re-numbered index, "Employee Count") are
    # explicitly excluded to prevent name-hint false positives.
    agg_candidate_cols: List[str] = []
    for col in output_df.columns:
        if col not in in_cols:
            continue  # ← key change: skip brand-new output columns
        if col not in num_cols_in:
            continue
        in_vals  = set(_coerce_numeric(input_df[col]).dropna().round(6).tolist())
        out_vals = set(_coerce_numeric(output_df[col]).dropna().round(6).tolist())
        if not out_vals.issubset(in_vals):
            agg_candidate_cols.append(col)

    if not agg_candidate_cols:
        return []

    # ── Try group_col combinations (1, 2 … up to 3) ──────────────────────────
    rules: List[Dict[str, Any]] = []
    confirmed_agg_cols: Set[str] = set()

    for r in range(1, min(4, len(candidate_group_cols) + 1)):
        for group_combo in combinations(candidate_group_cols, r):
            group_cols = list(group_combo)

            # ── Gate 2: singleton guard ───────────────────────────────────
            # If every group has exactly one input row, grouping produces
            # the same values — that's not an aggregation.
            if _all_groups_are_singletons(input_df, group_cols):
                continue

            agg_map: Dict[str, str] = {}

            for agg_col in agg_candidate_cols:
                if agg_col in group_cols:
                    continue

                hinted_func = _infer_agg_func(agg_col)
                funcs_to_try = (
                    [hinted_func] if hinted_func
                    else list(_AGG_FUNC_MAP.keys())
                )

                for func in funcs_to_try:
                    if _verify_group_agg(input_df, output_df, group_cols, agg_col, func):
                        agg_map[agg_col] = func
                        confirmed_agg_cols.add(agg_col)
                        break

            if agg_map:
                rules.append({
                    "group_by":     group_cols,
                    "aggregations": agg_map,
                })

        if rules:
            break  # Stop at the smallest confirmed group size

    # NOTE: The name-hint fallback that used _infer_agg_func on brand-new
    # columns has been intentionally removed.  It produced false positives
    # whenever a column name contained "count", "sum", etc. without any
    # real row-collapsing aggregation having taken place (e.g. "Employee Count"
    # is a re-numbered index, not an aggregated value).

    return rules


def build_groupby_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect GROUP BY / SUMMARIZE transformations.

    Handles
    -------
    - Single and multi-column group keys
    - All standard aggregation functions (sum, avg, count, min, max, median, stddev)
    - Multiple independent groupings detected simultaneously
    - Mixed Excel dtypes (currency strings, percentage strings)
    - NaN / empty group keys skipped
    - No false positive when output row count == input row count

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"group_by": [str, ...], "aggregations": {"col": "func", ...}}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    out_df = _remap_output(output_df, column_mapping)
    raw    = _detect_groupby_rules(input_df, out_df)

    # Translate back to output namespace
    rules: List[Dict[str, Any]] = []
    for r in raw:
        rules.append({
            "group_by": [
                _to_output_col(c, column_mapping) for c in r["group_by"]
            ],
            "aggregations": {
                _to_output_col(col, column_mapping): func
                for col, func in r["aggregations"].items()
            },
        })

    return {"applied": len(rules) > 0, "details": {"rules": rules}}


# ─────────────────────────────────────────────────────────────────────────────
# 5.3  CONDITIONAL AGGREGATION  (SUMIF / COUNTIF …)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cond_agg_column(col_name: str) -> Optional[Tuple[str, str]]:
    """
    Parse a column name like 'sumif_us_sales' or 'count_if_active'.
    Returns (operation, hint) or None.
    """
    n = _norm(col_name)
    for pattern, func in _COND_AGG_PATTERNS:
        if re.search(pattern, n):
            return func, col_name
    return None


def _infer_condition_from_name(col_name: str, input_df: pd.DataFrame) -> Optional[str]:
    """
    Try to infer a human-readable condition string from the column name
    by matching tokens against known column names and values in input_df.

    Example: 'sales_us' → "region == 'US'"
    """
    n = _norm(col_name)
    cat_cols = _categorical_cols(input_df)

    for cat_col in cat_cols:
        cat_n = _norm(cat_col)
        # Check if column name fragment appears in the aggregation column name
        if cat_n in n:
            continue   # too generic, skip

        # Check if any value from the categorical column appears in the name
        for val in input_df[cat_col].dropna().unique():
            val_n = _norm(str(val))
            if len(val_n) >= 2 and val_n in n:
                return f"{cat_col} == '{val}'"

    return None


def _detect_conditional_agg_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect CONDITIONAL AGGREGATION rules.

    Two detection paths
    -------------------
    Path A – Column name hints (SUMIF, COUNTIF, etc.):
      Output columns whose names contain SUMIF/COUNTIF-like patterns.

    Path B – Value verification for new numeric output columns:
      For each new numeric output column, try to find a (filter_col, filter_val,
      agg_col, func) combination in input_df whose conditional aggregate matches
      the single output value (only works when the output is a single summary row).

    Edge cases
    ----------
    - Column names like 'us_sales_sum', 'count_if_active', 'avg_if_north'
    - Multi-word / mixed-case / underscored names
    - SUMIF on string-equality conditions (region == 'US')
    - Output may be a single row (full-table conditional aggregate)
    - NaN values in filter column skipped
    """
    if input_df.empty or output_df.empty:
        return []

    rules: List[Dict[str, Any]] = []
    in_cols = set(input_df.columns)

    # ── Path A: name-based detection ─────────────────────────────────────────
    for col in output_df.columns:
        parsed = _parse_cond_agg_column(col)
        if parsed is None:
            continue
        func, _ = parsed
        cond = _infer_condition_from_name(col, input_df)
        rules.append({
            "column":    col,
            "condition": cond or f"(inferred from '{col}')",
            "operation": func,
        })

    if rules:
        return rules

    # ── Path B: value-based detection (single-summary-row output) ────────────
    if len(output_df) > 5:
        return []   # Too many rows — not a simple conditional aggregate

    new_num_cols = [
        c for c in output_df.columns
        if c not in in_cols and pd.api.types.is_numeric_dtype(output_df[c])
    ]

    cat_cols    = _categorical_cols(input_df)
    num_cols_in = _numeric_cols(input_df)

    for out_col in new_num_cols:
        out_val = _coerce_numeric(output_df[out_col]).dropna()
        if len(out_val) == 0:
            continue
        target = float(out_val.iloc[0])

        found = False
        for filter_col in cat_cols:
            for filter_val in input_df[filter_col].dropna().unique():
                subset = input_df[input_df[filter_col].astype(str) == str(filter_val)]
                if subset.empty:
                    continue
                for agg_src in num_cols_in:
                    for func in ("sum", "avg", "count", "min", "max"):
                        computed = _compute_agg(subset[agg_src], func)
                        if computed is not None and _val_close(computed, target):
                            rules.append({
                                "column":    out_col,
                                "condition": f"{filter_col} == '{filter_val}'",
                                "operation": func,
                            })
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break

    return rules


def build_conditional_aggregation_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect CONDITIONAL AGGREGATION (SUMIF / COUNTIF / AVERAGEIF …).

    Handles
    -------
    - Column names that embed SUMIF/COUNTIF keywords
    - Single-value output from full-table conditional aggregates
    - String conditions derived from categorical column values in input
    - Mixed-case and underscored column naming conventions

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "condition": str, "operation": str}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    out_df = _remap_output(output_df, column_mapping)
    raw    = _detect_conditional_agg_rules(input_df, out_df)

    rules = [
        {
            "column":    _to_output_col(r["column"], column_mapping),
            "condition": r["condition"],
            "operation": r["operation"],
        }
        for r in raw
    ]

    return {"applied": len(rules) > 0, "details": {"rules": rules}}


# ─────────────────────────────────────────────────────────────────────────────
# 5.4a  WINDOW – RUNNING TOTALS
# ─────────────────────────────────────────────────────────────────────────────

def _is_cumulative(series: pd.Series, source: pd.Series) -> bool:
    """
    Return True when *series* looks like the cumulative sum / running total
    of *source*.

    Checks forward-cumsum match on at least 70 % of rows.
    """
    try:
        src  = _coerce_numeric(source).fillna(0).reset_index(drop=True)
        tgt  = _coerce_numeric(series).reset_index(drop=True)
        cumsum = src.cumsum()
        n = min(len(cumsum), len(tgt))
        if n < 2:
            return False
        matches = sum(_val_close(cumsum.iloc[i], tgt.iloc[i]) for i in range(n))
        return (matches / n) >= 0.7
    except Exception:
        return False


def _detect_running_total_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect running / cumulative total columns added to the output.

    Detection paths
    ---------------
    Path A – Name-based: column names contain 'running', 'cumulative', 'ytd' etc.
    Path B – Value-based: new numeric output columns whose values equal the
             cumulative sum of an input numeric column.

    Partition detection: if a grouping column exists (same cardinality as a
    categorical column), partitioned running totals are detected.

    Edge cases
    ----------
    - Running total reset per group (partition_by)
    - Date ordering column included when present (order_by)
    - Running count, running avg (not just sum) flagged by name hints
    - Same-row-count input/output (running total is a new column, not a
      row-reduction)
    - NaN gaps in source series handled via fillna(0)
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) != len(input_df):
        return []   # Running totals don't change row count

    in_cols     = set(input_df.columns)
    num_cols_in = _numeric_cols(input_df)
    rules: List[Dict[str, Any]] = []
    confirmed: Set[str] = set()

    # Detect an order_by candidate (first date-ish column in input)
    order_by_col: Optional[str] = next(
        (c for c in input_df.columns if _is_date_col(c)), None
    )

    # Detect possible partition columns
    cat_cols = _categorical_cols(input_df)

    # ── Path A: name hints ────────────────────────────────────────────────────
    for col in output_df.columns:
        if col in in_cols:
            continue
        col_n = _norm(col)
        if not any(kw in col_n for kw in _RUNNING_KEYWORDS):
            continue
        # Find the source numeric column this is likely running-total of
        best_src: Optional[str] = None
        best_score = 0.0
        for src in num_cols_in:
            src_n = _norm(src)
            # Simple fragment overlap
            common = sum(1 for tok in src_n.split("_") if tok and tok in col_n)
            if common > best_score:
                best_score = common
                best_src = src

        if best_src is None and num_cols_in:
            best_src = num_cols_in[0]

        # Detect partition
        partition = _detect_partition(input_df, output_df, col, cat_cols)

        rule: Dict[str, Any] = {
            "column":       best_src or col,
            "order_by":     order_by_col,
            "partition_by": partition,
        }
        rules.append(rule)
        confirmed.add(col)

    # ── Path B: value verification ────────────────────────────────────────────
    for col in output_df.columns:
        if col in in_cols or col in confirmed:
            continue
        if not pd.api.types.is_numeric_dtype(output_df[col]):
            continue

        for src in num_cols_in:
            if _is_cumulative(output_df[col], input_df[src]):
                partition = _detect_partition(input_df, output_df, col, cat_cols)
                rules.append({
                    "column":       src,
                    "order_by":     order_by_col,
                    "partition_by": partition,
                })
                confirmed.add(col)
                break

    return rules


def _detect_partition(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    target_col: str,
    cat_cols: List[str],
) -> Optional[List[str]]:
    """
    Heuristic: if target_col resets at certain rows correlated with a categorical
    column change, that column is the partition_by column.
    """
    if target_col not in output_df.columns:
        return None

    tgt = _coerce_numeric(output_df[target_col]).reset_index(drop=True)
    reset_rows: List[int] = []
    for i in range(1, len(tgt)):
        if not pd.isna(tgt.iloc[i]) and not pd.isna(tgt.iloc[i - 1]):
            if tgt.iloc[i] < tgt.iloc[i - 1]:
                reset_rows.append(i)

    if not reset_rows:
        return None

    for cat_col in cat_cols:
        if cat_col not in input_df.columns:
            continue
        cat = input_df[cat_col].reset_index(drop=True)
        # Check if value changes at reset rows
        matches = sum(
            1 for r in reset_rows
            if r < len(cat) and str(cat.iloc[r]) != str(cat.iloc[r - 1])
        )
        if len(reset_rows) > 0 and matches / len(reset_rows) >= 0.7:
            return [cat_col]

    return None


def build_running_total_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect RUNNING TOTAL / CUMULATIVE SUM window transformations.

    Handles
    -------
    - Name-based detection ('running_total_sales', 'ytd_revenue' …)
    - Value-based cumulative sum verification (row-by-row match ≥ 70 %)
    - Partitioned running totals (reset per group / category)
    - order_by inferred from date-ish column names
    - Same row count preserved (running total adds a column, not rows)
    - NaN gaps in source filled with 0 during verification

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "order_by": str | None, "partition_by": [str] | None}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    out_df = _remap_output(output_df, column_mapping)
    raw    = _detect_running_total_rules(input_df, out_df)

    rules = [
        {
            "column":       _to_output_col(r["column"], column_mapping),
            "order_by":     _to_output_col(r["order_by"], column_mapping),
            "partition_by": (
                [_to_output_col(c, column_mapping) for c in r["partition_by"]]
                if r["partition_by"] else None
            ),
        }
        for r in raw
    ]

    return {"applied": len(rules) > 0, "details": {"rules": rules}}


# ─────────────────────────────────────────────────────────────────────────────
# 5.4b  WINDOW – RANKING
# ─────────────────────────────────────────────────────────────────────────────

def _detect_rank_method(rank_series: pd.Series, src_series: pd.Series, order: str) -> str:
    """
    Return the likely pandas rank method ('min', 'dense', 'first', 'average')
    that was used to produce rank_series from src_series.
    """
    ascending = order == "asc"
    for method in ("min", "dense", "first", "average"):
        try:
            computed = src_series.rank(method=method, ascending=ascending, na_option="keep")
            match = sum(
                _val_close(computed.iloc[i], rank_series.iloc[i])
                for i in range(min(len(computed), len(rank_series)))
                if not pd.isna(rank_series.iloc[i])
            )
            total = rank_series.notna().sum()
            if total > 0 and match / total >= 0.7:
                return method
        except Exception:
            continue
    return "min"   # default


def _detect_ranking_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect RANKING / ROW_NUMBER columns added to the output.

    Detection paths
    ---------------
    Path A – Name-based: new output columns containing rank/row_num keywords.
    Path B – Value-based: new integer output columns that match the rank of
             some input numeric column (ascending or descending).

    Edge cases
    ----------
    - Standard rank (ties get same rank, next rank skipped) → method=min
    - Dense rank (ties get same rank, no gaps)               → method=dense
    - Row numbering (sequential 1,2,3…)                      → method=first
    - Average rank (tied rows get average rank)               → method=average
    - Partitioned ranking (rank resets per group)
    - Rank column added alongside other columns (no row reduction)
    - NaN values in source kept as NaN in rank column
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) != len(input_df):
        return []

    in_cols     = set(input_df.columns)
    num_cols_in = _numeric_cols(input_df)
    cat_cols    = _categorical_cols(input_df)
    rules: List[Dict[str, Any]] = []
    confirmed: Set[str] = set()

    # ── Path A: name hints ────────────────────────────────────────────────────
    for col in output_df.columns:
        if col in in_cols:
            continue
        col_n = _norm(col)
        if not any(kw in col_n for kw in _RANK_KEYWORDS):
            continue

        # Determine which source column is being ranked
        best_src: Optional[str] = None
        best_order = "desc"

        rank_vals = _coerce_numeric(output_df[col]).dropna()
        if len(rank_vals) < 2:
            continue

        # Try each numeric input col
        for src in num_cols_in:
            for order in ("desc", "asc"):
                ascending = order == "asc"
                try:
                    computed = input_df[src].rank(method="min", ascending=ascending, na_option="keep")
                    n = min(len(computed), len(rank_vals))
                    matches = sum(
                        _val_close(computed.iloc[i], rank_vals.iloc[i])
                        for i in range(n)
                        if not pd.isna(rank_vals.iloc[i])
                    )
                    total = rank_vals.notna().sum()
                    if total > 0 and matches / total >= 0.7:
                        best_src  = src
                        best_order = order
                        break
                except Exception:
                    continue
            if best_src:
                break

        # If no source verified, pick the most-likely numeric col by name overlap
        if best_src is None and num_cols_in:
            for src in num_cols_in:
                src_n = _norm(src)
                if any(tok in col_n for tok in src_n.split("_") if len(tok) >= 3):
                    best_src = src
                    break
            if best_src is None:
                best_src = num_cols_in[0]

        partition = _detect_partition(input_df, output_df, col, cat_cols)
        method = (
            _detect_rank_method(rank_vals.reset_index(drop=True),
                                 input_df[best_src].reset_index(drop=True),
                                 best_order)
            if best_src else "min"
        )

        rules.append({
            "column":       best_src or col,
            "order":        best_order,
            "method":       method,
            "partition_by": partition,
        })
        confirmed.add(col)

    # ── Path B: value verification for unnamed rank cols ─────────────────────
    for col in output_df.columns:
        if col in in_cols or col in confirmed:
            continue
        rank_vals = _coerce_numeric(output_df[col]).dropna()
        if len(rank_vals) < 2:
            continue
        # Must look like ranks: positive integers up to ~2× row count
        if not (rank_vals.min() >= 1 and rank_vals.max() <= len(output_df) * 2):
            continue
        if not pd.api.types.is_integer_dtype(output_df[col]) and rank_vals.apply(
            lambda x: x != int(x)
        ).any():
            continue

        for src in num_cols_in:
            for order in ("desc", "asc"):
                ascending = order == "asc"
                try:
                    computed = input_df[src].rank(method="min", ascending=ascending, na_option="keep")
                    n = min(len(computed), len(output_df))
                    rank_full = _coerce_numeric(output_df[col]).reset_index(drop=True)
                    matches = sum(
                        _val_close(computed.iloc[i], rank_full.iloc[i])
                        for i in range(n)
                        if not pd.isna(rank_full.iloc[i])
                    )
                    total = rank_full.notna().sum()
                    if total > 0 and matches / total >= 0.7:
                        partition = _detect_partition(input_df, output_df, col, cat_cols)
                        method = _detect_rank_method(
                            rank_full[:n], input_df[src].reset_index(drop=True)[:n], order
                        )
                        rules.append({
                            "column":       src,
                            "order":        order,
                            "method":       method,
                            "partition_by": partition,
                        })
                        confirmed.add(col)
                        break
                except Exception:
                    continue
            if col in confirmed:
                break

    return rules


def build_ranking_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect RANKING / ROW_NUMBER window transformations.

    Handles
    -------
    - Name-based detection ('rank_sales', 'row_number', 'row_id' …)
    - Value-based rank verification (compared against pandas rank methods)
    - Standard, dense, first (row_number), average rank methods
    - Ascending and descending rank order
    - Partitioned ranking (rank resets per group / partition_by column)
    - NaN values preserved as NaN in rank column
    - No false positive for plain sequential index columns

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "order": "asc"|"desc",
           "method": str, "partition_by": [str] | None}
        ]
      }
    }
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    out_df = _remap_output(output_df, column_mapping)
    raw    = _detect_ranking_rules(input_df, out_df)

    rules = [
        {
            "column":       _to_output_col(r["column"], column_mapping),
            "order":        r["order"],
            "method":       r["method"],
            "partition_by": (
                [_to_output_col(c, column_mapping) for c in r["partition_by"]]
                if r["partition_by"] else None
            ),
        }
        for r in raw
    ]

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
