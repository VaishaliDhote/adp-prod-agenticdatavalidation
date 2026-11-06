# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Text Operations Detection
================================================
Detects string manipulations applied between input and output DataFrames.

Operations detected
-------------------
  upper / lower / proper   : UPPER(), LOWER(), PROPER()
  trim                     : TRIM()
  substring / left / right : LEFT(n), RIGHT(n), MID(start, len)
  length                   : LEN()
  replace / substitute     : SUBSTITUTE(), REPLACE()
  concatenation            : CONCAT(), "&" operator (covered here as text op
                             on *existing* columns — not new formula columns)
  prefix / suffix strip    : removing leading/trailing fixed strings

Detection strategy
------------------
For columns that EXIST in both input and output (modified in-place):
  - Compare value-by-value and infer the transformation applied.
For NEW columns not in input:
  - Check if the new column is a text-derived version of an input string column
    (e.g. length column, substring column, case-converted column).

Edge cases handled
------------------
- NaN / None in either side → skipped.
- Numeric-looking strings stored as object dtype in Excel. 
- Mixed-case data (partial case conversions).
- column_mapping forwarded so renamed columns are still checked.

Returns
-------
{
  "applied": bool,
  "details": {
    "rules": [
      {
        "column":    str,              # output column name
        "source":    str,              # input column the op was applied to
        "operation": str,              # upper | lower | proper | trim |
                                       # left | right | mid | len |
                                       # replace | substitute | concatenation
        "params":    dict,             # operation-specific params (optional)
        "confidence": float
      }
    ]
  }
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MATCH_THRESHOLD = 0.80
_SAMPLE_SIZE     = 100   # max rows used for row-by-row verification


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _str_series(series: pd.Series) -> pd.Series:
    """Return a clean string representation of a series (NaN → '')."""
    return series.fillna("").astype(str)


def _match_ratio(a: pd.Series, b: pd.Series) -> float:
    valid = (a != "") & (b != "")
    if valid.sum() == 0:
        return 0.0
    return float((a[valid] == b[valid]).mean())


def _sample(s: pd.Series, n: int = _SAMPLE_SIZE) -> pd.Series:
    return s.iloc[:n] if len(s) > n else s


def _is_string_col(series: pd.Series) -> bool:
    return (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
    )


def _row_identity_tuple(row: pd.Series, exclude_col: str) -> tuple:
    """
    Build a hashable identity key from all columns EXCEPT the one being tested.
    Used to match the same logical row across input and output DataFrames
    even when row indices differ (e.g. after filtering).
    """
    return tuple(
        str(v) for k, v in row.items() if k != exclude_col
    )


# ─────────────────────────────────────────────────────────────────────────────
# In-place transformation detectors  (same column, different values)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_case(
    inp: pd.Series, out: pd.Series, col: str
) -> Optional[Dict[str, Any]]:
    """Detect UPPER / LOWER / PROPER case conversion."""
    a = _str_series(_sample(inp)).reset_index(drop=True)
    b = _str_series(_sample(out)).reset_index(drop=True)
    
    # Ensure both series have the same length before comparison
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)

    for op, fn in [
        ("upper",  lambda s: s.str.upper()),
        ("lower",  lambda s: s.str.lower()),
        ("proper", lambda s: s.str.title()),
    ]:
        candidate = fn(a)
        ratio = _match_ratio(candidate, b)
        if ratio >= _MATCH_THRESHOLD:
            return {"column": col, "source": col, "operation": op,
                    "params": {}, "confidence": ratio}
    return None


