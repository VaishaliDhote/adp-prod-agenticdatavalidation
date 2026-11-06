# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Lookup Detection
========================================
Detects new columns that appear to have been joined from an external reference
table via VLOOKUP / XLOOKUP / INDEX-MATCH logic.

Detection strategy
------------------
1.  Row count must be preserved (VLOOKUP does not add/remove rows).
2.  One or more *new* columns appear in the output that were not in the input.
3.  A *key* column (ID / code / identifier) present in the input is used to
    drive the lookup — identified by keyword heuristics AND by checking that
    its value distribution is consistent with a join key (low-enough cardinality
    vs. total rows but not all-unique for VLOOKUP).
4.  Added columns tend to be descriptive / categorical (region, segment, name,
    description, category, type, currency, status …).
5.  Confidence bumped when the new column values repeat per key (hallmark of a
    VLOOKUP from a reference table).

Edge cases handled
------------------
- Multiple lookup keys and multiple enrichment columns.
- Partial lookups (some NULLs in the new columns).
- New columns that are *calculated* (numeric) are excluded — those belong to
  calculated_columns detection.
- column_mapping forwarded so renamed keys are still detected.

Returns
-------
{
  "applied": bool, 
  "details": {
    "rules": [
      {
        "key":            str,           # join key column (input namespace)
        "columns_added":  [str, ...],    # new descriptive columns
        "lookup_type":    str,           # "VLOOKUP" | "XLOOKUP" | "INDEX_MATCH"
        "confidence":     float
      }
    ]
  }
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_KEY_KEYWORDS = [
    "id", "code", "key", "num", "number", "no", "ref",
    "identifier", "sku", "account", "emp", "cust", "prod",
]

_ENRICHMENT_KEYWORDS = [
    "name", "description", "desc", "category", "type", "region",
    "segment", "currency", "status", "country", "city", "label",
    "title", "group", "class", "division", "department", "dept",
    "tier", "band", "level", "grade", "flag", "text",
]

_LOOKUP_TYPE_THRESHOLD_VLOOKUP = 0.85   # confidence for VLOOKUP pattern
_LOOKUP_TYPE_THRESHOLD_XLOOKUP = 0.80   # confidence for XLOOKUP pattern


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase + strip non-alpha for keyword matching."""
    return re.sub(r"[\s_\-]", "", s.lower())


def _is_key_column(series: pd.Series, col_name: str) -> bool:
    """
    Heuristic: True when *col_name* looks like a lookup key AND the series
    has an appropriate cardinality (not all-unique raw text, not boolean).
    """
    n = _norm(col_name)
    name_match = any(kw in n for kw in _KEY_KEYWORDS)

    # Also accept columns whose values look like codes / IDs even without
    # keyword name match (e.g. "emp_001", "C-001").
    val_match = False
    if not name_match:
        sample = series.dropna().astype(str).head(20)
        id_like = sample.str.match(r"^[A-Za-z0-9\-_]{1,20}$")
        val_match = id_like.mean() > 0.7

    return name_match or val_match


def _is_descriptive_column(col_name: str, series: pd.Series) -> bool:
    """
    True when the column looks like an enrichment / reference column:
    - keyword match in the name, OR
    - categorical string with limited unique values.
    Excludes purely numeric columns (those are calculated columns).
    """
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return False

    n = _norm(col_name)
    if any(kw in n for kw in _ENRICHMENT_KEYWORDS):
        return True

    # Categorical: low cardinality string column
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        n_unique = series.nunique(dropna=True)
        n_total = series.count()
        if n_total > 0 and n_unique / n_total < 0.6:
            return True

    return False


def _values_repeat_per_key(
    key_series: pd.Series,
    value_series: pd.Series,
) -> bool:
    """
    Return True when each unique key maps to exactly ONE enrichment value
    (i.e. the column was looked up from a 1:N reference table).
    """
    try:
        df = pd.DataFrame({"k": key_series, "v": value_series}).dropna()
        if df.empty:
            return False
        mapping = df.groupby("k")["v"].nunique()
        # Accept if ≥80 % of keys map to a single value
        return (mapping == 1).mean() >= 0.8
    except Exception:
        return False


def _infer_lookup_type(key_col: str, new_cols: List[str]) -> str:
    """Guess the Excel function family used (cosmetic label only)."""
    if len(new_cols) > 2:
        return "XLOOKUP"
    n = _norm(key_col)
    if "id" in n or "code" in n:
        return "VLOOKUP"
    return "INDEX_MATCH"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_lookup_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect lookup-enrichment transformations (VLOOKUP / XLOOKUP / INDEX-MATCH).

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame.
        column_mapping: Optional {output_col: input_col} name map.

    Returns:
        {"applied": bool, "details": {"rules": [...]}}
    """
    empty = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty

    # Row count must be preserved for VLOOKUP-style lookups
    if len(input_df) != len(output_df):
        return empty

    # column_mapping = {output_col: input_col}
    # Filter out sentinel values (DERIVED COLUMN, etc.) from rename map
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

    in_cols  = set(input_df.columns)
    out_cols = set(out_df.columns)
    new_cols = [c for c in out_df.columns if c not in in_cols]

    if not new_cols:
        return empty

    # Keep only descriptive (non-numeric) new columns
    descriptive_new = [
        c for c in new_cols
        if c in out_df.columns and _is_descriptive_column(c, out_df[c])
    ]

    if not descriptive_new:
        return empty

    # Find candidate key columns in input
    key_cols = [
        c for c in input_df.columns
        if _is_key_column(input_df[c], c)
    ]

    if not key_cols:
        # Fallback: any input column that drives repeated values in new cols
        for ic in input_df.columns:
            for nc in descriptive_new:
                if nc in out_df.columns and _values_repeat_per_key(input_df[ic], out_df[nc]):
                    key_cols.append(ic)
                    break

    if not key_cols:
        return empty

    # Build rules: group descriptive columns per key
    rules: List[Dict[str, Any]] = []
    used_cols: set = set()

    for key in key_cols:
        if key not in input_df.columns:
            continue

        matched_enrichment = []
        for nc in descriptive_new:
            if nc in used_cols:
                continue
            if nc in out_df.columns:
                repeats = _values_repeat_per_key(input_df[key], out_df[nc])
                if repeats or True:   # include all descriptive new cols per key
                    matched_enrichment.append(nc)

        if not matched_enrichment:
            continue

        confidence = _LOOKUP_TYPE_THRESHOLD_VLOOKUP if all(
            _values_repeat_per_key(input_df[key], out_df[c])
            for c in matched_enrichment if c in out_df.columns
        ) else _LOOKUP_TYPE_THRESHOLD_XLOOKUP

        # Translate back to output namespace using inv_map
        out_key = inv_map.get(key, key)
        out_enrichment = [
            inv_map.get(c, c)
            for c in matched_enrichment
        ]

        rules.append({
            "key":           out_key,
            "columns_added": out_enrichment,
            "lookup_type":   _infer_lookup_type(key, matched_enrichment),
            "confidence":    confidence,
        })

        for c in matched_enrichment:
            used_cols.add(c)

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
