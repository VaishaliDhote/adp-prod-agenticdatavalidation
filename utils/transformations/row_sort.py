# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Row Sort Utility
=================
Detects SORT transformations applied between input_df and output_df.

Detection logic:
  1. Identify common columns between input and output.
  2. Find a MINIMAL sort key by verifying that sorting by the chosen columns
     reproduces the exact output row order (tuple comparison).
  3. Only add secondary/tertiary columns when the primary key has ties that
     need tie-breaking — avoids false positives from coincidentally monotonic
     columns (e.g. Quantity DESC that happens to be monotonic alongside a
     Sale_Date ASC sort).
  4. A sort is considered "real" only when the output row order differs from
     the input row order after accounting for any row count change.
"""

import pandas as pd
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_sequential_index_col(series: pd.Series) -> bool:
    """Return True if the column looks like a re-numbered sequential index."""
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


def _get_serial_index_cols(df: pd.DataFrame) -> set:
    """
    Return the set of column names that look like serial/sequential index columns.
    These are excluded from row tuple comparisons so that auto-renumbered index
    columns don't cause false mismatches.
    """
    return {col for col in df.columns if _is_sequential_index_col(df[col])}


def _row_tuples(df: pd.DataFrame, exclude_cols: set = None) -> List[Tuple]:
    """
    Return a list of row tuples (stringified) for stable comparison.
    Columns in exclude_cols (e.g. serial index columns) are omitted.
    """
    if exclude_cols is None:
        exclude_cols = set()
    cols = [c for c in df.columns if c not in exclude_cols]
    if not cols:
        return [tuple(str(v) for v in row) for row in df.values]
    return [tuple(str(v) for v in row) for row in df[cols].values]


def _output_matches_sort(
    output_df: pd.DataFrame,
    sort_cols: List[str],
    ascending: List[bool],
) -> bool:
    """
    Return True when applying sort_values(sort_cols, ascending) to output_df
    preserves the original row order exactly (row-by-row tuple comparison).

    Serial/sequential index columns are excluded from the comparison so that
    auto-renumbered index columns (e.g. SID 1..N) don't cause false failures.
    """
    try:
        # Identify and exclude serial/index columns from the tuple comparison
        exclude_cols = _get_serial_index_cols(output_df)
        if exclude_cols:
            print(f"    [_output_matches_sort] excluding serial/index cols from tuple comparison: {exclude_cols}")

        sorted_df = (
            output_df
            .sort_values(by=sort_cols, ascending=ascending)
            .reset_index(drop=True)
        )
        return (
            _row_tuples(output_df.reset_index(drop=True), exclude_cols)
            == _row_tuples(sorted_df, exclude_cols)
        )
    except Exception:
        return False


def _normalize_to_comparable(series: pd.Series) -> pd.Series:
    """
    Normalize a series to a sortable numeric form for rank-based comparison.
    Tries datetime → numeric → string fallback so that format differences
    (e.g. "3/5/2026" vs "2026-03-05 00:00:00") don't falsely signal change.
    """
    try:
        dt = pd.to_datetime(series, errors='raise', infer_datetime_format=True)
        return dt.astype('int64')
    except Exception:
        pass
    num = pd.to_numeric(series, errors='coerce')
    if num.notna().sum() >= len(series) * 0.8:
        return num
    return series.astype(str)


def _col_order_changed(input_df: pd.DataFrame, output_df: pd.DataFrame, col: str) -> bool:
    """
    Return True if the RELATIVE RANK ORDER of col's values differs between
    input and output.

    Uses rank comparison instead of raw value comparison, so it is robust
    against format differences (e.g. date format changes) that preserve the
    sort order but change the string representation.
    """
    in_vals  = input_df[col].dropna().reset_index(drop=True)
    out_vals = output_df[col].dropna().reset_index(drop=True)
    min_len  = min(len(in_vals), len(out_vals))
    if min_len == 0:
        return False

    in_slice  = in_vals[:min_len]
    out_slice = out_vals[:min_len]

    changed: bool
    try:
        in_norm   = _normalize_to_comparable(in_slice)
        out_norm  = _normalize_to_comparable(out_slice)
        in_ranks  = in_norm.rank(method='first').tolist()
        out_ranks = out_norm.rank(method='first').tolist()
        changed   = in_ranks != out_ranks
        print(f"    [_col_order_changed] '{col}': "
              f"in_ranks={in_ranks[:5]} | out_ranks={out_ranks[:5]} | changed={changed}")
    except Exception:
        # Fall back to string comparison if normalization fails
        changed = not (in_slice.astype(str) == out_slice.astype(str)).all()
        print(f"    [_col_order_changed] '{col}': string fallback | changed={changed}")

    return changed


def _has_ties(series: pd.Series) -> bool:
    """Return True if the series contains any repeated values (ties at sort level)."""
    return series.duplicated().any()


# ─────────────────────────────────────────────────────────────────────────────
# Core detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_sort_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Detect the MINIMAL sort key that fully explains the output row order.

    Algorithm:
      1. Collect candidate columns: common, non-index, with ≥2 unique values,
         and whose order actually changed from the input.
      2. For each candidate (asc and desc), verify by sorting the output and
         comparing row tuples — not just monotonicity.
      3. Build the minimal key:
         a. Find the first column (any direction) that alone explains the order.
         b. If the primary column has ties, search for a secondary column that
            explains the tie-breaking, and so on.
      4. Return the minimal confirmed sort spec.
    """
    common_cols = [c for c in output_df.columns if c in input_df.columns]

    print(f"\n[_detect_sort_rules] input rows={len(input_df)}, output rows={len(output_df)}")
    print(f"[_detect_sort_rules] common_cols={common_cols}\n")

    # ── Step 1: gather candidates ──────────────────────────────────────────
    candidates: List[Tuple[str, str]] = []   # (col_name, "asc"|"desc")
    for col in common_cols:
        if _is_sequential_index_col(output_df[col]):
            print(f"  '{col}' → SKIPPED (sequential index)")
            continue

        col_series = output_df[col].dropna()
        if len(col_series) < 2 or col_series.nunique() < 2:
            print(f"  '{col}' → SKIPPED (< 2 unique values)")
            continue

        if not _col_order_changed(input_df, output_df, col):
            print(f"  '{col}' → SKIPPED (order unchanged from input)")
            continue

        # Add both directions; we'll verify later
        candidates.append((col, "asc"))
        candidates.append((col, "desc"))

    print(f"[_detect_sort_rules] candidates after filtering: {candidates}\n")

    if not candidates:
        return []

    # ── Step 2: find minimal sort key via tuple-row verification ─────────
    # Try every candidate as the primary sort column
    for primary_col, primary_dir in candidates:
        ascending = [primary_dir == "asc"]
        sort_cols = [primary_col]

        if not _output_matches_sort(output_df, sort_cols, ascending):
            print(f"  '{primary_col}' {primary_dir} → tuple check FAILED")
            continue

        print(f"  '{primary_col}' {primary_dir} → ✅ PRIMARY sort confirmed by tuple comparison")

        # Primary column alone explains the order.
        # Only look for secondary columns if primary has ties (duplicate values).
        result = [{"column": primary_col, "order": primary_dir}]

        if _has_ties(output_df[primary_col]):
            print(f"  '{primary_col}' has ties — searching for secondary sort column...")
            for sec_col, sec_dir in candidates:
                if sec_col == primary_col:
                    continue
                sec_ascending = ascending + [sec_dir == "asc"]
                sec_cols = sort_cols + [sec_col]
                if _output_matches_sort(output_df, sec_cols, sec_ascending):
                    print(f"    '{sec_col}' {sec_dir} → ✅ SECONDARY sort confirmed")
                    result.append({"column": sec_col, "order": sec_dir})

                    # Look for tertiary if secondary also has ties
                    if _has_ties(output_df[sec_col]):
                        for ter_col, ter_dir in candidates:
                            if ter_col in (primary_col, sec_col):
                                continue
                            ter_ascending = sec_ascending + [ter_dir == "asc"]
                            ter_cols = sec_cols + [ter_col]
                            if _output_matches_sort(output_df, ter_cols, ter_ascending):
                                print(f"      '{ter_col}' {ter_dir} → ✅ TERTIARY sort confirmed")
                                result.append({"column": ter_col, "order": ter_dir})
                                break
                    break  # Only one secondary needed

        print(f"\n[_detect_sort_rules] result: {result}\n")
        return result

    print(f"\n[_detect_sort_rules] result: []\n")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_sort_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect row sort transformations and return the phase_2_rows 'sort' config block.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (after sort applied).
        column_mapping: Optional {output_col: input_col} name mapping.
                        When provided the output is remapped before comparison.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {"column": str, "order": "asc" | "desc"}
                ]
            }
        }
    """
    # Sentinel values for derived columns
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}

    # Aggregation guard: a sort applied in phase_2 operates on the raw input rows.
    # A GroupBy aggregation retains only categorical/key columns — all numeric
    # measurements become derived. A sort+enrichment preserves at least one numeric
    # input column. Guard fires only when derived cols exist AND no numeric input
    # col survives, which is the signature of a pure GroupBy aggregation.
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
            pd.api.types.is_numeric_dtype(out_df[c])
            for c in out_df.columns if c in input_df.columns
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

    raw_rules = _detect_sort_rules(input_df, out_df)

    # Build inverse mapping {input_col: output_col} to translate detected names to output namespace
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}

    # Remap column names back to output namespace
    rules: List[Dict[str, Any]] = []
    for r in raw_rules:
        col = r["column"]
        col = inv_map.get(col, col)
        rules.append({"column": col, "order": r["order"]})

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