def _detect_trim(
    inp: pd.Series, out: pd.Series, col: str
) -> Optional[Dict[str, Any]]:
    """Detect TRIM (leading/trailing whitespace removal)."""
    a = _str_series(_sample(inp)).reset_index(drop=True)
    b = _str_series(_sample(out)).reset_index(drop=True)
    
    # Ensure both series have the same length before comparison
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)
    
    # Only consider columns where some values actually have surrounding spaces
    has_spaces = (a != a.str.strip()).any()
    if not has_spaces:
        return None
    ratio = _match_ratio(a.str.strip(), b)
    if ratio >= _MATCH_THRESHOLD:
        return {"column": col, "source": col, "operation": "trim",
                "params": {}, "confidence": ratio}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Row-tuple-based substitution validator  (full DataFrame aware)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_substitution_with_row_tuples(
    inp_df: pd.DataFrame,
    out_df: pd.DataFrame,
    col: str,
) -> Optional[Dict[str, str]]:
    """
    Determine whether any value in *col* was genuinely substituted by
    comparing FULL ROW IDENTITY across the two DataFrames.

    Algorithm
    ---------
    For every input row that has a value V in *col*:

      1. Build a row-identity key = tuple of all OTHER columns' string values.
         This fingerprints the row without using the column under test.

      2. Look up that fingerprint in the output DataFrame.
         - If the fingerprint is NOT present in output at all → the row was
           FILTERED OUT, not substituted.  Ignore it.
         - If the fingerprint IS present and the output value for *col* equals
           V → unchanged row, skip.
         - If the fingerprint IS present and the output value for *col* differs
           from V → genuine substitution V → output_value.  Record it.

    Only substitutions confirmed by matching row identities are returned.
    Returns None when no genuine substitutions are found.

    Why this eliminates false positives
    ------------------------------------
    When rows are filtered out (e.g. Status == "Terminate"), their email
    addresses disappear.  Rows that SURVIVE have entirely different emails.
    Because the surviving rows have different identity tuples (different Name,
    Age, City, Mobile# etc.), the fingerprint lookup will NOT find a match for
    the filtered-out rows — so they are correctly ignored.
    """
    if col not in inp_df.columns or col not in out_df.columns:
        return None

    # Build a lookup: identity_key → col_value  for the output DataFrame.
    # If multiple output rows share the same identity key, keep them all
    # (use a dict of key → set of values).
    out_identity: Dict[tuple, set] = {}
    for _, row in out_df.iterrows():
        key = _row_identity_tuple(row, exclude_col=col)
        val = str(row[col]) if pd.notna(row[col]) else ""
        out_identity.setdefault(key, set()).add(val)

    validated: Dict[str, str] = {}

    for _, row in inp_df.iterrows():
        src_val = str(row[col]) if pd.notna(row[col]) else ""
        if not src_val:
            continue

        key = _row_identity_tuple(row, exclude_col=col)

        # Row not present in output at all → filtered out, NOT substituted
        if key not in out_identity:
            continue

        out_vals = out_identity[key]

        # Row present and value unchanged → no substitution on this row
        if src_val in out_vals:
            continue

        # Row present but value differs → confirmed substitution
        if len(out_vals) == 1:
            tgt_val = next(iter(out_vals))
        else:
            # Multiple output rows share the same identity key (unlikely but
            # safe); only record if ALL of them changed to the same target
            if len(out_vals) > 1:
                continue
            tgt_val = next(iter(out_vals))

        # Consistency check: don't overwrite a previous mapping with a
        # different target for the same source value
        if src_val in validated and validated[src_val] != tgt_val:
            # Inconsistent mapping — this column has no single substitution rule
            return None

        validated[src_val] = tgt_val

    return validated if validated else None


