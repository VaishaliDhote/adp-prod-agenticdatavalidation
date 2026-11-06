# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Math / Numeric Operations Detection
===========================================================
Detects numeric transformations applied to columns that already EXIST in
the input (in-place transforms) OR new columns derived purely by numeric ops.

Operations detected
-------------------
  arithmetic   : col_a + col_b,  col_a - col_b,  col_a * col_b,  col_a / col_b
  rounding     : ROUND(col, n),  CEILING(col, n),  FLOOR(col, n),  TRUNC(col)
  percentage   : col / total * 100  or  col / sum_col
  ratio        : col_a / col_b  (result 0–1)
  absolute     : ABS(col)
  power        : col ** n
  cumulative   : cumsum, cummax, cummin (running totals)

Detection strategy
------------------
For *new* columns (not in input):
  - Try arithmetic combinations of existing numeric input columns (done here
    as a complement to enrichment_calculated — math_ops focuses on in-place
    and rounding/percentage patterns that calculated_columns doesn't cover).

For *existing* columns whose values changed:
  - Round / ceiling / floor: check if out ≈ round(in, n) for n ∈ {0,1,2,3,4}.
  - Absolute value: check if out == abs(in).
  - Percentage conversion: check if out == in * 100 or out == in / 100.

Edge cases handled
------------------
- Division by zero in percentage / ratio → rows with zero denominator skipped.
- NaN / None values → skipped in verification.
- Integer vs float storage in Excel (tolerance-based comparison).
- Cumulative operations over groups (partial match acceptable).
- column_mapping forwarded.

Returns
-------
{
  "applied": bool,
  "details": {
    "rules": [
      {
        "column":    str,        # output column (output namespace)
        "operation": str,        # e.g. "revenue - cost", "ROUND(price, 2)", "profit / revenue"
        "op_type":   str,        # arithmetic | rounding | percentage | ratio | absolute | power | cumulative
        "confidence": float
      }
    ]
  }
}
"""

from __future__ import annotations

import re
from itertools import permutations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MATCH_THRESHOLD = 0.80
_FLOAT_TOL       = 0.5    # absolute tolerance (handles int rounding)
_REL_TOL         = 1e-6   # relative tolerance for large numbers
_MAX_PAIRS       = 60
_ROUND_DECIMALS  = [0, 1, 2, 3, 4]

_ARITH_OPS: List[Tuple[str, str]] = [
    ("-", "arithmetic"),
    ("+", "arithmetic"),
    ("*", "arithmetic"),
    ("/", "ratio"),
]

# Sentinel keywords indicating a derived/calculated output column
_DERIVED_SENTINELS = ("derived", "multisource", "unknown")

def _is_derived_sentinel(value: str) -> bool:
    """Return True if the mapping value is a sentinel for a derived column."""
    if not isinstance(value, str):
        return False
    return any(kw in value.lower() for kw in _DERIVED_SENTINELS)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match_ratio(a: pd.Series, b: pd.Series, tol: float = _FLOAT_TOL) -> float:
    """Match ratio using combined absolute + relative tolerance."""
    try:
        valid = a.notna() & b.notna()
        if valid.sum() == 0:
            return 0.0
        a_val = a[valid].astype(float)
        b_val = b[valid].astype(float)
        diff = (a_val - b_val).abs()
        max_val = np.maximum(a_val.abs(), b_val.abs()).replace(0, 1)
        # Match if absolute diff is small OR relative diff is small
        matches = (diff <= tol) | (diff / max_val <= _REL_TOL)
        return float(matches.mean())
    except Exception:
        return 0.0


def _safe_op(a: pd.Series, b: pd.Series, op: str) -> Optional[pd.Series]:
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            denom = b.replace(0, np.nan)
            return a / denom
    except Exception:
        return None


def _is_numeric(series: pd.Series) -> bool:
    return (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


# ─────────────────────────────────────────────────────────────────────────────
# New-column detectors
# ─────────────────────────────────────────────────────────────────────────────

def _detect_arithmetic_new(
    new_col: str,
    new_series: pd.Series,
    num_input_cols: Dict[str, pd.Series],
) -> Optional[Dict[str, Any]]:
    """Try arithmetic / ratio on pairs of numeric input columns."""
    if not _is_numeric(new_series):
        return None

    target = new_series.reset_index(drop=True)
    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0

    pairs = list(permutations(num_input_cols.keys(), 2))[:_MAX_PAIRS]

    for col_a, col_b in pairs:
        a = num_input_cols[col_a].reset_index(drop=True)
        b = num_input_cols[col_b].reset_index(drop=True)

        for op_sym, op_type in _ARITH_OPS:
            candidate = _safe_op(a, b, op_sym)
            if candidate is None:
                continue
            ratio = _match_ratio(candidate, target)
            if ratio >= _MATCH_THRESHOLD and ratio > best_conf:
                best_conf = ratio
                formula = f"{col_a} {op_sym} {col_b}"
                best = {
                    "column":    new_col,
                    "operation": formula,
                    "op_type":   op_type,
                    "confidence": ratio,
                }

    return best


def _detect_percentage_new(
    new_col: str,
    new_series: pd.Series,
    num_input_cols: Dict[str, pd.Series],
) -> Optional[Dict[str, Any]]:
    """Detect col / total * 100 → percentage column."""
    if not _is_numeric(new_series):
        return None

    target = new_series.reset_index(drop=True)
    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0

    for col_a, a in num_input_cols.items():
        a_r = a.reset_index(drop=True)
        # col_a / sum(col_a) * 100
        total = a_r.sum()
        if total == 0:
            continue
        candidate = (a_r / total) * 100
        ratio = _match_ratio(candidate, target, tol=0.01)
        if ratio >= _MATCH_THRESHOLD and ratio > best_conf:
            best_conf = ratio
            best = {
                "column":    new_col,
                "operation": f"{col_a} / SUM({col_a}) * 100",
                "op_type":   "percentage",
                "confidence": ratio,
            }

        # col_a * 100  (simple scale by 100)
        candidate2 = a_r * 100
        ratio2 = _match_ratio(candidate2, target)
        if ratio2 >= _MATCH_THRESHOLD and ratio2 > best_conf:
            best_conf = ratio2
            best = {
                "column":    new_col,
                "operation": f"{col_a} * 100",
                "op_type":   "percentage",
                "confidence": ratio2,
            }

    return best


# ─────────────────────────────────────────────────────────────────────────────
# In-place column detectors  (same column, values changed)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_rounding_inplace(
    col: str,
    inp: pd.Series,
    out: pd.Series,
) -> Optional[Dict[str, Any]]:
    """Detect ROUND / CEILING / FLOOR / TRUNC on an existing column."""
    if not (_is_numeric(inp) and _is_numeric(out)):
        return None
    a = inp.reset_index(drop=True)
    b = out.reset_index(drop=True)

    # ROUND(col, n)
    for n in _ROUND_DECIMALS:
        ratio = _match_ratio(a.round(n), b)
        if ratio >= _MATCH_THRESHOLD:
            op = f"ROUND({col}, {n})" if n > 0 else f"ROUND({col})"
            return {"column": col, "operation": op,
                    "op_type": "rounding", "confidence": ratio}

    # FLOOR / CEILING (to integer)
    ratio_floor = _match_ratio(np.floor(a).astype(float), b.astype(float))
    if ratio_floor >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"FLOOR({col})",
                "op_type": "rounding", "confidence": ratio_floor}

    ratio_ceil = _match_ratio(np.ceil(a).astype(float), b.astype(float))
    if ratio_ceil >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"CEILING({col})",
                "op_type": "rounding", "confidence": ratio_ceil}

    # TRUNC
    ratio_trunc = _match_ratio(np.trunc(a).astype(float), b.astype(float))
    if ratio_trunc >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"TRUNC({col})",
                "op_type": "rounding", "confidence": ratio_trunc}

    return None


def _detect_absolute_inplace(
    col: str,
    inp: pd.Series,
    out: pd.Series,
) -> Optional[Dict[str, Any]]:
    """Detect ABS(col)."""
    if not (_is_numeric(inp) and _is_numeric(out)):
        return None
    a = inp.reset_index(drop=True)
    b = out.reset_index(drop=True)
    ratio = _match_ratio(a.abs(), b)
    if ratio >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"ABS({col})",
                "op_type": "absolute", "confidence": ratio}
    return None


def _detect_percentage_scale_inplace(
    col: str,
    inp: pd.Series,
    out: pd.Series,
) -> Optional[Dict[str, Any]]:
    """Detect * 100 or / 100 scale (decimal to percent or vice versa)."""
    if not (_is_numeric(inp) and _is_numeric(out)):
        return None
    a = inp.reset_index(drop=True)
    b = out.reset_index(drop=True)

    ratio_x100 = _match_ratio(a * 100, b)
    if ratio_x100 >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"{col} * 100",
                "op_type": "percentage", "confidence": ratio_x100}

    ratio_d100 = _match_ratio(a / 100, b)
    if ratio_d100 >= _MATCH_THRESHOLD:
        return {"column": col, "operation": f"{col} / 100",
                "op_type": "percentage", "confidence": ratio_d100}

    return None


def _detect_power_inplace(
    col: str,
    inp: pd.Series,
    out: pd.Series,
) -> Optional[Dict[str, Any]]:
    """Detect POWER(col, n) for n in {2, 3, 0.5}."""
    if not (_is_numeric(inp) and _is_numeric(out)):
        return None
    a = inp.reset_index(drop=True)
    b = out.reset_index(drop=True)

    for n, label in [(2, "2"), (3, "3"), (0.5, "0.5")]:
        try:
            candidate = a.pow(n)
            ratio = _match_ratio(candidate, b)
            if ratio >= _MATCH_THRESHOLD:
                return {"column": col, "operation": f"POWER({col}, {label})",
                        "op_type": "power", "confidence": ratio}
        except Exception:
            continue
    return None


def _detect_cumulative_inplace(
    col: str,
    inp: pd.Series,
    out: pd.Series,
) -> Optional[Dict[str, Any]]:
    """Detect CUMSUM, CUMMAX, CUMMIN."""
    if not (_is_numeric(inp) and _is_numeric(out)):
        return None
    a = inp.reset_index(drop=True)
    b = out.reset_index(drop=True)

    for op_name, candidate_fn in [
        ("cumsum", lambda s: s.cumsum()),
        ("cummax", lambda s: s.cummax()),
        ("cummin", lambda s: s.cummin()),
    ]:
        try:
            ratio = _match_ratio(candidate_fn(a), b)
            if ratio >= _MATCH_THRESHOLD:
                return {"column": col, "operation": f"{op_name.upper()}({col})",
                        "op_type": "cumulative", "confidence": ratio}
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

# def build_math_operations_config(
#     input_df: pd.DataFrame,
#     output_df: pd.DataFrame,
#     column_mapping: Optional[Dict[str, str]] = None,
# ) -> Dict[str, Any]:
#     """
#     Detect numeric / math operations.

#     Args:
#         input_df:       Input DataFrame.
#         output_df:      Output DataFrame.
#         column_mapping: Optional {input_col: output_col} name map.

#     Returns:
#         {"applied": bool, "details": {"rules": [...]}}
#     """
#     empty = {"applied": False, "details": {"rules": []}}

#     if input_df.empty or output_df.empty:
#         return empty

#     inv_map: Dict[str, str] = {}
#     if column_mapping:
#         inv_map = {v: k for k, v in column_mapping.items()}
#     out_df = output_df.rename(columns=inv_map) if inv_map else output_df

#     num_input_cols: Dict[str, pd.Series] = {
#         c: input_df[c].reset_index(drop=True)
#         for c in input_df.columns if _is_numeric(input_df[c])
#     }

#     rules: List[Dict[str, Any]] = []

#     # ── New columns derived by math ops ──────────────────────────────────────
#     new_cols = [c for c in out_df.columns if c not in set(input_df.columns)]
#     for col in new_cols:
#         if col not in out_df.columns or not _is_numeric(out_df[col]):
#             continue
#         new_series = out_df[col].reset_index(drop=True)

#         result = (
#             _detect_arithmetic_new(col, new_series, num_input_cols)
#             or _detect_percentage_new(col, new_series, num_input_cols)
#         )

#         if result:
#             out_col = column_mapping.get(col, col) if column_mapping else col
#             result["column"] = out_col
#             rules.append(result)

#     # ── In-place numeric transforms on existing columns ───────────────────────
#     common_num_cols = [
#         c for c in input_df.columns
#         if c in out_df.columns and _is_numeric(input_df[c])
#     ]
#     for col in common_num_cols:
#         inp = input_df[col].reset_index(drop=True)
#         out = out_df[col].reset_index(drop=True)

#         # Skip if values are identical
#         if _match_ratio(inp, out, tol=_FLOAT_TOL) >= 0.999:
#             continue

#         for detector in [
#             _detect_rounding_inplace,
#             _detect_absolute_inplace,
#             _detect_percentage_scale_inplace,
#             _detect_power_inplace,
#             _detect_cumulative_inplace,
#         ]:
#             result = detector(col, inp, out)
#             if result:
#                 out_col = column_mapping.get(col, col) if column_mapping else col
#                 result["column"] = out_col
#                 rules.append(result)
#                 break

#     return {"applied": len(rules) > 0, "details": {"rules": rules}}

def build_math_operations_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    skip_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Detect numeric / math operations.

    This module focuses on IN-PLACE transforms applied to EXISTING columns:
      - Rounding (ROUND, FLOOR, CEILING, TRUNC)
      - Absolute value (ABS)
      - Percentage scaling (* 100, / 100)
      - Power operations (POWER)
      - Cumulative operations (CUMSUM, CUMMAX, CUMMIN)

    NEW column detection (arithmetic formulas like A * B, A + B) is handled
    by calculated_columns module to avoid duplication. Use skip_columns to
    exclude columns already detected by calculated_columns.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame.
        column_mapping: Optional { output_col: input_col } name map.
                        If input_col is a sentinel (derived/multisource/unknown),
                        the output column is treated as a math-derived candidate.
        skip_columns:   Optional list of column names to skip (typically columns
                        already detected by calculated_columns).

    Returns:
        {"applied": bool, "details": {"rules": [...]}}
    """
    empty = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty
 
    cm = column_mapping or {}

    # ── Split into real renames vs sentinel-flagged output cols ──────────────
    # column_mapping = { output_col: input_col }
    # real_rename_map : { output_col: input_col } — rename output→input namespace
    # sentinel_output_cols : output cols whose input is derived/multisource/unknown
    real_rename_map: Dict[str, str] = {}
    sentinel_output_cols: List[str] = []

    for out_col, in_col in cm.items():
        if _is_derived_sentinel(str(in_col)):
            sentinel_output_cols.append(out_col)
        else:
            real_rename_map[out_col] = in_col

    # Columns to skip (already detected by calculated_columns)
    skip_set = set(skip_columns or [])
    print(f"[math_operations] Real renames  : {real_rename_map}")
    print(f"[math_operations] Sentinel cols : {sentinel_output_cols}")
    print(f"[math_operations] Skip columns  : {skip_set}")

    # ── Rename output_df to input namespace for comparison ───────────────────
    # e.g. output "SID" → renamed to "SaleID" → matches input "SaleID" → not new
    out_df = output_df.rename(columns=real_rename_map) if real_rename_map else output_df.copy()
    # Drop duplicates: multiple output cols can map to the same input col name.
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]

    rules: List[Dict[str, Any]] = []

    # ══════════════════════════════════════════════════════════════════════════
    # NOTE: New column detection (arithmetic formulas) is DISABLED here.
    # All new derived columns should be detected by calculated_columns module,
    # which supports multi-pass detection for dependent formulas.
    #
    # This module focuses ONLY on in-place transforms on existing columns.
    # ══════════════════════════════════════════════════════════════════════════

    # ── In-place numeric transforms on existing columns ───────────────────────
    # Only check in-place transforms when row counts match (same rows in both)
    # Use real_rename_map to find input col name for each output col
    # common_num_cols: output cols (in renamed namespace) that exist in input
    if len(input_df) == len(out_df):
        common_num_cols = [
            c for c in input_df.columns
            if c in out_df.columns and _is_numeric(input_df[c])
        ]

        for col in common_num_cols:
            inp = input_df[col].reset_index(drop=True)
            out = out_df[col].reset_index(drop=True)

            # Skip if values are identical
            if _match_ratio(inp, out, tol=_FLOAT_TOL) >= 0.999:
                continue

            for detector in [
                _detect_rounding_inplace,
                _detect_absolute_inplace,
                _detect_percentage_scale_inplace,
                _detect_power_inplace,
                _detect_cumulative_inplace,
            ]:
                result = detector(col, inp, out)
                if result:
                    # Translate back to output col name using real_rename_map
                    # real_rename_map is { output_col: input_col }
                    # We need reverse: { input_col: output_col }
                    input_to_output = {v: k for k, v in real_rename_map.items()}
                    out_col_name = input_to_output.get(col, col)
                    result["column"] = out_col_name
                    rules.append(result)
                    break

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
