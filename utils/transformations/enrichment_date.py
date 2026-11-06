# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Derived column detector (tasks 7-9). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment: Date Operations Detection
================================================
Detects date-related derivations split into three sub-families:

  A. Extraction  – year, month, day, quarter, weekday, week-number extracted
                   from a date column into separate new columns.
  B. Arithmetic  – difference, age, duration, tenure between two date columns
                   or between a date column and a reference (today / fixed).
  C. Formatting  – TEXT(date, fmt): date converted to a specific string format.

Detection strategy
------------------
1.  Parse all columns as dates (errors='coerce') to discover date columns.
2.  For new numeric columns whose name contains a date-component keyword
    (year, month, day, quarter, week, weekday) verify they equal the
    corresponding pandas attribute on a source date column.
3.  For new numeric columns whose name contains age/duration/days/tenure
    verify they equal the difference (in days / months / years) between
    two date columns or between a date column and today.
4.  For new string columns, verify they match common strftime formats
    applied to a source date column.

Edge cases handled
------------------
- Mixed date formats in Excel (stored as strings or floats).
- NaT / NaN values in date parsing → skipped in verification.
- Leap years, month-end dates in age calculations.
- Duration expressed in days vs. months vs. years.
- column_mapping forwarded for renamed columns.
 
Returns
-------
{
  "applied": bool,
  "details": {
    "rules": [
      {
        "sub_type":  "extraction" | "arithmetic" | "formatting",
        "source":    str,         # source date column
        "target":    str,         # output column
        "extract":   str,         # year | month | day | quarter | week | weekday  (extraction only)
        "operation": str,         # e.g. "date_diff_days", "age_years"             (arithmetic only)
        "format":    str,         # strftime format string                          (formatting only)
        "confidence": float
      }
    ]
  }
}
"""

from __future__ import annotations

import re
from datetime import date as date_type
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MATCH_THRESHOLD = 0.80
_TODAY           = pd.Timestamp("today").normalize()

_EXTRACT_MAP = {
    "year":    lambda s: s.dt.year,
    "month":   lambda s: s.dt.month,
    "day":     lambda s: s.dt.day,
    "quarter": lambda s: s.dt.quarter,
    "week":    lambda s: s.dt.isocalendar().week.astype(int),
    "weekday": lambda s: s.dt.dayofweek,
    "hour":    lambda s: s.dt.hour,
    "minute":  lambda s: s.dt.minute,
}

_EXTRACT_KEYWORDS = list(_EXTRACT_MAP.keys())

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%Y/%m/%d", "%m-%d-%Y", "%Y%m%d",
]

_ARITH_KEYWORDS = ["age", "duration", "days", "tenure", "elapsed", "diff",
                   "period", "months", "years", "weeks"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", s.lower())


def _to_datetime(series: pd.Series) -> pd.Series:
    """Attempt to parse a series as datetime; return NaT for failures."""
    try:
        return pd.to_datetime(series, errors="coerce", infer_datetime_format=True)
    except Exception:
        return pd.Series(pd.NaT, index=series.index)


def _match_ratio_numeric(a: pd.Series, b: pd.Series) -> float:
    """Fraction of rows where a == b, ignoring NaN."""
    try:
        valid = a.notna() & b.notna()
        if valid.sum() == 0:
            return 0.0
        return float(((a[valid].astype(float) - b[valid].astype(float)).abs() < 0.5).mean())
    except Exception:
        return 0.0


def _match_ratio_str(a: pd.Series, b: pd.Series) -> float:
    a_s = a.fillna("").astype(str)
    b_s = b.fillna("").astype(str)
    valid = (a_s != "") & (b_s != "")
    if valid.sum() == 0:
        return 0.0
    return float((a_s[valid] == b_s[valid]).mean())


def _discover_date_columns(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Return {col_name: parsed_datetime_series} for all date-like columns."""
    result: Dict[str, pd.Series] = {}
    for col in df.columns:
        parsed = _to_datetime(df[col])
        if parsed.notna().sum() / max(len(df), 1) >= 0.5:
            result[col] = parsed
    return result


# ─────────────────────────────────────────────────────────────────────────────
# A. Extraction
# ─────────────────────────────────────────────────────────────────────────────

def _detect_extraction(
    new_col: str,
    new_series: pd.Series,
    date_cols: Dict[str, pd.Series],
) -> Optional[Dict[str, Any]]:
    """Detect date-component extraction into a new column."""
    if not pd.api.types.is_numeric_dtype(new_series):
        return None

    n = _norm(new_col)
    matched_component: Optional[str] = None
    for kw in _EXTRACT_KEYWORDS:
        if kw in n:
            matched_component = kw
            break

    if not matched_component:
        return None

    extractor = _EXTRACT_MAP[matched_component]
    best_conf = 0.0
    best_src  = ""

    for src_col, date_series in date_cols.items():
        try:
            extracted = extractor(date_series).reset_index(drop=True)
            target    = new_series.reset_index(drop=True)
            ratio = _match_ratio_numeric(extracted.astype(float), target.astype(float))
            if ratio > best_conf:
                best_conf = ratio
                best_src  = src_col
        except Exception:
            continue

    if best_conf >= _MATCH_THRESHOLD:
        return {
            "sub_type":  "extraction",
            "source":    best_src,
            "target":    new_col,
            "extract":   matched_component,
            "confidence": best_conf,
        }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# B. Arithmetic