def _detect_replace_substitute(
    inp: pd.Series,
    out: pd.Series,
    col: str,
    inp_df: Optional[pd.DataFrame] = None,
    out_df: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """
    Detect SUBSTITUTE / REPLACE using full-DataFrame row-tuple validation.

    When inp_df / out_df are provided the full-row identity check is used to
    guarantee that only genuine in-place value changes are reported — not
    artifacts of row filtering.  Falls back to series-only comparison when
    the full DataFrames are not available.
    """
    if inp_df is not None and out_df is not None:
        validated = _validate_substitution_with_row_tuples(inp_df, out_df, col)
    else:
        # Legacy fallback: series-level check (less reliable)
        validated = _validate_substitution_with_row_tuples(
            pd.DataFrame({col: inp}),
            pd.DataFrame({col: out}),
            col,
        )

    if not validated:
        return None

    # Verify with a positionally-aligned sample that the mapping is consistent.
    # After the fix in phases/main.py, inp and out share the same row set,
    # so positional alignment is valid here.
    a = _str_series(_sample(inp)).reset_index(drop=True)
    b = _str_series(_sample(out)).reset_index(drop=True)
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)

    predicted = a.replace(validated)
    ratio = _match_ratio(predicted, b)

    if ratio >= _MATCH_THRESHOLD:
        return {
            "column":     col,
            "source":     col,
            "operation":  "substitute",
            "params":     {"replacements": dict(list(validated.items())[:10])},
            "confidence": ratio,
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# New-column derivation detectors
# ─────────────────────────────────────────────────────────────────────────────

def _detect_length(
    src_series: pd.Series,
    new_series: pd.Series,
    src_col: str,
    new_col: str,
) -> Optional[Dict[str, Any]]:
    """Detect LEN(): new numeric column = length of source string column."""
    if not pd.api.types.is_numeric_dtype(new_series):
        return None
    a = _str_series(_sample(src_series))
    b = _sample(new_series).reset_index(drop=True)
    a = a.reset_index(drop=True)
    
    # Ensure both series have the same length before comparison
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)
    
    candidate = a.str.len().astype(float)
    b_float = b.astype(float)
    valid = b_float.notna()
    if valid.sum() == 0:
        return None
    ratio = float((candidate[valid] == b_float[valid]).mean())
    if ratio >= _MATCH_THRESHOLD:
        return {"column": new_col, "source": src_col, "operation": "len",
                "params": {}, "confidence": ratio}
    return None


def _detect_left_right_mid(
    src_series: pd.Series,
    new_series: pd.Series,
    src_col: str,
    new_col: str,
) -> Optional[Dict[str, Any]]:
    """Detect LEFT(n) / RIGHT(n) / MID(start, length)."""
    if not _is_string_col(new_series):
        return None
    a = _str_series(_sample(src_series)).reset_index(drop=True)
    b = _str_series(_sample(new_series)).reset_index(drop=True)
    
    # Ensure both series have the same length before comparison
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)

    max_len = int(a.str.len().max() or 0)

    # LEFT(n)
    for n in range(1, min(max_len + 1, 20)):
        ratio = _match_ratio(a.str[:n], b)
        if ratio >= _MATCH_THRESHOLD:
            return {"column": new_col, "source": src_col,
                    "operation": "left",
                    "params": {"length": n}, "confidence": ratio}

    # RIGHT(n)
    for n in range(1, min(max_len + 1, 20)):
        ratio = _match_ratio(a.str[-n:], b)
        if ratio >= _MATCH_THRESHOLD:
            return {"column": new_col, "source": src_col,
                    "operation": "right",
                    "params": {"length": n}, "confidence": ratio}

    # MID(start, length)
    for start in range(0, min(max_len, 15)):
        for length in range(1, min(max_len - start + 1, 15)):
            ratio = _match_ratio(a.str[start: start + length], b)
            if ratio >= _MATCH_THRESHOLD:
                return {"column": new_col, "source": src_col,
                        "operation": "mid",
                        "params": {"start": start, "length": length},
                        "confidence": ratio}
    return None


def _detect_case_derived(
    src_series: pd.Series,
    new_series: pd.Series,
    src_col: str,
    new_col: str,
) -> Optional[Dict[str, Any]]:
    """Detect UPPER / LOWER / PROPER on a NEW derived column."""
    if not _is_string_col(new_series):
        return None
    a = _str_series(_sample(src_series)).reset_index(drop=True)
    b = _str_series(_sample(new_series)).reset_index(drop=True)
    
    # Ensure both series have the same length before comparison
    min_len = min(len(a), len(b))
    a = a.iloc[:min_len].reset_index(drop=True)
    b = b.iloc[:min_len].reset_index(drop=True)
    
    for op, fn in [
        ("upper",  lambda s: s.str.upper()),
        ("lower",  lambda s: s.str.lower()),
        ("proper", lambda s: s.str.title()),
    ]:
        ratio = _match_ratio(fn(a), b)
        if ratio >= _MATCH_THRESHOLD:
            return {"column": new_col, "source": src_col,
                    "operation": op, "params": {}, "confidence": ratio}
    return None


