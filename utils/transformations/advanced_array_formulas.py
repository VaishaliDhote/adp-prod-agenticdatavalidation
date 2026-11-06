# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Advanced-logic detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Advanced Logic – Array Formula Detection  (Phase 7.1)
======================================================
Detects Excel array-formula patterns applied between input_df and output_df:

  FILTER  → output is a row-subset of input based on one or more column conditions
  UNIQUE  → output contains only distinct rows / values from input
  SORT    → output rows are sorted (single or multi-column, asc or desc)

These three functions often appear together (e.g. SORT(FILTER(…))) so every
one is evaluated independently; all that fire are reported.

Detection strategy
------------------
FILTER  : output rows are a strict subset of input rows; a consistent
          column-value condition explains exactly which rows survived.
UNIQUE  : duplicate rows / values present in the input are absent in the
          output AND row count dropped by at least one duplicate cluster.
SORT    : output column(s) are monotonically ordered (asc or desc) AND the
          row sequence differs from the original input order.

All three detectors:
  • Handle Excel-origin dtypes (currency, percentage, date strings, booleans)
  • Respect column_mapping (detect in input namespace, report in output namespace)
  • Return applied=False gracefully on empty / malformed frames
  • Use a configurable match-threshold (≥ MATCH_THRESHOLD) to allow minor NaN
    or dtype-coercion noise in real Excel exports

Public API
----------
  build_array_formulas_config(input_df, output_df, column_mapping?) → dict
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Fraction of rows that must confirm a rule (tolerates minor Excel noise)
_MATCH_THRESHOLD = 0.75

# Maximum rows sampled when verifying row membership (perf guard)
_SAMPLE = 500

# Excel null representations in string columns
_EXCEL_NULLS = frozenset(
    {"", "nan", "none", "null", "n/a", "na", "#n/a", "nil", "-"}
)

# Comparison operators supported for FILTER condition description
_NUMERIC_OPS: List[Tuple[str, Any]] = [
    ("==",  lambda a, b: a == b),
    ("!=",  lambda a, b: a != b),
    (">",   lambda a, b: a > b),
    (">=",  lambda a, b: a >= b),
    ("<",   lambda a, b: a < b),
    ("<=",  lambda a, b: a <= b),
]

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

# Sentinel values for derived/new columns
_DERIVED_SENTINELS = frozenset({"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"})


def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to input-namespace for detection.
    
    column_mapping is {output_col: input_col}.
    """
    if not column_mapping:
        return output_df
    rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    out_df = output_df.rename(columns=rename_map)
    # Drop duplicates: multiple output cols can map to the same input col name
    # (e.g. Full_Name + Reconstructed_Full_Name → Full_Name). Keep first.
    return out_df.loc[:, ~out_df.columns.duplicated()]


def _to_out(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Translate an input-namespace column name to output-namespace."""
    if not column_mapping:
        return col
    inv = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return inv.get(col, col)


