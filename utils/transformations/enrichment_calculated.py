# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived/calculated column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Calculated Columns Detection
=====================================================
Detects ALL new columns whose values can be explained by a formula applied
to existing input columns.

Formula families detected
--------------------------
  arithmetic   : col_a ± col_b,  col_a * col_b,  col_a / col_b
  ratio        : col / sum_of_cols,  col_a / col_b  (0–1 or 0–100)
  concatenation: str_a + sep + str_b  (e.g. first_name + " " + last_name)
  boolean flag : col > threshold, col == value, col.isin([...])
  derived cat  : pd.cut / pd.qcut patterns (binned numeric → labeled bucket)

Detection strategy
------------------
1.  Identify truly new columns (present in output but not input).
2.  For each new numeric column, try every ordered pair of input numeric
    columns with *, +, -, / operators and verify row-by-row (≥80 % match).
3.  For string columns, try concatenation with common separators.
4.  For boolean/flag columns check simple threshold or membership tests.
5.  For low-cardinality string columns derived from a numeric column,
    attempt binning detection.
6.  When no formula is confirmed, record the column with formula="unknown"
    so it is still surfaced in the config for manual review.

Edge cases handled
------------------
- Division by zero → rows with zero denominator skipped in verification.
- NaN / None values in either operand → skipped during verification.
- Excel stores integers as float (e.g. 1.0) → comparison uses tolerance.
- column_mapping forwarded to resolve renamed columns.