def _detect_fill_down(
    src_series: pd.Series,
    new_series: pd.Series,
    src_col: str,
    new_col: str,
) -> Optional[Dict[str, Any]]:
    """Detect FILL DOWN: new column = forward-fill of source column."""
    # Forward fill the source series
    candidate = src_series.ffill()
    ratio = _match_ratio(_str_series(candidate), _str_series(new_series))
    if ratio >= _MATCH_THRESHOLD:
        return {"column": new_col, "source": src_col, "operation": "fill_down",
                "params": {}, "confidence": ratio}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_text_operations_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect text / string operations.

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
    # Drop duplicates: multiple output cols can map to the same input col name
    # (e.g. Full_Name + Reconstructed_Full_Name → Full_Name). Keep first occurrence.
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    # Create inv_map for translating back to output namespace
    inv_map: Dict[str, str] = {v: k for k, v in real_rename_map.items()} if real_rename_map else {}

    # Reset indices so positional comparisons are valid
    inp_df_reset = input_df.reset_index(drop=True)
    out_df_reset = out_df.reset_index(drop=True)

    rules: List[Dict[str, Any]] = []

    # ── In-place text transforms on existing columns ──────────────────────────
    # Only check in-place transforms when row counts match
    if len(inp_df_reset) == len(out_df_reset):
        common_cols = [
            c for c in inp_df_reset.columns
            if c in out_df_reset.columns and _is_string_col(inp_df_reset[c])
        ]
        for col in common_cols:
            inp = inp_df_reset[col]
            out = out_df_reset[col]

            # Skip if all values are identical (no transformation)
            if _str_series(inp).reset_index(drop=True).equals(
                _str_series(out).reset_index(drop=True)
            ):
                continue

            result = None

            # Try case / trim first (they don't need full-DF context)
            for detector in [_detect_case, _detect_trim]:
                result = detector(inp, out, col)
                if result:
                    break

            # Substitute: pass full DataFrames for row-identity validation
            if result is None:
                result = _detect_replace_substitute(
                    inp, out, col,
                    inp_df=inp_df_reset,
                    out_df=out_df_reset,
                )

            if result:
                # Translate back to output namespace using inv_map
                out_col = inv_map.get(col, col)
                result["column"] = out_col
                result["source"] = out_col
                rules.append(result)

    # ── New columns derived from existing string columns ─────────────────────
    new_cols = [c for c in out_df_reset.columns if c not in set(inp_df_reset.columns)]
    # Use out_df_reset for source cols to ensure row alignment with target (new_series)
    src_str_cols = [c for c in out_df_reset.columns if _is_string_col(out_df_reset[c]) and c not in new_cols]

    for new_col in new_cols:
        new_series = out_df_reset[new_col].reset_index(drop=True)
        best_rule: Optional[Dict[str, Any]] = None
        best_conf = 0.0

        for src_col in src_str_cols:
            src_series = out_df_reset[src_col].reset_index(drop=True)

            for detector in [
                _detect_length,
                _detect_left_right_mid,
                _detect_case_derived,
                _detect_fill_down,
            ]:
                result = detector(src_series, new_series, src_col, new_col)
                if result and result["confidence"] > best_conf:
                    best_conf = result["confidence"]
                    best_rule = result

        if best_rule:
            # Translate back to output namespace using inv_map
            out_col = inv_map.get(new_col, new_col)
            out_src = inv_map.get(best_rule["source"], best_rule["source"])
            best_rule["column"] = out_col
            best_rule["source"] = out_src
            rules.append(best_rule)

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
