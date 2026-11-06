# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs;
# strip any pandas data-mutation helpers.
# ============================================================================
"""
Row Value Filter Utility
=========================
Detects numeric and string equality/comparison filters applied to rows
between input_df and output_df.

Detection logic is ported directly from the value_filter.ipynb notebook.

Supported operators:
  Numeric : ==, !=, >, <, >=, <=
  String  : ==, !=

The detector works sequentially: once a filter is found and applied, the
reduced DataFrame is used to search for the next filter.
"""

from numpy import result_type
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Column-type helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_numeric(series: pd.Series) -> bool:
    """Return True if the series holds numeric (non-bool) values."""
    return (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


def _is_comparable_string(series: pd.Series) -> bool:
    """Return True if the series holds sortable string values."""
    if not (
        pd.api.types.is_string_dtype(series)
        or pd.api.types.is_object_dtype(series)
    ):
        return False
    try:
        vals = series.dropna().astype(str).unique()[:10]
        if len(vals) > 0:
            sorted(vals)
            return True
    except Exception:
        pass
    return False


def _is_serial_index_column(
    input_col: pd.Series,
    output_col: pd.Series,
    col_name: str,
) -> bool:
    """
    Detect if a column is a serial/index column that was re-numbered.
    
    Heuristics:
      1. Column name looks like an index (SL No, ID, Index, Seq, Employee Count, etc.)
      2. Both input and output are numeric with mostly sequential values
      3. Value ranges differ significantly (re-numbering pattern)
      4. Output is a clean sequential 1..N or 0..N-1 sequence
    
    IMPORTANT: If output values are a SUBSET of input values, this indicates
    FILTERING not re-numbering, so we return False to allow filter detection.
    
    Returns True if the column appears to be a re-numbered index.
    """
    # Check column name patterns (MORE AGGRESSIVE)
    index_patterns = {
        "sl no", "sl.", "index", "seq", "sequence", "row", "sno",
        "#", "number", "count", "serial", "employee count", "ref #",
        "serial no", "serial number", "record number", "row number",
        "entry number", "item number", "line number", "no.", "num",
    }
    col_lower = col_name.lower().strip()
    
    # Direct match or starts with pattern
    if col_lower in index_patterns or col_lower.startswith("no.") or "count" in col_lower and "employee" in col_lower:
        return True
    
    # Check if both are numeric
    if not (_is_numeric(input_col) and _is_numeric(output_col)):
        return False
    
    input_vals = input_col.dropna().astype(int).values
    output_vals = output_col.dropna().astype(int).values
    
    if len(input_vals) == 0 or len(output_vals) == 0:
        return False
    
    input_set = set(input_vals)
    output_set = set(output_vals)
    
    # ✅ CRITICAL CHECK: If output values are a SUBSET of input values,
    # this is a FILTER operation, not a re-numbering. Allow filter detection.
    if output_set.issubset(input_set) and output_set != input_set:
        # Output is a proper subset - rows were filtered, not re-numbered
        return False
    
    # ✅ Key indicator: output is EXACTLY 1..N sequential
    output_sorted = sorted(output_set)
    is_clean_sequence = (
        output_sorted == list(range(1, len(output_sorted) + 1)) or 
        output_sorted == list(range(len(output_sorted)))
    )
    
    # If output is clean 1..N, this is almost certainly an index
    if is_clean_sequence:
        return True
    
    # Check if they're mostly sequential (sign of index columns)
    def is_mostly_sequential(vals):
        if len(vals) < 2:
            return False
        sorted_vals = sorted(set(vals))
        diffs = [sorted_vals[i+1] - sorted_vals[i] for i in range(len(sorted_vals)-1)]
        if len(diffs) == 0:
            return False
        seq_count = sum(1 for d in diffs if d == 1)
        return seq_count / len(diffs) >= 0.6
    
    input_seq = is_mostly_sequential(input_vals)
    output_seq = is_mostly_sequential(output_vals)
    
    # Both sequential AND output is NOT a subset = likely index columns being renumbered
    if input_seq and output_seq:
        return True
    
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Row-tuple validation
# ─────────────────────────────────────────────────────────────────────────────

def _get_row_tuples(df: pd.DataFrame, exclude_cols: set = None) -> set:
    """
    Get a set of row tuples (excluding serial/index columns).
    Used to validate that filters actually match complete rows.
    """
    if exclude_cols is None:
        exclude_cols = set()
    
    cols = [c for c in df.columns if c not in exclude_cols]
    if not cols:
        return set()
    
    return {
        tuple(str(v) for v in row)
        for row in df[cols].values
    }


def _validate_filter_on_rows(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    operator: str,
    column: str,
    value: Any,
    exclude_cols: set = None,
) -> bool:
    """
    Validate that applying the filter to input_df actually produces rows
    that match output_df (excluding serial/index columns).
    
    Returns True only if the row content matches, not just the count.
    """
    if exclude_cols is None:
        exclude_cols = set()
    
    # Apply the filter to input
    op_fn = {
        "==":      lambda df, c, v: df[df[c] == v],
        "!=":      lambda df, c, v: df[df[c] != v],
        ">":       lambda df, c, v: df[df[c] >  v],
        "<":       lambda df, c, v: df[df[c] <  v],
        ">=":      lambda df, c, v: df[df[c] >= v],
        "<=":      lambda df, c, v: df[df[c] <= v],
        "not_null": lambda df, c, v: df[df[c].notna()],
        "is_null":  lambda df, c, v: df[df[c].isna()],
    }
    
    if operator not in op_fn:
        return False
    
    filtered = op_fn[operator](input_df, column, value).reset_index(drop=True)

    # Only compare columns common to both dataframes (output may have extra derived cols)
    common_cols = set(filtered.columns) & set(output_df.columns) - exclude_cols
    if not common_cols:
        return len(filtered) == len(output_df)

    filtered_tuples = _get_row_tuples(filtered[list(common_cols)], exclude_cols)
    output_tuples = _get_row_tuples(output_df[list(common_cols)], exclude_cols)

    return filtered_tuples == output_tuples


# ─────────────────────────────────────────────────────────────────────────────
# Row-level output mask
# ─────────────────────────────────────────────────────────────────────────────

def _output_mask(input_col: pd.Series, output_col: pd.Series) -> pd.Series:
    """Boolean mask: which input rows have values that appear in output."""
    output_vals = set(output_col.dropna().astype(str).unique())
    return input_col.astype(str).isin(output_vals)


# ─────────────────────────────────────────────────────────────────────────────
# Single-operator test
# ─────────────────────────────────────────────────────────────────────────────

def _test_operator(
    input_col: pd.Series,
    output_col: pd.Series,
    operator: str,
    threshold: Any,
) -> Tuple[bool, int]:
    """
    Test whether *operator threshold* reproduces exactly the output rows.

    Returns (is_valid, match_count).

    Note: comparisons are done on .values (numpy arrays) so that a
    non-contiguous index on input_col (produced by row-filtering) never
    raises "Can only compare identically-labeled Series objects".
    """
    expected_mask = _output_mask(input_col, output_col)

    op_map = {
        "==": input_col == threshold,
        "!=": input_col != threshold,
        ">":  input_col >  threshold,
        "<":  input_col <  threshold,
        ">=": input_col >= threshold,
        "<=": input_col <= threshold,
    }
    if operator not in op_map:
        return False, 0

    test_mask = op_map[operator]
    # Use .values so mismatched index labels never cause a ValueError
    is_valid = (test_mask.values == expected_mask.values).all()
    return is_valid, int(test_mask.sum())


# ─────────────────────────────────────────────────────────────────────────────
# Per-column detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_numeric_filters(
    input_col: pd.Series, output_col: pd.Series
) -> List[Dict[str, Any]]:
    """Detect numeric value filters (==, !=, >, <, >=, <=)."""
    if not _is_numeric(input_col) or not _is_numeric(output_col):
        return []

    input_vals = input_col.dropna()
    output_vals = output_col.dropna()

    if len(input_vals) == 0 or len(output_vals) == 0:
        return []

    in_unique  = set(input_vals)
    out_unique = set(output_vals)

    if in_unique == out_unique:
        return []

    kept     = sorted(out_unique)
    excluded = sorted(in_unique - out_unique)

    if not kept:
        return []

    min_kept     = min(kept)
    max_kept     = max(kept)

    # --- EQUALS: single value kept -------------------------------------------
    if len(kept) == 1:
        ok, cnt = _test_operator(input_col, output_col, "==", kept[0])
        if ok and cnt == len(output_vals):
            return [{"operator": "==", "value": kept[0]}]

    # --- NOT_EQUALS: single value excluded ------------------------------------
    if len(excluded) == 1:
        ok, cnt = _test_operator(input_col, output_col, "!=", excluded[0])
        if ok and cnt == len(output_vals):
            return [{"operator": "!=", "value": excluded[0]}]

    # --- Range filters: all excluded are BELOW kept (>= / > filter) ----------
    if excluded:
        max_excl = max(excluded)

        ok, cnt = _test_operator(input_col, output_col, ">=", min_kept)
        if ok and cnt == len(output_vals):
            return [{"operator": ">=", "value": min_kept}]

        ok, cnt = _test_operator(input_col, output_col, ">", max_excl)
        if ok and cnt == len(output_vals):
            return [{"operator": ">", "value": max_excl}]

    # --- Range filters: all excluded are ABOVE kept (<= / < filter) ----------
    if excluded:
        min_excl = min(excluded)

        ok, cnt = _test_operator(input_col, output_col, "<=", max_kept)
        if ok and cnt == len(output_vals):
            return [{"operator": "<=", "value": max_kept}]

        ok, cnt = _test_operator(input_col, output_col, "<", min_excl)
        if ok and cnt == len(output_vals):
            return [{"operator": "<", "value": min_excl}]

    return []


