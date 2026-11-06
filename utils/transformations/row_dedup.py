# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs;
# strip any pandas data-mutation helpers.
# ============================================================================
"""
Row Deduplication Utility
==========================
Detects REMOVE DUPLICATES transformations applied between input_df and output_df.

Detection logic:
  1. If output has fewer rows than input, check whether the output rows are
     exactly the deduplicated version of the input.
  2. Try all common-column subsets (full set first, then individual columns)
     to find the minimal key set that explains the row reduction.
  3. Check for "keep best" patterns - keep row with max/min value in another column.
  4. Confirm only when deduplication perfectly reproduces the output row count.

Supported keep strategies:
  - first: Keep first occurrence
  - last: Keep last occurrence
  - max:<column>: Keep the row with maximum value in specified column
  - min:<column>: Keep the row with minimum value in specified column
"""

import pandas as pd
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_sequential_index(series: pd.Series) -> bool:
    """
    Return True if a numeric series looks like a sequential row index
    (e.g. SaleID 101-105, RowNo 1-N).  Such columns must NOT be used as
    the keep-strategy column because they carry no business meaning.
    """
    try:
        if not pd.api.types.is_numeric_dtype(series):
            return False
        vals = series.dropna().astype(int).tolist()
        if len(vals) < 2:
            return False
        sorted_vals = sorted(vals)
        diffs = [sorted_vals[i + 1] - sorted_vals[i] for i in range(len(sorted_vals) - 1)]
        return diffs.count(1) / len(diffs) >= 0.7
    except Exception:
        return False