# ─────────────────────────────────────────────────────────────────────────────

def _date_diff_days(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a - b).dt.days.astype(float)


def _date_diff_months(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a.dt.year - b.dt.year) * 12 + (a.dt.month - b.dt.month)).astype(float)


def _date_diff_years(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.dt.year - b.dt.year).astype(float)


def _detect_arithmetic(
    new_col: str,
    new_series: pd.Series,
    date_cols: Dict[str, pd.Series],
    input_df: pd.DataFrame,
) -> Optional[Dict[str, Any]]:
    """Detect date difference / age / duration calculations."""
    if not pd.api.types.is_numeric_dtype(new_series):
        return None

    n = _norm(new_col)
    has_arith_hint = any(kw in n for kw in _ARITH_KEYWORDS)

    date_col_names = list(date_cols.keys())
    target = new_series.reset_index(drop=True)

    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0

    ops = [
        ("date_diff_days",   _date_diff_days),
        ("date_diff_months", _date_diff_months),
        ("date_diff_years",  _date_diff_years),
    ]

    # ── Difference between two date columns ────────────────────────────────
    for i, col_a in enumerate(date_col_names):
        for col_b in date_col_names[i + 1:]:
            da = date_cols[col_a].reset_index(drop=True)
            db = date_cols[col_b].reset_index(drop=True)
            for op_name, op_fn in ops:
                for a, b, label_a, label_b in [(da, db, col_a, col_b),
                                                (db, da, col_b, col_a)]:
                    try:
                        candidate = op_fn(a, b)
                        ratio = _match_ratio_numeric(candidate, target)
                        if ratio >= _MATCH_THRESHOLD and ratio > best_conf:
                            best_conf = ratio
                            best = {
                                "sub_type":   "arithmetic",
                                "source":     label_a,
                                "source_b":   label_b,
                                "target":     new_col,
                                "operation":  op_name,
                                "confidence": ratio,
                            }
                    except Exception:
                        continue

    # ── Difference between a date column and today ─────────────────────────
    for col_a, da in date_cols.items():
        da_r = da.reset_index(drop=True)
        for op_name, op_fn in ops:
            try:
                today_series = pd.Series([_TODAY] * len(da_r), index=da_r.index)
                today_series = pd.to_datetime(today_series)
                candidate = op_fn(today_series, da_r)
                ratio = _match_ratio_numeric(candidate, target)
                if ratio >= _MATCH_THRESHOLD and ratio > best_conf:
                    best_conf = ratio
                    best = {
                        "sub_type":   "arithmetic",
                        "source":     col_a,
                        "source_b":   "TODAY()",
                        "target":     new_col,
                        "operation":  op_name,
                        "confidence": ratio,
                    }
            except Exception:
                continue

    return best


# ─────────────────────────────────────────────────────────────────────────────
# C. Formatting
# ─────────────────────────────────────────────────────────────────────────────

def _detect_formatting(
    new_col: str,
    new_series: pd.Series,
    date_cols: Dict[str, pd.Series],
) -> Optional[Dict[str, Any]]:
    """Detect TEXT(date, format) → string column."""
    if not (
        pd.api.types.is_object_dtype(new_series)
        or pd.api.types.is_string_dtype(new_series)
    ):
        return None

    target = new_series.fillna("").astype(str).reset_index(drop=True)

    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0

    for src_col, date_series in date_cols.items():
        ds = date_series.reset_index(drop=True)
        for fmt in _DATE_FORMATS:
            try:
                candidate = ds.dt.strftime(fmt).fillna("")
                ratio = _match_ratio_str(candidate, target)
                if ratio >= _MATCH_THRESHOLD and ratio > best_conf:
                    best_conf = ratio
                    best = {
                        "sub_type":   "formatting",
                        "source":     src_col,
                        "target":     new_col,
                        "format":     fmt,
                        "confidence": ratio,
                    }
            except Exception:
                continue

    return best


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_date_operations_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect date extraction, arithmetic, and formatting operations.

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
    # Drop duplicates: multiple output cols can map to the same input col name.
    out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    # Create inv_map for translating back to output namespace
    inv_map: Dict[str, str] = {v: k for k, v in real_rename_map.items()} if real_rename_map else {}

    # Discover date columns from renamed output (for row-aligned comparison)
    date_cols = _discover_date_columns(out_df)
    if not date_cols:
        return empty

    new_cols = [c for c in out_df.columns if c not in set(input_df.columns)]
    if not new_cols:
        return empty

    rules: List[Dict[str, Any]] = []

    for col in new_cols:
        if col not in out_df.columns:
            continue
        series = out_df[col].reset_index(drop=True)

        result = (
            _detect_extraction(col, series, date_cols)
            or _detect_arithmetic(col, series, date_cols, out_df)
            or _detect_formatting(col, series, date_cols)
        )

        if result:
            # Translate back to output namespace using inv_map
            out_col   = inv_map.get(col, col)
            out_src   = inv_map.get(result["source"], result["source"])
            result["target"] = out_col
            result["source"] = out_src
            if "source_b" in result and result["source_b"] != "TODAY()":
                result["source_b"] = inv_map.get(result["source_b"], result["source_b"])
            rules.append(result)

    return {"applied": len(rules) > 0, "details": {"rules": rules}}