def _detect_null_filters(
    input_col: pd.Series, output_col: pd.Series
) -> List[Dict[str, Any]]:
    """
    Detect null-based filters (is_null / not_null).
    
    Rules:
      • not_null: input has nulls but output has no nulls → rows with nulls were removed
      • is_null: output contains only null rows → rows with non-nulls were removed
      • mixed: output has both nulls and non-nulls → no pure null filter applies
    """
    try:
        input_null_count = input_col.isna().sum()
        output_null_count = output_col.isna().sum()
        col_name = getattr(input_col, 'name', 'unknown')
        
        # Ensure we're comparing the same length (accounting for row filtering)
        # If output is a strict subset by nullness pattern, we can detect
        
        # Case 1: input has nulls, output has NONE → not_null filter applied
        if input_null_count > 0 and output_null_count == 0:
            return [{"operator": "not_null", "value": None}]
        
        # Case 2: input has non-nulls, output has ONLY nulls → is_null filter applied
        if input_null_count < len(input_col) and output_null_count == len(output_col):
            return [{"operator": "is_null", "value": None}]
        
        return []
    except Exception as e:
        # Silently skip if there's any error in null detection
        return []


def _detect_string_equality_filters(
    input_col: pd.Series, output_col: pd.Series
) -> List[Dict[str, Any]]:
    """Detect string equality filters (== and !=)."""
    if not _is_comparable_string(input_col):
        return []

    in_str  = input_col.dropna().astype(str)
    out_str = output_col.dropna().astype(str)

    if len(in_str) == 0 or len(out_str) == 0:
        return []

    in_unique  = set(in_str)
    out_unique = set(out_str)

    if in_unique == out_unique:
        return []

    kept     = list(out_unique)
    excluded = list(in_unique - out_unique)

    if len(kept) == 1:
        ok, cnt = _test_operator(input_col, output_col, "==", kept[0])
        if ok and cnt == len(out_str):
            return [{"operator": "==", "value": kept[0]}]

    if len(excluded) == 1:
        ok, cnt = _test_operator(input_col, output_col, "!=", excluded[0])
        if ok and cnt == len(out_str):
            return [{"operator": "!=", "value": excluded[0]}]

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Sequential detection across all columns
# ─────────────────────────────────────────────────────────────────────────────