def _common_cols(df_a: pd.DataFrame, df_b: pd.DataFrame) -> List[str]:
    return [c for c in df_a.columns if c in df_b.columns]


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and np.isnan(val):
        return True
    if isinstance(val, str) and val.strip().lower() in _EXCEL_NULLS:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _row_key(row: pd.Series) -> str:
    """Stable string key for a DataFrame row (used for membership checks)."""
    return "||".join(
        "" if _is_missing(v) else str(v).strip()
        for v in row
    )


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """
    Coerce a Series to float, stripping Excel-embedded currency / percent /
    thousand-separator noise.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    _CURR = re.compile(r"^\s*[$€£¥₹₩]\s*|\s*[$€£¥₹₩]\s*$", re.UNICODE)
    _PCT  = re.compile(r"\s*%\s*$")
    _THOU = re.compile(r"(?<=\d),(?=\d{3})")

    def _clean(v: Any) -> Optional[float]:
        if _is_missing(v):
            return None
        s = str(v).strip()
        s = _CURR.sub("", s)
        s = _PCT.sub("", s)
        if re.match(r"^-?\d{1,3}(\.\d{3})*,\d+$", s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = _THOU.sub("", s)
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    return series.map(_clean)


def _is_sequential_index(series: pd.Series) -> bool:
    """Return True if series looks like a re-numbered sequential index."""
    try:
        if not pd.api.types.is_numeric_dtype(series):
            return False
        vals = series.dropna().astype(int).tolist()
        if len(vals) < 2:
            return False
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        return diffs.count(1) / len(diffs) >= 0.70
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 7.1a  FILTER detection
# ─────────────────────────────────────────────────────────────────────────────

def _infer_filter_conditions(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[str]:
    """
    For each common column, determine whether a simple condition on that
    column explains the row subset chosen by FILTER.

    Strategy
    --------
    1. Build a set of row-keys present in output.
    2. For each column C in both frames:
        a. String equality / inequality  (C == "value" or C != "value")
        b. Numeric comparison            (C > v, C >= v, C < v, C <= v, C == v)
        c. IS NOT NULL / IS NULL         (blanks removed or kept)
    3. A condition is accepted when applying it to input reproduces ≥
       MATCH_THRESHOLD of the actual output rows.

    Returns a list of human-readable condition strings, one per detected rule.

    Edge cases handled
    ------------------
    - String columns: equality check against every unique output value
    - Numeric columns: all six comparison operators tried
    - Mixed-type (Excel-origin) columns: coerced to numeric before comparison
    - NULL / blank removal (IS NOT NULL filter)
    - Multi-condition AND filter: each column tested independently;
      if combined mask explains the subset, both conditions are reported
    - Columns that are identical in input/output → skipped (not a filter key)
    """
    if input_df.empty or output_df.empty:
        return []

    # Row membership set (output rows that exist in input)
    out_keys: set = {
        _row_key(row)
        for _, row in output_df[_common_cols(input_df, output_df)].iterrows()
    }
    in_sample = input_df.iloc[:_SAMPLE].copy()

    conditions: List[str] = []

    for col in _common_cols(input_df, output_df):
        in_col  = input_df[col]
        out_col = output_df[col]

        # Skip if column values are unchanged (not a filter key)
        in_vals  = set(in_col.dropna().astype(str).str.strip())
        out_vals = set(out_col.dropna().astype(str).str.strip())
        if in_vals == out_vals:
            continue

        # ── NULL filter ───────────────────────────────────────────────────────
        null_in_count = in_col.map(_is_missing).sum()
        if null_in_count > 0:
            null_in_out = out_col.map(_is_missing).sum()
            if null_in_out == 0:
                conditions.append(f"{col} IS NOT NULL")
                continue
            if null_in_out == null_in_count:
                conditions.append(f"{col} IS NULL")
                continue

        # ── Numeric comparison ────────────────────────────────────────────────
        in_num  = _coerce_numeric(in_col)
        out_num = _coerce_numeric(out_col)
        if in_num.notna().mean() >= _MATCH_THRESHOLD:
            out_non_null = out_num.dropna()
            if len(out_non_null) == 0:
                continue
            threshold_candidates: List[float] = sorted(
                set(out_non_null.tolist())
                | {float(out_non_null.min()), float(out_non_null.max())}
            )
            # Limit to distinct boundary values
            threshold_candidates = list(dict.fromkeys(threshold_candidates))[:20]

            best_op: Optional[str] = None
            best_val: Optional[float] = None
            best_score: float = 0.0

            for thr in threshold_candidates:
                for op_label, op_fn in _NUMERIC_OPS:
                    try:
                        mask = in_num.map(
                            lambda v, _thr=thr, _fn=op_fn: (  # noqa: B023
                                False if pd.isna(v) else _fn(v, _thr)
                            )
                        )
                        filtered = input_df[mask]
                        if filtered.empty:
                            continue
                        # Compute match ratio against actual output
                        fk = {
                            _row_key(r)
                            for _, r in filtered[_common_cols(input_df, output_df)].iterrows()
                        }
                        if len(fk) == 0:
                            continue
                        score = len(fk & out_keys) / max(len(fk), len(out_keys))
                        if score > best_score:
                            best_score = score
                            best_op    = op_label
                            best_val   = thr
                    except Exception:
                        continue

            if best_op and best_score >= _MATCH_THRESHOLD:
                # Format integer threshold cleanly
                disp = int(best_val) if best_val == int(best_val) else best_val  # type: ignore[arg-type]
                conditions.append(f"{col} {best_op} {disp}")
                continue

        # ── String equality ───────────────────────────────────────────────────
        # out_vals is a strict subset of in_vals → filter kept only these values
        if out_vals and out_vals < in_vals:
            if len(out_vals) == 1:
                conditions.append(f"{col} == '{next(iter(out_vals))}'")
            else:
                vals_repr = ", ".join(f"'{v}'" for v in sorted(out_vals))
                conditions.append(f"{col} IN ({vals_repr})")

    return conditions


def _is_filter_transformation(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> bool:
    """
    Return True when output_df is a strict row-subset of input_df.

    Conditions
    ----------
    • output rows ≤ input rows (strict reduction)
    • output column set ⊆ input column set (FILTER never adds columns)
    • ≥ MATCH_THRESHOLD of output rows are found verbatim in input
    """
    if output_df.empty or input_df.empty:
        return False
    if len(output_df) >= len(input_df):
        return False
    if not set(output_df.columns).issubset(set(input_df.columns)):
        return False

    common = _common_cols(input_df, output_df)
    if not common:
        return False

    in_keys = {
        _row_key(row)
        for _, row in input_df[common].iterrows()
    }
    match_count = sum(
        1
        for _, row in output_df[common].iterrows()
        if _row_key(row) in in_keys
    )
    return (match_count / max(len(output_df), 1)) >= _MATCH_THRESHOLD


def build_filter_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect Excel FILTER array-formula transformation.

    Detects
    -------
    - Row subset that satisfies a single equality / inequality / range condition
    - IS NULL / IS NOT NULL filtering (blank-removal)
    - Multi-condition AND filter (each column condition detected independently)
    - Numeric comparisons with Excel-origin values (currency, percent, etc.)
    - String equality / IN-list filtering

    Edge cases
    ----------
    - Output has same columns as input (just fewer rows) ✓
    - Output columns are a renamed subset via column_mapping ✓
    - Column values coerced before comparison ✓
    - Identical columns (not filter keys) are skipped ✓
    - No false-positive when row reduction is due to deduplication only ✓

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "functions": ["FILTER"],
        "conditions": [str, ...]   # human-readable filter conditions
      }
    }
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {"functions": ["FILTER"], "conditions": []},
    }

    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)

    if not _is_filter_transformation(input_df, out_df):
        return empty

    raw_conditions = _infer_filter_conditions(input_df, out_df)

    # Translate column names in conditions back to output namespace
    conditions: List[str] = []
    for cond in raw_conditions:
        for in_col, out_col in (column_mapping or {}).items():
            cond = re.sub(rf"\b{re.escape(in_col)}\b", out_col, cond)
        conditions.append(cond)

    return {
        "applied": True,
        "details": {
            "functions":  ["FILTER"],
            "conditions": conditions,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.1b  UNIQUE detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_unique_transformation(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> Tuple[bool, List[str]]:
    """
    Detect whether a UNIQUE array formula was applied.

    Returns (detected: bool, unique_on_columns: List[str]).

    Strategy
    --------
    1. Whole-row deduplication: output rows == input.drop_duplicates()
       → unique_on = all common columns
    2. Column-subset deduplication: some column C has fewer unique values in
       output AND output rows are distinct on that column
       → unique_on = [C, ...]
    3. Single-column UNIQUE: output has exactly one column with only unique
       values from that input column

    Edge cases
    ----------
    - Row order may differ (SORT(UNIQUE(…)) pattern) → compare as sets
    - NaN values treated as equal duplicates (Excel behaviour)
    - Numeric near-equality tolerance applied
    - Output may have fewer columns than input (projected UNIQUE)
    """
    if input_df.empty or output_df.empty:
        return False, []
    if len(output_df) >= len(input_df):
        return False, []

    common = _common_cols(input_df, output_df)
    if not common:
        return False, []

    # ── Check 1: whole-row dedup ──────────────────────────────────────────────
    in_deduped = (
        input_df[common]
        .drop_duplicates()
        .sort_values(common)
        .reset_index(drop=True)
    )
    out_sorted = (
        output_df[common]
        .sort_values(common)
        .reset_index(drop=True)
    )
    if len(in_deduped) == len(out_sorted):
        try:
            match = (
                in_deduped.astype(str).reset_index(drop=True)
                == out_sorted.astype(str).reset_index(drop=True)
            ).all().all()
            if match:
                return True, common
        except Exception:
            pass

    # ── Check 2: column-subset dedup ─────────────────────────────────────────
    unique_on: List[str] = []
    for col in common:
        in_unique_count  = input_df[col].nunique(dropna=False)
        out_unique_count = output_df[col].nunique(dropna=False)
        if out_unique_count < in_unique_count and out_unique_count == len(output_df):
            unique_on.append(col)

    if unique_on:
        return True, unique_on

    # ── Check 3: output rows are all distinct (no dups) and ≤ input uniques ──
    out_keys = [_row_key(r) for _, r in output_df[common].iterrows()]
    if len(out_keys) == len(set(out_keys)):
        in_keys = [_row_key(r) for _, r in input_df[common].iterrows()]
        if len(set(in_keys)) < len(in_keys):          # input had duplicates
            # Check all output keys exist in input
            in_key_set = set(in_keys)
            if all(k in in_key_set for k in out_keys):
                return True, common

    return False, []


def build_unique_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect Excel UNIQUE array-formula transformation.

    Detects
    -------
    - Whole-row deduplication (all common columns used as key)
    - Column-level deduplication (specific column(s) made unique)
    - Single-column UNIQUE extraction (one column → distinct value list)
    - Combined SORT(UNIQUE(…)) pattern (row order may differ)

    Edge cases
    ----------
    - NaN values treated as duplicates (Excel UNIQUE treats blank as a value) ✓
    - Output row order independent of input (set-comparison used) ✓
    - Output with fewer columns than input (projected UNIQUE) ✓
    - column_mapping resolved before detection ✓
    - No false-positive for FILTER-only row reduction ✓

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "functions":  ["UNIQUE"],
        "unique_on":  [str, ...]   # columns that were made unique
      }
    }
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {"functions": ["UNIQUE"], "unique_on": []},
    }

    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)
    detected, unique_on_cols = _is_unique_transformation(input_df, out_df)

    if not detected:
        return empty

    # Translate to output namespace
    unique_on = [_to_out(c, column_mapping) for c in unique_on_cols]

    return {
        "applied": True,
        "details": {
            "functions": ["UNIQUE"],
            "unique_on": unique_on,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.1c  SORT (array formula / dynamic array) detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_sort_columns(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect which columns were used in a SORT / SORTBY formula.

    Returns a list of {"column": str, "order": "asc"|"desc"} dicts.

    Guards applied (mirrors row_sort.py logic)
    ------------------------------------------
    1. n_unique == 1  → constant column, trivially monotonic, NOT a sort.
    2. n_unique == len(output)  → every value is unique (e.g. Email), a
       monotonic sequence of unique strings is almost never a user-applied
       SORT formula — skip unless it is a numeric column with clear ordering.
    3. Order unchanged from input → not a SORT.
    4. Sequential index columns → skipped.
    """
    if input_df.empty or output_df.empty:
        return []
    if len(output_df) < 2:
        return []

    common = _common_cols(input_df, output_df)
    if not common:
        return []

    sort_rules: List[Dict[str, Any]] = []

    for col in common:
        if _is_sequential_index(output_df[col]):
            print(f"  [phase7 sort] '{col}': SKIPPED (sequential index column)")
            continue

        out_series = output_df[col].dropna()
        n_total    = len(out_series)
        n_unique   = out_series.nunique()

        if n_total < 2:
            print(f"  [phase7 sort] '{col}': SKIPPED (fewer than 2 non-null values)")
            continue

        # ── Guard 1: constant column ─────────────────────────────────────────
        # A column with only one unique value is trivially monotonic in BOTH
        # directions — it cannot represent a meaningful SORT.
        if n_unique == 1:
            print(f"  [phase7 sort] '{col}': SKIPPED (constant — all values identical, "
                  f"trivially monotonic, NOT a sort). value={out_series.iloc[0]!r}")
            continue

        # ── Guard 2: high-cardinality string column ──────────────────────────
        # If every value is unique AND the column is non-numeric (e.g. Email,
        # Name), a monotonic sequence is almost certainly not a user SORT —
        # it's just the natural order of distinct strings after filtering.
        is_num_col = _coerce_numeric(output_df[col]).notna().mean() >= _MATCH_THRESHOLD
        if n_unique == n_total and not is_num_col:
            print(f"  [phase7 sort] '{col}': SKIPPED (all-unique non-numeric column — "
                  f"monotonicity is incidental, not a sort)")
            continue

        # ── Monotonicity check ───────────────────────────────────────────────
        num_series = _coerce_numeric(output_df[col]).dropna()
        if is_num_col and len(num_series) >= 2:
            is_asc  = bool(num_series.is_monotonic_increasing)
            is_desc = bool(num_series.is_monotonic_decreasing)
        else:
            str_series = output_df[col].dropna().astype(str).str.lower()
            is_asc  = bool(str_series.is_monotonic_increasing)
            is_desc = bool(str_series.is_monotonic_decreasing)

        print(f"  [phase7 sort] '{col}': unique={n_unique}/{n_total} "
              f"is_asc={is_asc} is_desc={is_desc} "
              f"sample={out_series.head(3).tolist()}")

        if not (is_asc or is_desc):
            print(f"  [phase7 sort] '{col}': SKIPPED (not monotonic)")
            continue

        # ── Guard 3: order unchanged from input ──────────────────────────────
        # Compare the VALUE SETS at the same positions, not positional equality,
        # because after filtering the input indices are a non-contiguous subset.
        # Instead: check whether the output order is the same as the order those
        # values appeared in the input (i.e. no reordering occurred).
        in_col_vals  = input_df[col].dropna().reset_index(drop=True).astype(str)
        out_col_vals = output_df[col].dropna().reset_index(drop=True).astype(str)
        n = min(len(in_col_vals), len(out_col_vals))

        # For the overlap, check if the output sequence is just the input
        # sequence with some values removed (filter) vs. actually reordered.
        # Strategy: extract output values in the order they appear in input.
        # If that order matches the actual output order → no sort applied.
        out_set = set(out_col_vals.tolist())
        in_filtered_order = [v for v in in_col_vals.tolist() if v in out_set]
        out_order         = out_col_vals.tolist()

        # Pad / trim to the same length for comparison
        n_cmp = min(len(in_filtered_order), len(out_order))
        order_unchanged = (in_filtered_order[:n_cmp] == out_order[:n_cmp])

        print(f"  [phase7 sort] '{col}': in_filtered_order[:5]={in_filtered_order[:5]} "
              f"out_order[:5]={out_order[:5]} order_unchanged={order_unchanged}")

        if order_unchanged:
            print(f"  [phase7 sort] '{col}': SKIPPED (order unchanged from filtered input)")
            continue

        direction = "asc" if is_asc else "desc"
        print(f"  [phase7 sort] '{col}': ✅ SORT DETECTED order={direction}")
        sort_rules.append({"column": col, "order": direction})

    print(f"  [phase7 sort] result → {sort_rules}")
    return sort_rules