Returns
-------
{
  "applied": bool,
  "details": {
    "rules": [
      {"column": str, "formula": str, "formula_type": str, "confidence": float}
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

_MATCH_THRESHOLD  = 0.80   # fraction of rows that must match to confirm formula
_FLOAT_TOL        = 0.5    # absolute tolerance for float comparison (handles int rounding)
_REL_TOL          = 1e-6   # relative tolerance for large numbers
_ZERO_TOL         = 1e-6   # tolerance for detecting zero values
_MAX_PAIRS        = 50     # cap on column-pair attempts to avoid O(n²) blow-up
_CONCAT_SEPS      = [" ", "_", "-", ",", "|", "/", "", " - ", " / "]
_ARITH_OPS: List[Tuple[str, str]] = [
    ("*", "multiply"),
    ("+", "add"),
    ("-", "subtract"),
    ("/", "divide"),
]
# Conditional arithmetic patterns (for IF-based formulas)
_CONDITIONAL_PERCENTAGE_RATES = [0.10, 0.05, 0.15, 0.20, 0.25, 0.08, 0.12]  # common discount/tax rates
_CONDITIONAL_THRESHOLDS_TO_TRY = 5  # how many threshold candidates to try

# Sentinel values in column_mapping that indicate a derived/calculated column
# (set by the analysis phase when no direct input→output mapping was found)
_DERIVED_SENTINELS = {"derived column", "multisource column", "unknown mapping type"}

def _is_derived_sentinel(value: str) -> bool:
    """Return True if the mapping value is a sentinel for a calculated column."""
    if not isinstance(value, str):
        return False
    return any(kw in value.lower() for kw in ("derived", "multisource", "unknown"))

# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", s.lower())


def _match_ratio(a: pd.Series, b: pd.Series) -> float:
    """Fraction of rows where a ≈ b (NaN rows excluded from denominator).
    
    Uses both absolute tolerance (_FLOAT_TOL) and relative tolerance (_REL_TOL)
    to handle both small and large numeric values correctly.
    Falls back to string comparison for non-numeric series.
    """
    valid = a.notna() & b.notna()
    if valid.sum() == 0:
        return 0.0
    a_valid = a[valid]
    b_valid = b[valid]

    # Attempt numeric comparison only when BOTH sides are convertible to float
    a_num = pd.to_numeric(a_valid, errors="coerce")
    b_num = pd.to_numeric(b_valid, errors="coerce")
    both_numeric = a_num.notna().all() and b_num.notna().all()

    if both_numeric:
        diff = (a_num - b_num).abs()
        max_val = np.maximum(a_num.abs(), b_num.abs()).replace(0, 1)
        matches = (diff <= _FLOAT_TOL) | (diff / max_val <= _REL_TOL)
        return float(matches.mean())
    else:
        return float((a_valid.astype(str) == b_valid.astype(str)).mean())


def _try_arithmetic(
    new_series: pd.Series,
    col_a: pd.Series,
    col_b: pd.Series,
    op: str,
) -> float:
    """Return match ratio for new_series ≈ col_a <op> col_b."""
    try:
        if op == "*":
            candidate = col_a * col_b
        elif op == "+":
            candidate = col_a + col_b
        elif op == "-":
            candidate = col_a - col_b
        elif op == "/":
            denom = col_b.replace(0, np.nan)
            candidate = col_a / denom
        else:
            return 0.0
        return _match_ratio(new_series, candidate)
    except Exception:
        return 0.0


def _try_concat(
    new_series: pd.Series,
    col_a: pd.Series,
    col_b: pd.Series,
    sep: str,
) -> float:
    """Return fraction of rows where str(a) + sep + str(b) == new_series."""
    try:
        a_str = col_a.fillna("").astype(str).str.strip()
        b_str = col_b.fillna("").astype(str).str.strip()
        candidate = a_str + sep + b_str
        new_str = new_series.fillna("").astype(str).str.strip()
        valid = new_str != ""
        if valid.sum() == 0:
            return 0.0
        return float((candidate[valid] == new_str[valid]).mean())
    except Exception:
        return 0.0
 

def _try_boolean_flag(
    new_series: pd.Series,
    source_col: pd.Series,
) -> Tuple[float, str]:
    """
    Try common boolean / flag patterns.  Returns (confidence, formula_str).
    """
    try:
        # Pattern: new_series is True/False or 0/1 or Yes/No
        uniq = new_series.dropna().unique()
        if len(uniq) != 2:
            return 0.0, ""

        # Numeric threshold: col > median / mean
        if pd.api.types.is_numeric_dtype(source_col):
            threshold = source_col.median()
            for op_str, op_fn in [
                (f"> {threshold}", lambda s: s > threshold),
                (f">= {threshold}", lambda s: s >= threshold),
                (f"< {threshold}", lambda s: s < threshold),
            ]:
                mask = op_fn(source_col)
                # Align with new_series by truthy matching
                if pd.api.types.is_bool_dtype(new_series):
                    ratio = float((mask == new_series).mean())
                else:
                    ratio = float(
                        (mask.astype(str).str.lower() ==
                         new_series.astype(str).str.lower()).mean()
                    )
                if ratio >= _MATCH_THRESHOLD:
                    return ratio, op_str

        # Membership / equality flag
        for val in uniq:
            eq_mask = source_col == val
            ratio = float((eq_mask == new_series.astype(bool)).mean())
            if ratio >= _MATCH_THRESHOLD:
                return ratio, f"== {val}"

        return 0.0, ""
    except Exception:
        return 0.0, ""


def _try_binning(
    new_series: pd.Series,
    source_col: pd.Series,
    col_name: str,
) -> Tuple[float, str]:
    """
    Detect pd.cut / pd.qcut derived category columns.
    Returns (confidence, formula_str).
    """
    try:
        if not pd.api.types.is_numeric_dtype(source_col):
            return 0.0, ""
        n_unique = new_series.nunique(dropna=True)
        if n_unique < 2 or n_unique > 10:
            return 0.0, ""

        # Try equal-width bins
        for n_bins in range(2, min(n_unique + 1, 6)):
            try:
                binned = pd.cut(source_col, bins=n_bins, labels=False)
                ratio = float(
                    (binned.astype(str) ==
                     new_series.astype(str)).mean()
                )
                if ratio >= _MATCH_THRESHOLD:
                    return ratio, f"pd.cut({col_name}, bins={n_bins})"
            except Exception:
                pass

        return 0.0, ""
    except Exception:
        return 0.0, ""


def _try_conditional_numeric(
    new_series: pd.Series,
    source_col: pd.Series,
    source_col_name: str,
) -> Tuple[float, str, str]:
    """
    Detect conditional numeric formulas like:
      IF(source > threshold, source * rate, 0)
      IF(source > threshold, source * rate, source)
      
    Returns (confidence, formula_str, formula_type).
    
    Examples detected:
      Discount = IF(TotalAmount > 10000, TotalAmount * 0.10, 0)
    """
    try:
        if not pd.api.types.is_numeric_dtype(new_series):
            return 0.0, "", ""
        if not pd.api.types.is_numeric_dtype(source_col):
            return 0.0, "", ""
        
        # Filter out NaN values for analysis
        valid = new_series.notna() & source_col.notna()
        if valid.sum() < 3:
            return 0.0, "", ""
        
        new_valid = new_series[valid].reset_index(drop=True)
        src_valid = source_col[valid].reset_index(drop=True)
        
        # Pattern 1: IF(source > threshold, source * rate, 0)
        # The new column has some zeros and some values that are source * rate
        
        # Find rows where new_series is 0 vs non-zero
        zero_mask = (new_valid == 0) | (new_valid.abs() < _ZERO_TOL)
        nonzero_mask = ~zero_mask
        
        if zero_mask.sum() == 0 or nonzero_mask.sum() == 0:
            # All zeros or no zeros - not a conditional pattern
            return 0.0, "", ""
        
        # For non-zero rows, calculate the ratio new/source
        nonzero_src = src_valid[nonzero_mask]
        nonzero_new = new_valid[nonzero_mask]
        
        # Avoid division by zero
        valid_div = nonzero_src.abs() > _ZERO_TOL
        if valid_div.sum() == 0:
            return 0.0, "", ""
        
        ratios = nonzero_new[valid_div] / nonzero_src[valid_div]
        
        # Check if ratios are consistent (all approximately the same)
        if ratios.std() > 0.01:
            return 0.0, "", ""
        
        detected_rate = round(ratios.median(), 4)
        
        # Verify: source values where new=0 should be below threshold
        # Source values where new!=0 should be above threshold
        src_when_zero = src_valid[zero_mask]
        src_when_nonzero = src_valid[nonzero_mask]
        
        # Find the threshold boundary
        max_zero_src = src_when_zero.max()
        min_nonzero_src = src_when_nonzero.min()
        
        if min_nonzero_src <= max_zero_src:
            # Overlapping ranges - not a clean conditional
            return 0.0, "", ""
        
        # Choose a clean threshold value between the ranges
        threshold_candidates = []
        mid_threshold = (max_zero_src + min_nonzero_src) / 2
        
        # Try common round numbers near the midpoint
        for mult in [1000, 5000, 10000, 100, 500]:
            candidate = round(mid_threshold / mult) * mult
            if max_zero_src < candidate <= min_nonzero_src:
                threshold_candidates.append(candidate)
        
        # Also try the exact midpoint
        threshold_candidates.append(mid_threshold)
        
        # Pick the cleanest threshold
        threshold = min(threshold_candidates, key=lambda x: abs(x - mid_threshold)) if threshold_candidates else mid_threshold
        threshold = int(threshold) if threshold == int(threshold) else threshold
        
        # Verify the formula
        candidate_new = np.where(src_valid > threshold, src_valid * detected_rate, 0)
        match_ratio = _match_ratio(pd.Series(candidate_new), new_valid)
        
        if match_ratio >= _MATCH_THRESHOLD:
            # Format the rate nicely
            if abs(detected_rate - round(detected_rate * 100) / 100) < 0.001:
                rate_str = f"{detected_rate:.2f}"
            else:
                rate_str = f"{detected_rate}"
            
            formula = f"IF({source_col_name} > {threshold}, {source_col_name} * {rate_str}, 0)"
            return match_ratio, formula, "conditional_arithmetic"
        
        return 0.0, "", ""
        
    except Exception as e:
        return 0.0, "", ""


# ─────────────────────────────────────────────────────────────────────────────
# Per-column formula inference
# ─────────────────────────────────────────────────────────────────────────────

def _infer_formula(
    new_col: str,
    new_series: pd.Series,
    input_df: pd.DataFrame,
    known_derived_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Try all formula families for *new_series* against *input_df* columns
    and optionally against *known_derived_df* columns (already-computed derived columns).
    
    This enables multi-pass detection where:
      Pass 1: TotalAmount = Quantity * Price (input cols only)
      Pass 2: Discount = IF(TotalAmount > 10000, TotalAmount * 0.10, 0) (uses derived col)
      Pass 3: NetAmount = TotalAmount - Discount (uses two derived cols)
    
    Returns the best-matching rule dict.
    """
    is_numeric_new = (
        pd.api.types.is_numeric_dtype(new_series)
        and not pd.api.types.is_bool_dtype(new_series)
    )
    is_string_new  = (
        pd.api.types.is_object_dtype(new_series)
        or pd.api.types.is_string_dtype(new_series)
    )
    is_bool_new    = (
        pd.api.types.is_bool_dtype(new_series)
        or new_series.nunique(dropna=True) == 2
    )

    # Build combined DataFrame for formula search (input + known derived)
    combined_df = input_df.copy()
    if known_derived_df is not None and not known_derived_df.empty:
        for col in known_derived_df.columns:
            if col not in combined_df.columns:
                combined_df[col] = known_derived_df[col].values

    num_cols = [
        c for c in combined_df.columns
        if pd.api.types.is_numeric_dtype(combined_df[c])
        and not pd.api.types.is_bool_dtype(combined_df[c])
    ]
    str_cols = [
        c for c in combined_df.columns
        if pd.api.types.is_object_dtype(combined_df[c])
        or pd.api.types.is_string_dtype(combined_df[c])
    ]
    
    # Track which columns are derived (for formula annotation)
    derived_col_names = set(known_derived_df.columns) if known_derived_df is not None else set()

    best_rule: Dict[str, Any] = {
        "column":       new_col,
        "formula":      "unknown",
        "formula_type": "unknown",
        "confidence":   0.0,
    }

    # ── Arithmetic (numeric new column) ──────────────────────────────────────
    if is_numeric_new and len(num_cols) >= 2:
        pairs = list(permutations(num_cols, 2))[:_MAX_PAIRS]
        for a, b in pairs:
            for op_sym, op_name in _ARITH_OPS:
                ratio = _try_arithmetic(new_series, combined_df[a], combined_df[b], op_sym)
                if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                    formula = f"{a} {op_sym} {b}"
                    best_rule = {
                        "column":       new_col,
                        "formula":      formula,
                        "formula_type": op_name,
                        "confidence":   ratio,
                        "depends_on_derived": bool(a in derived_col_names or b in derived_col_names),
                    }

    # ── Ratio against single column (percentage, e.g. col / total_col) ───────
    if is_numeric_new and len(num_cols) >= 1:
        for a in num_cols:
            for b in num_cols:
                if a == b:
                    continue
                ratio = _try_arithmetic(new_series, combined_df[a], combined_df[b], "/")
                if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                    best_rule = {
                        "column":       new_col,
                        "formula":      f"{a} / {b}",
                        "formula_type": "ratio",
                        "confidence":   ratio,
                        "depends_on_derived": bool(a in derived_col_names or b in derived_col_names),
                    }

    # ── Conditional numeric formula (IF statements) ──────────────────────────
    # Try this on numeric columns including derived ones
    if is_numeric_new:
        for src in num_cols:
            conf, cond_formula, cond_type = _try_conditional_numeric(
                new_series, combined_df[src], src
            )
            if conf >= _MATCH_THRESHOLD and conf > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      cond_formula,
                    "formula_type": cond_type,
                    "confidence":   conf,
                    "depends_on_derived": bool(src in derived_col_names),
                }

    # ── Concatenation (string new column) ────────────────────────────────────
    if is_string_new and len(str_cols) >= 2:
        pairs = list(permutations(str_cols, 2))[:_MAX_PAIRS]
        for a, b in pairs:
            for sep in _CONCAT_SEPS:
                ratio = _try_concat(new_series, combined_df[a], combined_df[b], sep)
                if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                    sep_repr = f'"{sep}"' if sep else '""'
                    best_rule = {
                        "column":       new_col,
                        "formula":      f"CONCAT({a}, {sep_repr}, {b})",
                        "formula_type": "concatenation",
                        "confidence":   ratio,
                    }

    # ── Boolean / flag columns ────────────────────────────────────────────────
    if is_bool_new:
        for src in combined_df.columns:
            conf, flag_formula = _try_boolean_flag(new_series, combined_df[src])
            if conf >= _MATCH_THRESHOLD and conf > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      f"{src} {flag_formula}",
                    "formula_type": "boolean_flag",
                    "confidence":   conf,
                }

    # ── Binning / derived category ────────────────────────────────────────────
    if is_string_new or (new_series.nunique(dropna=True) <= 10):
        for src in num_cols:
            conf, bin_formula = _try_binning(new_series, combined_df[src], src)
            if conf >= _MATCH_THRESHOLD and conf > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      bin_formula,
                    "formula_type": "derived_category",
                    "confidence":   conf,
                }

# ── Text operations (string new column) ───────────────────────────────────
    if is_string_new:
        for src in str_cols:
            # Try UPPER
            candidate = combined_df[src].str.upper()
            ratio = _match_ratio(new_series, candidate)
            if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      f"UPPER({src})",
                    "formula_type": "text_operation",
                    "confidence":   ratio,
                }
            # Try LOWER
            candidate = combined_df[src].str.lower()
            ratio = _match_ratio(new_series, candidate)
            if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      f"LOWER({src})",
                    "formula_type": "text_operation",
                    "confidence":   ratio,
                }
            # Try PROPER (title case)
            candidate = combined_df[src].str.title()
            ratio = _match_ratio(new_series, candidate)
            if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      f"PROPER({src})",
                    "formula_type": "text_operation",
                    "confidence":   ratio,
                }
            # Try TRIM
            candidate = combined_df[src].str.strip()
            ratio = _match_ratio(new_series, candidate)
            if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                best_rule = {
                    "column":       new_col,
                    "formula":      f"TRIM({src})",
                    "formula_type": "text_operation",
                    "confidence":   ratio,
                }
            # Try LEFT (first 4 chars, as example)
            for length in [4, 5, 3]:
                candidate = combined_df[src].str[:length]
                ratio = _match_ratio(new_series, candidate)
                if ratio >= _MATCH_THRESHOLD and ratio > best_rule["confidence"]:
                    best_rule = {
                        "column":       new_col,
                        "formula":      f"LEFT({src}, {length})",
                        "formula_type": "text_operation",
                        "confidence":   ratio,
                    }

    return best_rule


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

# def build_calculated_columns_config(
#     input_df: pd.DataFrame,
#     output_df: pd.DataFrame,
#     column_mapping: Optional[Dict[str, str]] = None,
# ) -> Dict[str, Any]:
#     """
#     Detect all calculated / derived columns.

#     Args:
#         input_df:       Input DataFrame.
#         output_df:      Output DataFrame.
#         column_mapping: Optional {input_col: output_col} name map.

#     Returns:
#         {
#             "applied": bool,
#             "details": {
#                 "rules": [
#                     {
#                         "column":       str,
#                         "formula":      str,   # e.g. "price * quantity"
#                         "formula_type": str,   # multiply | add | ratio | concatenation | boolean_flag | derived_category | unknown
#                         "confidence":   float
#                     }
#                 ]
#             }
#         }
#     """
#     empty = {"applied": False, "details": {"rules": []}}

#     if input_df.empty or output_df.empty:
#         return empty

#     # Remap output to input namespace — build inv_map ONCE (was incorrectly inside a loop)
#     cm = column_mapping or {}
#     inv_map: Dict[str, str] = {v: k for k, v in cm.items()}
#     out_df = output_df.rename(columns=inv_map) if inv_map else output_df

#     new_cols = [c for c in out_df.columns if c not in set(input_df.columns)]

#     if not new_cols:
#         return empty

#     rules: List[Dict[str, Any]] = []
#     for col in new_cols:
#         if col not in out_df.columns:
#             continue
#         rule = _infer_formula(col, out_df[col].reset_index(drop=True),
#                               input_df.reset_index(drop=True))
#         # Translate back to output namespace
#         out_col = cm.get(col, col)
#         rule["column"] = out_col
#         rules.append(rule)

#     return {"applied": len(rules) > 0, "details": {"rules": rules}}


def _compute_derived_value(
    formula: str,
    formula_type: str,
    input_df: pd.DataFrame,
    known_derived_df: pd.DataFrame,
) -> Optional[pd.Series]:
    """
    Compute the values of a derived column based on its detected formula.
    Used for multi-pass detection where later columns depend on earlier ones.
    """
    try:
        # Build combined source DataFrame
        combined_df = input_df.copy()
        if known_derived_df is not None and not known_derived_df.empty:
            for col in known_derived_df.columns:
                if col not in combined_df.columns:
                    combined_df[col] = known_derived_df[col].values
        
        # Handle arithmetic formulas: "col_a * col_b", "col_a - col_b", etc.
        if formula_type in ("multiply", "add", "subtract", "divide", "ratio"):
            for op_sym in ["*", "+", "-", "/"]:
                if f" {op_sym} " in formula:
                    parts = formula.split(f" {op_sym} ")
                    if len(parts) == 2:
                        a, b = parts[0].strip(), parts[1].strip()
                        if a in combined_df.columns and b in combined_df.columns:
                            col_a = combined_df[a]
                            col_b = combined_df[b]
                            if op_sym == "*":
                                return col_a * col_b
                            elif op_sym == "+":
                                return col_a + col_b
                            elif op_sym == "-":
                                return col_a - col_b
                            elif op_sym == "/":
                                return col_a / col_b.replace(0, np.nan)
        
        # Handle conditional arithmetic: "IF(TotalAmount > 10000, TotalAmount * 0.10, 0)"
        if formula_type == "conditional_arithmetic":
            import re
            match = re.match(
                r"IF\((\w+)\s*>\s*([\d.]+),\s*(\w+)\s*\*\s*([\d.]+),\s*([\d.]+)\)",
                formula
            )
            if match:
                cond_col = match.group(1)
                threshold = float(match.group(2))
                mult_col = match.group(3)
                rate = float(match.group(4))
                else_val = float(match.group(5))
                
                if cond_col in combined_df.columns and mult_col in combined_df.columns:
                    result = np.where(
                        combined_df[cond_col] > threshold,
                        combined_df[mult_col] * rate,
                        else_val
                    )
                    return pd.Series(result, index=combined_df.index)
        
        return None
    except Exception as e:
        print(f"[calculated_columns] ⚠️ Failed to compute derived value for formula '{formula}': {e}")
        return None


def build_calculated_columns_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect all calculated / derived columns using MULTI-PASS detection.

    Multi-pass detection enables detection of derived columns that depend on
    other derived columns:
    
      Pass 1: TotalAmount = Quantity * Price       (uses input cols)
      Pass 2: Discount = IF(TotalAmount > 10000, TotalAmount * 0.10, 0)  (uses derived col)
      Pass 3: NetAmount = TotalAmount - Discount   (uses two derived cols)

    column_mapping format: { output_col_name: input_col_name }
    
    If input_col_name is a sentinel like "DERIVED COLUMN", "MULTISOURCE COLUMN",
    or "UNKNOWN MAPPING TYPE", the output column has no direct input source
    and formula inference is attempted against all input columns.
    """
    empty = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty

    # cm = { output_col: input_col }
    cm = column_mapping or {}

    # ── Split mappings into real renames vs sentinel (derived) cols ──────────
    real_rename_map: Dict[str, str] = {}
    sentinel_output_cols: List[str] = []

    for out_col, in_col in cm.items():
        if _is_derived_sentinel(str(in_col)):
            sentinel_output_cols.append(out_col)
        else:
            real_rename_map[out_col] = in_col

    print(f"[calculated_columns] Real renames   : {real_rename_map}")
    print(f"[calculated_columns] Sentinel cols  : {sentinel_output_cols}")

    # ── Rename output_df columns to input namespace for comparison ───────────
    out_df = output_df.rename(columns=real_rename_map)
    # Drop duplicates: multiple output cols can map to the same input col name
    # (e.g. Full_Name + Reconstructed_Full_Name → Full_Name). Keep first occurrence.
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]

    # ── Find candidate columns (structurally new + sentinel) ─────────────────
    input_col_set = set(input_df.columns)
    structurally_new = [c for c in out_df.columns if c not in input_col_set]
    extra_sentinel = [
        out_col for out_col in sentinel_output_cols
        if out_col not in structurally_new and out_col in output_df.columns
    ]
    candidate_cols = list(dict.fromkeys(structurally_new + extra_sentinel))

    if not candidate_cols:
        print("[calculated_columns] No candidate columns found.")
        return empty

    print(f"[calculated_columns] Candidates     : {candidate_cols}")

    # ══════════════════════════════════════════════════════════════════════════
    # MULTI-PASS DETECTION
    # ══════════════════════════════════════════════════════════════════════════
    MAX_PASSES = 5
    rules: List[Dict[str, Any]] = []
    
    # Build formula_search_df from out_df but EXCLUDE candidate columns
    # This ensures we only use INPUT columns (renamed to input namespace) for
    # formula detection. Derived columns are added to known_derived_df as they
    # are detected in each pass.
    #
    # Example: If candidates = [TotalAmount, Discount, NetAmount]
    #   Pass 1: formula_search_df = [Quantity, UnitPrice, ...] (input cols only)
    #           → detects TotalAmount = Quantity * UnitPrice
    #   Pass 2: formula_search_df + known_derived_df = [Quantity, UnitPrice, TotalAmount]
    #           → detects Discount = IF(TotalAmount > 10000, TotalAmount * 0.10, 0)
    #   Pass 3: formula_search_df + known_derived_df = [..., TotalAmount, Discount]
    #           → detects NetAmount = TotalAmount - Discount
    #
    candidate_set = set(candidate_cols)
    formula_search_cols = [c for c in out_df.columns if c not in candidate_set]
    formula_search_df = out_df[formula_search_cols].reset_index(drop=True)
    print(f"[calculated_columns] Formula search cols (excl candidates): {formula_search_cols}")
    
    known_derived_df = pd.DataFrame(index=formula_search_df.index)  # stores computed derived cols
    remaining_cols = candidate_cols.copy()
    
    for pass_num in range(1, MAX_PASSES + 1):
        print(f"[calculated_columns] ── Pass {pass_num}: {len(remaining_cols)} columns to detect ──")
        
        newly_detected = []
        still_unknown = []
        
        for col in remaining_cols:
            # Get the output series
            if col in out_df.columns:
                series = out_df[col].reset_index(drop=True)
            elif col in output_df.columns:
                series = output_df[col].reset_index(drop=True)
            else:
                print(f"[calculated_columns] ⚠️ Column '{col}' not found, skipping.")
                continue
            
            # Run inference with known derived columns
            # Use formula_search_df (renamed output) for row-aligned formula search
            rule = _infer_formula(
                col,
                series,
                formula_search_df,
                known_derived_df.reset_index(drop=True) if not known_derived_df.empty else None,
            )
            rule["column"] = col
            
            if rule["formula"] != "unknown" and rule["confidence"] >= _MATCH_THRESHOLD:
                # Successfully detected!
                newly_detected.append((col, rule))
                print(f"[calculated_columns]   ✓ {col} = {rule['formula']} (conf: {rule['confidence']:.2f})")
            else:
                still_unknown.append(col)
                print(f"[calculated_columns]   ? {col} = unknown")
        
        # Add newly detected rules and compute their values for next pass
        for col, rule in newly_detected:
            rule["execution_order"] = len(rules) + 1
            rule["detection_pass"] = pass_num
            rules.append(rule)
            
            # Compute the derived column value for use in subsequent passes
            # Use formula_search_df for row-aligned computation
            computed = _compute_derived_value(
                rule["formula"],
                rule["formula_type"],
                formula_search_df,
                known_derived_df.reset_index(drop=True) if not known_derived_df.empty else pd.DataFrame(),
            )
            if computed is not None:
                known_derived_df[col] = computed.values
                print(f"[calculated_columns]   📊 Computed intermediate column '{col}' for next pass")
            else:
                # Fallback: use actual output values for next pass
                # (this ensures multi-pass detection can continue even if computation fails)
                if col in out_df.columns:
                    known_derived_df[col] = out_df[col].reset_index(drop=True).values
                    print(f"[calculated_columns]   📊 Using actual output values for '{col}' in next pass")
        
        remaining_cols = still_unknown
        
        if not remaining_cols:
            print(f"[calculated_columns] ✅ All columns detected in {pass_num} passes!")
            break
        
        if not newly_detected:
            # No progress made this pass, add remaining as unknown
            print(f"[calculated_columns] ⚠️ No new detections in pass {pass_num}, stopping.")
            for col in remaining_cols:
                if col in out_df.columns:
                    series = out_df[col].reset_index(drop=True)
                elif col in output_df.columns:
                    series = output_df[col].reset_index(drop=True)
                else:
                    continue
                rules.append({
                    "column": col,
                    "formula": "unknown",
                    "formula_type": "unknown",
                    "confidence": 0.0,
                    "execution_order": len(rules) + 1,
                    "detection_pass": pass_num,
                })
            break
    
    # ── Final summary ─────────────────────────────────────────────────────────
    detected_count = sum(1 for r in rules if r["formula"] != "unknown")
    unknown_count = len(rules) - detected_count
    print(f"[calculated_columns] 📋 Summary: {detected_count} detected, {unknown_count} unknown")

    return {"applied": len(rules) > 0, "details": {"rules": rules}}