def _is_numeric(series: pd.Series) -> bool:
    """Return True if the series holds numeric (non-bool) values."""
    return (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


def _rows_after_dedup(df: pd.DataFrame, subset: List[str]) -> int:
    """Return the number of rows after dropping duplicates on *subset*."""
    return len(df.drop_duplicates(subset=subset))


def _dedup_keep_max(df: pd.DataFrame, subset: List[str], max_col: str) -> pd.DataFrame:
    """
    Deduplicate by keeping the row with maximum value in max_col for each group.
    Preserves original row order (sort_index ensures groupby alphabetical ordering
    doesn't reorder rows).
    """
    idx = df.groupby(subset, sort=False)[max_col].idxmax()
    return df.loc[sorted(idx)].reset_index(drop=True)


def _dedup_keep_min(df: pd.DataFrame, subset: List[str], min_col: str) -> pd.DataFrame:
    """
    Deduplicate by keeping the row with minimum value in min_col for each group.
    Preserves original row order.
    """
    idx = df.groupby(subset, sort=False)[min_col].idxmin()
    return df.loc[sorted(idx)].reset_index(drop=True)


def _output_matches_dedup(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    subset: List[str],
    keep: str = "first",
) -> bool:
    """
    Return True when deduplicating *input_df* on *subset* yields the same
    row count as *output_df* and content matches.
    
    keep: "first", "last", "max:<col>", or "min:<col>"
    """
    try:
        if keep == "first" or keep == "last":
            deduped = input_df.drop_duplicates(subset=subset, keep=keep)
        elif keep.startswith("max:"):
            max_col = keep.split(":", 1)[1]
            if max_col not in input_df.columns:
                return False
            deduped = _dedup_keep_max(input_df, subset, max_col)
        elif keep.startswith("min:"):
            min_col = keep.split(":", 1)[1]
            if min_col not in input_df.columns:
                return False
            deduped = _dedup_keep_min(input_df, subset, min_col)
        else:
            deduped = input_df.drop_duplicates(subset=subset, keep="first")
    except Exception:
        return False
    
    if len(deduped) != len(output_df):
        return False

    # Content check using ALL common columns (not just subset columns).
    # This ensures "keep=first" is NOT accepted when the kept rows differ in
    # non-subset columns (e.g. UnitPrice differs → max/min strategy needed).
    all_common_cols = [c for c in deduped.columns if c in output_df.columns]
    if not all_common_cols:
        return True

    deduped_keys = set(
        tuple(str(v) for v in row)
        for row in deduped[all_common_cols].values
    )
    output_keys = set(
        tuple(str(v) for v in row)
        for row in output_df[all_common_cols].values
    )
    return output_keys.issubset(deduped_keys)


def _col_discriminates_duplicates(
    input_df: pd.DataFrame,
    subset: List[str],
    col: str,
) -> bool:
    """
    Return True only if *col* has DIFFERENT values within at least one
    duplicate group defined by *subset*.

    If every duplicate group has a single unique value for *col* (e.g. both
    Laptop rows have Quantity=1), then max/min on *col* is meaningless — any
    row would be equally "max".  We must reject such columns as keep targets.
    """
    try:
        for _, grp in input_df.groupby(subset, sort=False):
            if len(grp) > 1 and grp[col].nunique() > 1:
                return True
        return False
    except Exception:
        return False


def _detect_keep_strategy(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    subset: List[str],
) -> Tuple[bool, str]:
    """
    Detect which keep strategy explains the deduplication.

    Candidate numeric columns for max/min are filtered to:
      - be numeric
      - NOT be a dedup subset column
      - NOT look like a sequential index (SaleID, RowNo, etc.)
      - MUST actually discriminate within duplicate groups (different values
        across duplicate rows) — columns where all duplicates share the same
        value (e.g. Quantity=1 for both Laptop rows) are rejected as keep
        targets because max/min on them is arbitrary and meaningless.

    Returns (matched: bool, keep_strategy: str)
    """
    numeric_cols = [
        c for c in input_df.columns
        if _is_numeric(input_df[c])
        and c not in subset
        and not _is_sequential_index(input_df[c])
        and _col_discriminates_duplicates(input_df, subset, c)   # ← key guard
    ]

    # Try "keep max" and "keep min" on meaningful numeric columns FIRST.
    for num_col in numeric_cols:
        if _output_matches_dedup(input_df, output_df, subset, f"max:{num_col}"):
            return True, f"max:{num_col}"
        if _output_matches_dedup(input_df, output_df, subset, f"min:{num_col}"):
            return True, f"min:{num_col}"

    # Fall back to simple keep="first" / "last"
    for keep in ["first", "last"]:
        if _output_matches_dedup(input_df, output_df, subset, keep):
            return True, keep

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Core detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_dedup_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Identify the MINIMAL column subset used for duplicate removal and how.

    Strategy — smallest key wins:
      1. Try each individual column that has duplicates in input.
      2. Try every 2-column combination.
      3. Fall back to the full common-column set.

    This ensures we never return [Product, Quantity] when [Product] alone
    fully explains the deduplication.
    """
    if len(output_df) >= len(input_df):
        return []   # No rows removed — dedup not applicable

    common_cols = [c for c in input_df.columns if c in output_df.columns]
    if not common_cols:
        return []

    # ── 1. Single-column keys (minimal) ──────────────────────────────────────
    for col in common_cols:
        if input_df[col].duplicated().any():
            matched, keep = _detect_keep_strategy(input_df, output_df, [col])
            if matched:
                return [{"columns": [col], "keep": keep}]

    # ── 2. Two-column combinations ────────────────────────────────────────────
    for col1, col2 in combinations(common_cols, 2):
        matched, keep = _detect_keep_strategy(input_df, output_df, [col1, col2])
        if matched:
            return [{"columns": [col1, col2], "keep": keep}]

    # ── 3. Full common-column set (last resort) ───────────────────────────────
    matched, keep = _detect_keep_strategy(input_df, output_df, common_cols)
    if matched:
        return [{"columns": common_cols, "keep": keep}]

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_dedup_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect remove-duplicates transformations and return the phase_2_rows
    'remove_duplicates' config block.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (after deduplication applied).
        column_mapping: Optional {output_col: input_col} name mapping.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {
                        "columns": [str, ...],
                        "keep": str  # "first", "last", "max:<col>", "min:<col>"
                    }
                ]
            }
        }
    """
    # Sentinel values for derived columns
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}

    # Aggregation guard: dedup never creates new columns.
    # A GroupBy aggregation reduces rows AND retains only categorical/key columns —
    # all numeric measurements become derived (sum, avg, count, etc.).
    # A dedup+enrichment also adds derived columns but PRESERVES at least one
    # numeric input column in the kept row (e.g. Quantity, UnitPrice).
    # Guard fires only when derived cols exist AND no numeric input col survives.
    if column_mapping:
        has_sentinel = any(v in _DERIVED_SENTINELS for v in column_mapping.values())
        surviving_input_cols = {
            in_col for out_col, in_col in column_mapping.items()
            if in_col not in _DERIVED_SENTINELS and in_col in input_df.columns
        }
        has_surviving_numeric = any(
            pd.api.types.is_numeric_dtype(input_df[c]) for c in surviving_input_cols
        )
        has_aggregated = has_sentinel and not has_surviving_numeric
    else:
        new_out_cols = [c for c in output_df.columns if c not in input_df.columns]
        has_surviving_numeric = any(
            pd.api.types.is_numeric_dtype(output_df[c])
            for c in output_df.columns if c in input_df.columns
        )
        has_aggregated = len(new_out_cols) > 0 and not has_surviving_numeric

    if has_aggregated:
        return {"applied": False, "details": {"rules": []}}

    # column_mapping is {output_col: input_col} - use directly to rename output→input
    if column_mapping:
        rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
        out_df = output_df.rename(columns=rename_map)
        # Drop duplicate column names that arise when multiple output cols map to
        # the same input col (e.g. Full_Name + Reconstructed_Full_Name → Full_Name).
        out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    else:
        out_df = output_df

    raw_rules = _detect_dedup_rules(input_df, out_df)

    # Build inverse mapping {input_col: output_col} to translate detected names to output namespace
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}

    # Remap column names back to output namespace
    rules: List[Dict[str, Any]] = []
    for r in raw_rules:
        cols = [inv_map.get(c, c) for c in r["columns"]]
        keep = r.get("keep", "first")
        
        # Also remap the column name in "max:<col>" or "min:<col>"
        if keep.startswith("max:") or keep.startswith("min:"):
            prefix, keep_col = keep.split(":", 1)
            keep_col = inv_map.get(keep_col, keep_col)
            keep = f"{prefix}:{keep_col}"
        
        rules.append({"columns": cols, "keep": keep})

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
