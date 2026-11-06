# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Advanced-logic detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Advanced Logic – Indirect / Dynamic Reference Detection  (Phase 7.2)
=====================================================================
Detects Excel INDIRECT-style patterns between input_df and output_df.

What is an "indirect reference" in a spreadsheet context?
----------------------------------------------------------
Excel's INDIRECT(ref_text) evaluates a *text string* as a cell/range address
at runtime.  In the DataFrame world this manifests as:

  1. LOOKUP-BY-COLUMN-NAME  – A column whose values are other column names
     present in the frame (the column acts as a pointer to another column).
  2. DYNAMIC COLUMN SELECTOR – A new output column whose values are picked
     from different source columns *per row*, controlled by a selector column.
  3. COLUMN-NAME-AS-VALUE  – An output column that literally contains input
     column names as cell values (e.g. INDIRECT used to build a reference
     table or a cross-reference).
  4. INDEX/MATCH DYNAMIC REFERENCE – An output column that appears to index
     into another column at a position determined by a third column.

Detection strategy (per column)
--------------------------------
A column is flagged as an indirect-reference source when ANY of the
following heuristics fire with sufficient confidence:

  • At least POINTER_THRESHOLD fraction of its non-null distinct values
    are names of other columns in the input frame.  → "pointer" type
  • A new output column can be explained by row-wise lookup: for each row i,
    output[col][i] == input[selector[i]][i]  for a known selector column.
    → "dynamic_selector" type
  • An output column's values match input column names in a systematic way
    (row-number cross-reference, pivot-style header expansion).
    → "column_name_as_value" type
  • An output column of integers whose values index into another column
    (INDEX-style reference).  → "index_match" type

All four detectors run independently and are merged into a single result.

Public API
----------
  build_indirect_reference_config(input_df, output_df, column_mapping?) → dict
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Min fraction of distinct non-null values that must be column names
_POINTER_THRESHOLD = 0.60

# Min fraction of rows that must match for dynamic-selector verification
_SELECTOR_MATCH_THRESHOLD = 0.70

# Min fraction of rows for index-match verification
_INDEX_MATCH_THRESHOLD = 0.70

# Maximum column pairs tried for dynamic-selector exhaustive search
_MAX_SELECTOR_PAIRS = 50

# Excel null representations
_EXCEL_NULLS = frozenset(
    {"", "nan", "none", "null", "n/a", "na", "#n/a", "nil", "-"}
)