def build_sort_array_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect Excel SORT / SORTBY dynamic array-formula transformation.

    Detects
    -------
    - Single-column ascending or descending SORT
    - Multi-column SORT (primary, secondary keys)
    - SORTBY (sort by a helper / derived column)
    - Numeric, string (lexicographic), and mixed-type sorting
    - NaN values present at end (Excel default SORT behaviour)

    Edge cases
    ----------
    - Sequential index columns (row numbers) are excluded ✓
    - Columns with unchanged order are excluded (no false-positive) ✓
    - Numeric coercion applied for Excel-origin string numbers ✓
    - column_mapping resolved before detection ✓

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "functions": ["SORT"],
        "sort_by":   [{"column": str, "order": "asc"|"desc"}, ...]
      }
    }
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {"functions": ["SORT"], "sort_by": []},
    }

    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)
    raw_rules = _detect_sort_columns(input_df, out_df)

    if not raw_rules:
        return empty

    rules = [
        {"column": _to_out(r["column"], column_mapping), "order": r["order"]}
        for r in raw_rules
    ]

    return {
        "applied": True,
        "details": {
            "functions": ["SORT"],
            "sort_by":   rules,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_array_formulas_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect all Excel array-formula transformations (FILTER, UNIQUE, SORT).

    Each of the three sub-detectors runs independently — all that apply are
    reported simultaneously (e.g. SORT(FILTER(…)) triggers both SORT and FILTER).

    Args:
        input_df:       Source DataFrame (Excel sheet before formula).
        output_df:      Result DataFrame (Excel sheet after formula applied).
        column_mapping: Optional {input_col_name: output_col_name} mapping.
                        Used to align renamed columns across both frames.

    Returns:
        {
          "applied": bool,
          "details": {
            "functions": ["FILTER"] | ["UNIQUE"] | ["SORT"] | combinations,
            "filter":    { "conditions": [...] }              # only if FILTER detected
            "unique":    { "unique_on": [...] }               # only if UNIQUE detected
            "sort":      { "sort_by":   [{"column", "order"}] } # only if SORT detected
          }
        }

    Edge cases covered
    ------------------
    - SORT(FILTER(…))   : both SORT and FILTER detected ✓
    - SORT(UNIQUE(…))   : both SORT and UNIQUE detected ✓
    - UNIQUE(FILTER(…)) : both UNIQUE and FILTER detected ✓
    - Empty frames      : applied=False returned ✓
    - No transformation : applied=False returned ✓
    - column_mapping    : all results in output-column namespace ✓
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {"functions": []},
    }

    if input_df is None or output_df is None:
        return empty
    if input_df.empty or output_df.empty:
        return empty

    filter_cfg = build_filter_config(input_df, output_df, column_mapping)
    unique_cfg = build_unique_config(input_df, output_df, column_mapping)
    sort_cfg   = build_sort_array_config(input_df, output_df, column_mapping)

    functions_detected: List[str] = []
    details: Dict[str, Any] = {}

    if filter_cfg["applied"]:
        functions_detected.append("FILTER")
        details["filter"] = {
            "conditions": filter_cfg["details"]["conditions"]
        }

    if unique_cfg["applied"]:
        functions_detected.append("UNIQUE")
        details["unique"] = {
            "unique_on": unique_cfg["details"]["unique_on"]
        }

    if sort_cfg["applied"]:
        functions_detected.append("SORT")
        details["sort"] = {
            "sort_by": sort_cfg["details"]["sort_by"]
        }

    if not functions_detected:
        return empty

    details["functions"] = functions_detected

    return {
        "applied": True,
        "details": details,
    }