def _detect_value_filters_sequential(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """
    Detect value-based row filters sequentially across all columns.

    Each confirmed filter is applied to `current_df` before the next column
    is evaluated, matching how stacked Excel AutoFilters behave.

    Returns a list of rule dicts:
        {"column": str, "operator": str, "value": Any, "type": "Numeric"|"String"}
    """
    value_filters: List[Dict[str, Any]] = []
    current_df = input_df.copy().reset_index(drop=True)
    # Reset output index once so it always starts at 0 and stays comparable
    output_df  = output_df.reset_index(drop=True)
    filtered_cols: set = set()
    max_iter = len(input_df.columns) * 2

    for _ in range(max_iter):
        if len(current_df) == len(output_df):
            break
        if len(current_df) < len(output_df):
            break

        found = False

        for col in input_df.columns:
            if col in filtered_cols or col not in output_df.columns:
                continue

            is_num = _is_numeric(current_df[col])
            is_str = _is_comparable_string(current_df[col])

            # Null filter check runs on ALL column types first
            try:
                null_filters = _detect_null_filters(current_df[col], output_df[col])
                if null_filters:
                    op = null_filters[0]["operator"]
                    
                    # Apply null filter and check if row count matches
                    null_op_fn = {
                        "not_null": lambda df, c: df[df[c].notna()],
                        "is_null":  lambda df, c: df[df[c].isna()],
                    }
                    
                    if op in null_op_fn:
                        projected = null_op_fn[op](current_df, col)
                        
                        # For null filters, we validate by row count AND verify structure match
                        # when possible. If row count matches, it's likely a null filter.
                        if len(projected) == len(output_df):
                            before = len(current_df)
                            current_df = projected.reset_index(drop=True)
                            value_filters.append({
                                "column": col, 
                                "operator": op, 
                                "value": None, 
                                "type": "Null"
                            })
                            filtered_cols.add(col)
                            found = True
                            if verbose:
                                print(f"  ✅ {col} {op}  ({before} → {len(current_df)} rows)")
                            break
            except Exception as exc:
                if verbose:
                    print(f"  ❌ {col} (null check): {exc}")
                # Continue to next column if null check fails
                pass

            if not (is_num or is_str):
                continue

            if is_num and _is_serial_index_column(input_df[col], output_df[col], col):
                continue

            try:
                if is_num:
                    filters = _detect_numeric_filters(current_df[col], output_df[col])
                else:
                    filters = _detect_string_equality_filters(current_df[col], output_df[col])

                if not filters:
                    continue

                f   = filters[0]
                op  = f["operator"]
                val = f["value"]

                if not _validate_filter_on_rows(current_df, output_df, op, col, val, exclude_cols=filtered_cols):
                    continue

                before = len(current_df)

                op_fn = {
                    "==":      lambda df, c, v: df[df[c] == v],
                    "!=":      lambda df, c, v: df[df[c] != v],
                    ">":       lambda df, c, v: df[df[c] >  v],
                    "<":       lambda df, c, v: df[df[c] <  v],
                    ">=":      lambda df, c, v: df[df[c] >= v],
                    "<=":      lambda df, c, v: df[df[c] <= v],
                    "not_null": lambda df, c, v: df[df[c].notna()],
                    "is_null":  lambda df, c, v: df[df[c].isna()],
                }
                # Reset index after filtering so subsequent iterations keep
                # aligned integer indices (avoids "identically-labeled" errors)
                current_df = op_fn[op](current_df, col, val).reset_index(drop=True)

                value_filters.append({
                    "column":   col,
                    "operator": op,
                    "value":    val,
                    "type":     "Numeric" if is_num else "String",
                })
                filtered_cols.add(col)
                found = True

                if verbose:
                    print(f"  ✅ {col} {op} {val}  ({before} → {len(current_df)} rows)")
                break

            except Exception as exc:
                if verbose:
                    print(f"  ❌ {col}: {exc}")
                continue

        if not found:
            break

    return value_filters


# ─────────────────────────────────────────────────────────────────────────────
# Row-matching based fallback filter detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_filter_by_row_matching(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """
    Fallback filter detection using row-matching approach.
    
    When input has more rows than output, this function:
    1. First checks if a null filter on any column can fully explain the row reduction
    2. Then tries to find common columns between input and output for row-matching
    3. Identifies which input rows were excluded and finds a column+value that 
       uniquely identifies excluded rows
    
    Returns a list of filter rules.
    """
    if len(input_df) <= len(output_df):
        return []
    
    # ─────────────────────────────────────────────────────────────────────────
    # FIRST: Check for null filters on each column
    # ─────────────────────────────────────────────────────────────────────────
    for col in input_df.columns:
        if col not in output_df.columns:
            continue
        
        try:
            null_filters = _detect_null_filters(input_df[col], output_df[col])
            if null_filters:
                op = null_filters[0]["operator"]
                
                # Verify the null filter actually produces the right row count
                null_op_fn = {
                    "not_null": lambda df, c: df[df[c].notna()],
                    "is_null":  lambda df, c: df[df[c].isna()],
                }
                
                if op in null_op_fn:
                    projected = null_op_fn[op](input_df, col)
                    if len(projected) == len(output_df):
                        if verbose:
                            print(f"  [row_matching] ✅ Found null filter via fallback: {col} {op}")
                        return [{"column": col, "operator": op, "value": None, "type": "Null"}]
        except Exception as e:
            if verbose:
                print(f"  [row_matching] Error checking null filter on {col}: {e}")
            continue
    
    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK: Standard row-matching for value filters
    # ─────────────────────────────────────────────────────────────────────────
    
    # Find common columns (columns that exist in both and are not derived)
    common_cols = [c for c in input_df.columns if c in output_df.columns]
    if not common_cols:
        if verbose:
            print("  [row_matching] No common columns found")
        return []
    
    if verbose:
        print(f"  [row_matching] Common columns: {common_cols}")
        print(f"  [row_matching] Input rows: {len(input_df)}, Output rows: {len(output_df)}")
    
    # Create row signatures for matching
    def row_signature(row, cols):
        return tuple(str(row[c]) for c in cols if c in row.index)
    
    # Get signatures of output rows
    output_sigs = set()
    for _, row in output_df.iterrows():
        sig = row_signature(row, common_cols)
        output_sigs.add(sig)
    
    # Find input rows that are NOT in output (excluded rows)
    excluded_indices = []
    for idx, row in input_df.iterrows():
        sig = row_signature(row, common_cols)
        if sig not in output_sigs:
            excluded_indices.append(idx)
    
    if not excluded_indices:
        if verbose:
            print("  [row_matching] No excluded rows found")
        return []
    
    if verbose:
        print(f"  [row_matching] Found {len(excluded_indices)} excluded rows")
    
    # Get excluded rows
    excluded_df = input_df.loc[excluded_indices]
    kept_df = input_df.drop(excluded_indices)
    
    # Try to find a column+value that uniquely identifies excluded rows
    rules = []
    for col in input_df.columns:
        try:
            if not (_is_numeric(input_df[col]) or _is_comparable_string(input_df[col])):
                continue
            
            excluded_vals = set(excluded_df[col].dropna().astype(str))
            kept_vals = set(kept_df[col].dropna().astype(str))
            
            # Find values that appear ONLY in excluded rows
            unique_to_excluded = excluded_vals - kept_vals
            
            if len(unique_to_excluded) == 1:
                # Perfect: exactly one value identifies excluded rows
                excl_val = list(unique_to_excluded)[0]
                
                # Convert back to proper type
                if _is_numeric(input_df[col]):
                    try:
                        excl_val = int(float(excl_val)) if float(excl_val) == int(float(excl_val)) else float(excl_val)
                    except:
                        pass
                
                # Verify: applying != filter should give us kept_df row count
                test_result = input_df[input_df[col].astype(str) != str(excl_val)]
                if len(test_result) == len(output_df):
                    rules.append({
                        "column": col,
                        "operator": "!=",
                        "value": excl_val,
                        "type": "Numeric" if _is_numeric(input_df[col]) else "String",
                    })
                    if verbose:
                        print(f"  [row_matching] ✅ Found filter: {col} != {excl_val}")
                    break  # Found a filter, stop searching
                    
        except Exception as e:
            if verbose:
                print(f"  [row_matching] ❌ Error checking {col}: {e}")
            continue
    
    return rules


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_null_filters_standalone(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """
    Standalone null filter detection - checks each column for null/not-null filters
    and returns detected rules without other filter types.
    
    This can be used as a priority check before other filter types are tested.
    
    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (after filters applied).
        column_mapping: Optional {output_col: input_col} mapping.
        verbose:        Print detection details.
    
    Returns:
        List of null filter rules: [{"column": str, "operator": str, "value": None}, ...]
    """
    rules: List[Dict[str, Any]] = []
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    
    # Apply column mapping if provided
    if column_mapping:
        rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
        out_df = output_df.rename(columns=rename_map)
        out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    else:
        out_df = output_df
    
    # Build inverse mapping {input_col: output_col}
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    
    # Check each column for null filters
    for col in input_df.columns:
        if col not in out_df.columns:
            continue
        
        try:
            null_filters = _detect_null_filters(input_df[col], out_df[col])
            if null_filters:
                op = null_filters[0]["operator"]
                
                # Verify this actually produces the right row count
                null_op_fn = {
                    "not_null": lambda df, c: df[df[c].notna()],
                    "is_null":  lambda df, c: df[df[c].isna()],
                }
                
                if op in null_op_fn:
                    projected = null_op_fn[op](input_df, col)
                    if len(projected) == len(out_df):
                        out_col = inv_map.get(col, col)
                        rules.append({
                            "column": out_col,
                            "operator": op,
                            "value": None
                        })
                        if verbose:
                            print(f"  [null_filter] ✅ Detected: {out_col} {op}")
        except Exception as e:
            if verbose:
                print(f"  [null_filter] ❌ Error on {col}: {e}")
            continue
    
    return rules


def build_value_filter_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Detect value-based row filters and return the phase_2_rows 'filter' config
    block contribution from numeric / string-equality operators.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (after filters applied).
        column_mapping: Optional {input_col: output_col} name mapping.
                        When provided the output is remapped before comparison.
        verbose:        Print per-column detection details.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {"column": str, "operator": str, "value": Any}
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

    raw_rules: List[Dict[str, Any]] = []
    
    # PRIORITY 1: Check for null filters first (before other filter types)
    # This ensures that ProductID IS NOT NULL filters are detected
    if verbose:
        print("  [filter] Checking for null-based filters first...")
    null_rules = detect_null_filters_standalone(input_df, out_df, column_mapping, verbose=verbose)
    raw_rules.extend(null_rules)
    
    # Check if we need to look for additional filters
    # If null filters were found and explain the row reduction, we're done
    # Otherwise continue with other filter types
    needs_additional_filters = (
        not null_rules or  # No null filters detected
        len(input_df) > len(out_df)  # Null filters found but row count still differs
    )
    
    if needs_additional_filters:
        if null_rules and verbose:
            print(f"  [filter] Null filters detected but row count still differs, checking for additional filters...")
        elif verbose and not null_rules:
            print("  [filter] No null filters detected, trying other filter types...")
        
        # PRIORITY 2: Try primary sequential detection for non-null filters
        sequential_rules = _detect_value_filters_sequential(input_df, out_df, verbose=verbose)
        # Avoid adding duplicate rules that are already in null_rules
        for rule in sequential_rules:
            # Only add if it's not already detected as a null filter
            if not any(r['column'] == rule['column'] and r['operator'] in ('not_null', 'is_null') 
                      for r in null_rules):
                raw_rules.append(rule)
        
        # PRIORITY 3: If no rules found but row count differs, try row-matching fallback
        if not sequential_rules and len(input_df) > len(out_df):
            if verbose:
                print("  [filter] No sequential rules found, trying row-matching fallback...")
            fallback_rules = _detect_filter_by_row_matching(input_df, out_df, verbose=verbose)
            raw_rules.extend(fallback_rules)

    # Build inverse mapping {input_col: output_col} to translate detected names to output namespace
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}

    # Strip internal "type" field (if present); normalise column name back to output namespace
    rules: List[Dict[str, Any]] = []
    seen_rules = set()  # Track (column, operator) pairs to avoid duplicates
    
    for r in raw_rules:
        col = r.get("column", "")
        col = inv_map.get(col, col)
        op = r.get("operator", "")
        
        # Create a unique key for this rule
        rule_key = (col, op)
        
        # Skip if we've already added this exact rule (deduplicate)
        if rule_key in seen_rules:
            continue
            
        seen_rules.add(rule_key)
        rules.append({
            "column": col, 
            "operator": op, 
            "value": r.get("value", None)
        })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