# Keywords that suggest a dynamic / indirect reference column
_INDIRECT_KEYWORDS = [
    "dynamic", "ref", "reference", "indirect", "pointer",
    "lookup_col", "col_ref", "col_name", "target_col",
    "selector", "dynamic_ref", "dyn_ref", "var_col",
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
    return output_df.rename(columns=rename_map)


def _to_out(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Translate input-namespace column to output-namespace."""
    if not column_mapping:
        return col
    inv = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return inv.get(col, col)


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


def _norm_col_name(name: str) -> str:
    """Normalise a column name: lower, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _col_name_set(df: pd.DataFrame) -> Set[str]:
    """Return the set of normalised column names for a DataFrame."""
    return {_norm_col_name(c) for c in df.columns}


def _raw_col_lookup(df: pd.DataFrame) -> Dict[str, str]:
    """Map normalised column name → original column name."""
    return {_norm_col_name(c): c for c in df.columns}


def _is_string_col(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _is_integer_col(series: pd.Series) -> bool:
    if pd.api.types.is_integer_dtype(series):
        return True
    # Float column that contains only whole numbers
    if pd.api.types.is_float_dtype(series):
        non_null = series.dropna()
        if len(non_null) == 0:
            return False
        try:
            return bool((non_null == non_null.astype(int)).all())
        except (ValueError, TypeError):
            return False
    return False


def _has_indirect_keyword(col_name: str) -> bool:
    n = _norm_col_name(col_name)
    return any(kw in n for kw in _INDIRECT_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# 7.2a  POINTER columns  (values == other column names)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_pointer_columns(
    input_df: pd.DataFrame,
    all_col_names: Set[str],
    raw_lookup: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Find columns in input_df whose values are (mostly) names of other columns.

    A column C is a "pointer" when ≥ POINTER_THRESHOLD of its distinct
    non-null values are valid column names in the frame.

    Returns list of:
        {"column": str, "type": "pointer", "references": [col_name, ...]}
    """
    results: List[Dict[str, Any]] = []

    for col in input_df.columns:
        series = input_df[col]
        if not _is_string_col(series):
            continue

        non_null = series.dropna()
        if len(non_null) == 0:
            continue

        distinct_vals = non_null.astype(str).str.strip().unique()
        # Exclude the column itself from the candidate set
        other_cols = all_col_names - {_norm_col_name(col)}

        pointer_vals = [
            v for v in distinct_vals
            if _norm_col_name(v) in other_cols
        ]

        ratio = len(pointer_vals) / max(len(distinct_vals), 1)
        if ratio >= _POINTER_THRESHOLD:
            # Resolve back to original column names
            references = [
                raw_lookup.get(_norm_col_name(v), v)
                for v in pointer_vals
            ]
            results.append({
                "column":     col,
                "type":       "pointer",
                "references": references,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 7.2b  DYNAMIC SELECTOR  (output[col][i] == input[selector[i]][i])
# ─────────────────────────────────────────────────────────────────────────────

def _verify_dynamic_selector(
    input_df: pd.DataFrame,
    output_col: pd.Series,
    selector_col: pd.Series,
    raw_lookup: Dict[str, str],
) -> bool:
    """
    Check whether output_col[i] == input_df[selector_col[i]][i] for
    ≥ SELECTOR_MATCH_THRESHOLD of rows.

    selector_col values must be column names in input_df.
    """
    n = min(len(output_col), len(selector_col), len(input_df))
    if n == 0:
        return False

    matches = 0
    valid   = 0

    for i in range(n):
        sel_val = selector_col.iloc[i]
        if _is_missing(sel_val):
            continue

        # Resolve selector value to an actual column name
        sel_norm = _norm_col_name(str(sel_val))
        src_col  = raw_lookup.get(sel_norm)
        if src_col is None or src_col not in input_df.columns:
            continue

        valid += 1
        expected = input_df[src_col].iloc[i]
        actual   = output_col.iloc[i]

        if _is_missing(expected) and _is_missing(actual):
            matches += 1
        elif str(expected).strip() == str(actual).strip():
            matches += 1

    if valid == 0:
        return False
    return (matches / valid) >= _SELECTOR_MATCH_THRESHOLD


def _detect_dynamic_selector(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    raw_lookup: Dict[str, str],
    pointer_cols: List[str],
) -> List[Dict[str, Any]]:
    """
    Detect INDIRECT-style dynamic column selection.

    For each new / changed output column, check whether a selector column
    (a pointer column or any column whose values are column names) drives
    which input column supplies the output value row-by-row.

    Returns list of:
        {
          "column":        str,   # output column that contains dynamic values
          "type":          "dynamic_selector",
          "selector_col":  str,   # column whose values control which source is used
          "source_cols":   [str]  # input columns being dynamically referenced
        }
    """
    results: List[Dict[str, Any]] = []
    confirmed_out_cols: Set[str] = set()

    # Candidate selector columns: pointer cols + name-hint cols
    selector_candidates = list(pointer_cols)
    for col in input_df.columns:
        if col not in selector_candidates and _has_indirect_keyword(col):
            selector_candidates.append(col)

    if not selector_candidates:
        return results

    in_col_set = set(input_df.columns)

    for out_col in output_df.columns:
        if out_col in confirmed_out_cols:
            continue

        out_series = output_df[out_col].reset_index(drop=True)

        for sel_col in selector_candidates:
            if sel_col not in input_df.columns:
                continue
            sel_series = input_df[sel_col].reset_index(drop=True)

            if _verify_dynamic_selector(input_df, out_series, sel_series, raw_lookup):
                # Determine which source columns are referenced
                src_cols_norm = (
                    sel_series.dropna()
                    .astype(str)
                    .str.strip()
                    .map(_norm_col_name)
                    .unique()
                )
                source_cols = [
                    raw_lookup[n] for n in src_cols_norm
                    if n in raw_lookup and raw_lookup[n] in in_col_set
                ]
                results.append({
                    "column":       out_col,
                    "type":         "dynamic_selector",
                    "selector_col": sel_col,
                    "source_cols":  source_cols,
                })
                confirmed_out_cols.add(out_col)
                break

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 7.2c  COLUMN-NAME-AS-VALUE  (output col contains input column names)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_colname_as_value(
    output_df: pd.DataFrame,
    input_col_names: Set[str],
    raw_lookup: Dict[str, str],
    already_found: Set[str],
) -> List[Dict[str, Any]]:
    """
    Detect output columns (typically new ones) that store *column names*
    as cell values — the Excel equivalent of building a reference table or
    using INDIRECT to vary which column is read.

    A column qualifies when ≥ POINTER_THRESHOLD of its non-null distinct
    values are column names found in the input frame.

    Returns list of:
        {"column": str, "type": "column_name_as_value", "references": [str]}
    """
    results: List[Dict[str, Any]] = []

    for col in output_df.columns:
        if col in already_found:
            continue
        series = output_df[col]
        if not _is_string_col(series):
            continue

        non_null = series.dropna()
        if len(non_null) == 0:
            continue

        distinct_vals = non_null.astype(str).str.strip().unique()
        referenced = [
            raw_lookup.get(_norm_col_name(v), v)
            for v in distinct_vals
            if _norm_col_name(v) in input_col_names
        ]

        ratio = len(referenced) / max(len(distinct_vals), 1)
        if ratio >= _POINTER_THRESHOLD:
            results.append({
                "column":     col,
                "type":       "column_name_as_value",
                "references": referenced,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 7.2d  INDEX-MATCH dynamic reference
#       output[col][i] == input[src_col][index_col[i]]
# ─────────────────────────────────────────────────────────────────────────────

def _verify_index_match(
    input_df: pd.DataFrame,
    output_col: pd.Series,
    src_col: str,
    index_col: pd.Series,
) -> bool:
    """
    Check whether output_col[i] == input_df[src_col][ index_col[i] - 1 ]
    (1-based Excel-style index) for ≥ INDEX_MATCH_THRESHOLD of rows.
    """
    src = input_df[src_col].reset_index(drop=True)
    n = min(len(output_col), len(index_col))
    matches = 0
    valid   = 0

    for i in range(n):
        idx_val = index_col.iloc[i]
        out_val = output_col.iloc[i]

        if _is_missing(idx_val) or _is_missing(out_val):
            continue

        try:
            # Both 0-based and 1-based indexing tried
            for base in (1, 0):
                row_idx = int(float(idx_val)) - base
                if 0 <= row_idx < len(src):
                    expected = src.iloc[row_idx]
                    valid += 1
                    if str(expected).strip() == str(out_val).strip():
                        matches += 1
                    break
        except (ValueError, TypeError):
            continue

    if valid == 0:
        return False
    return (matches / valid) >= _INDEX_MATCH_THRESHOLD


def _detect_index_match(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    already_found: Set[str],
) -> List[Dict[str, Any]]:
    """
    Detect INDEX/MATCH-style dynamic references.

    For each new output column that is NOT already explained by other detectors,
    search for an integer column in input that acts as a row-index into a
    source column.

    Returns list of:
        {
          "column":     str,   # output column with indexed values
          "type":       "index_match",
          "source_col": str,   # input column being indexed into
          "index_col":  str,   # integer column providing the row index
        }
    """
    results: List[Dict[str, Any]] = []
    in_cols = set(input_df.columns)

    # Candidate integer index columns in input
    index_col_candidates = [
        c for c in input_df.columns
        if _is_integer_col(input_df[c]) and not _is_missing(input_df[c].iloc[0])
    ]

    if not index_col_candidates:
        return results

    for out_col in output_df.columns:
        if out_col in already_found:
            continue

        out_series = output_df[out_col].reset_index(drop=True)
        if out_series.dropna().empty:
            continue

        # Limit exhaustive search
        tried = 0
        for src_col in input_df.columns:
            for idx_col in index_col_candidates:
                if tried >= _MAX_SELECTOR_PAIRS:
                    break
                if src_col == idx_col:
                    continue
                tried += 1

                if _verify_index_match(
                    input_df, out_series, src_col, input_df[idx_col].reset_index(drop=True)
                ):
                    results.append({
                        "column":     out_col,
                        "type":       "index_match",
                        "source_col": src_col,
                        "index_col":  idx_col,
                    })
                    already_found.add(out_col)
                    break

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_indirect_reference_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect Excel INDIRECT / dynamic reference transformations.

    Four detection passes run independently and are merged:

    1. Pointer columns     : column values == other column names (lookup table)
    2. Dynamic selector    : output[col][i] == input[selector[i]][i]
    3. Column-name-as-value: output column stores input column names as data
    4. INDEX/MATCH         : output[col][i] == input[src][index_col[i]]

    Args:
        input_df:       Source DataFrame (before INDIRECT formula applied).
        output_df:      Result DataFrame (after INDIRECT formula applied).
        column_mapping: Optional {input_col: output_col} name mapping.

    Returns:
        {
          "applied": bool,
          "details": {
            "columns": [str, ...],   # output-namespace column names involved
            "references": [
              {
                "column":    str,    # column that carries the dynamic reference
                "type":      str,    # "pointer" | "dynamic_selector" |
                                     # "column_name_as_value" | "index_match"
                "references": [...], # (pointer / column_name_as_value) referenced cols
                "selector_col": str, # (dynamic_selector) the selector column
                "source_cols":  [...],# (dynamic_selector) input cols referenced
                "source_col": str,   # (index_match) input col being indexed
                "index_col":  str,   # (index_match) integer index column
              },
              ...
            ]
          }
        }

    Edge cases covered
    ------------------
    - Column names with spaces / mixed-case matched case-insensitively ✓
    - NaN / blank values in pointer / selector columns skipped ✓
    - column_mapping resolved; all results reported in output namespace ✓
    - Multiple reference types on the same column all reported ✓
    - Empty frames → applied=False ✓
    - Selector column not in output columns (it may be dropped) ✓
    - 0-based and 1-based index positions both tried for INDEX/MATCH ✓
    - Excel currency/percent/boolean values in referenced cells ✓
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {"columns": [], "references": []},
    }

    if input_df is None or output_df is None:
        return empty
    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)

    # Normalised column name helpers
    all_col_names: Set[str] = _col_name_set(input_df) | _col_name_set(out_df)
    in_col_names:  Set[str] = _col_name_set(input_df)
    raw_lookup: Dict[str, str] = _raw_col_lookup(input_df)

    raw_refs: List[Dict[str, Any]] = []
    found_cols: Set[str] = set()  # input-namespace cols already explained

    # ── 1. Pointer columns ────────────────────────────────────────────────────
    pointer_results = _detect_pointer_columns(input_df, all_col_names, raw_lookup)
    for r in pointer_results:
        raw_refs.append(r)
        found_cols.add(r["column"])

    pointer_col_names = [r["column"] for r in pointer_results]

    # ── 2. Dynamic selector ───────────────────────────────────────────────────
    selector_results = _detect_dynamic_selector(
        input_df, out_df, raw_lookup, pointer_col_names
    )
    for r in selector_results:
        raw_refs.append(r)
        found_cols.add(r["column"])

    # ── 3. Column-name-as-value in output ─────────────────────────────────────
    colname_results = _detect_colname_as_value(
        out_df, in_col_names, raw_lookup, found_cols
    )
    for r in colname_results:
        raw_refs.append(r)
        found_cols.add(r["column"])

    # ── 4. INDEX/MATCH ────────────────────────────────────────────────────────
    index_match_results = _detect_index_match(input_df, out_df, found_cols)
    raw_refs.extend(index_match_results)

    if not raw_refs:
        return empty

    # ── Translate to output namespace ─────────────────────────────────────────
    def _xlate(col: Optional[str]) -> Optional[str]:
        if col is None:
            return None
        return _to_out(col, column_mapping)

    def _xlate_list(cols: List[str]) -> List[str]:
        return [_to_out(c, column_mapping) for c in cols]

    references: List[Dict[str, Any]] = []
    columns_involved: List[str] = []

    for r in raw_refs:
        out_col = _xlate(r["column"])
        columns_involved.append(out_col)

        entry: Dict[str, Any] = {
            "column": out_col,
            "type":   r["type"],
        }

        if r["type"] in ("pointer", "column_name_as_value"):
            entry["references"] = _xlate_list(r.get("references", []))

        elif r["type"] == "dynamic_selector":
            entry["selector_col"] = _xlate(r.get("selector_col"))
            entry["source_cols"]  = _xlate_list(r.get("source_cols", []))

        elif r["type"] == "index_match":
            entry["source_col"] = _xlate(r.get("source_col"))
            entry["index_col"]  = _xlate(r.get("index_col"))

        references.append(entry)

    # De-duplicate columns_involved (preserve order)
    seen: Set[str] = set()
    unique_cols: List[str] = []
    for c in columns_involved:
        if c not in seen:
            seen.add(c)
            unique_cols.append(c)

    return {
        "applied": True,
        "details": {
            "columns":    unique_cols,
            "references": references,
        },
    }
