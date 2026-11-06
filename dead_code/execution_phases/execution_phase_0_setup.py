"""
execution/execution_phase_0_setup.py
=====================================
PHASE 0 — Setup
---------------
Executes BEFORE everything else.

Steps:
  1. Load Excel / CSV into a DataFrame
  2. Normalize column headers (strip whitespace, case-safe)
  3. Infer & cast basic dtypes (numeric, datetime, boolean)
  4. Handle basic missing values (drop fully-empty rows/cols)

Reads from : config["preprocessing"]
Produces   : a clean, typed pandas DataFrame ready for Phase 1
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    input_path: str,
    config: Dict[str, Any],
    sheet_name: str | int = 0,
) -> pd.DataFrame:
    """
    Load and set up the raw input file.

    Args:
        input_path : Absolute or relative path to the input Excel / CSV file.
        config     : Full merged config dict produced by phases/main.py.
        sheet_name : Sheet name or 0-based index (Excel only).

    Returns:
        A clean, typed DataFrame — the starting point for all execution phases.
    """
    pre = config.get("preprocessing", {})

    # ── 1. Load file ──────────────────────────────────────────────────────────
    df = _load_file(input_path, sheet_name)
    print(f"[Phase 0] Loaded '{input_path}'  →  {df.shape[0]} rows × {df.shape[1]} cols")

    # ── 2. Normalize column headers ───────────────────────────────────────────
    if pre.get("normalize_column_names", True):
        df = _normalize_headers(df, case_insensitive=pre.get("case_insensitive", True))
        print(f"[Phase 0] Column headers normalized.")

    # ── 3. Strip whitespace from string cells ─────────────────────────────────
    if pre.get("trim_whitespace", True):
        df = _trim_string_cells(df)
        print(f"[Phase 0] Whitespace trimmed from string cells.")

    # ── 4. Auto type conversion ───────────────────────────────────────────────
    if pre.get("auto_type_conversion", True):
        df = _infer_and_cast_dtypes(df)
        print(f"[Phase 0] Basic dtype inference applied.")

    # ── 5. Drop fully-empty rows and columns ──────────────────────────────────
    if pre.get("handle_nulls", True):
        before = df.shape
        df = df.dropna(how="all")           # drop rows where every cell is NaN
        df = df.loc[:, df.notna().any()]    # drop columns where every cell is NaN
        df = df.reset_index(drop=True)
        after = df.shape
        if before != after:
            print(f"[Phase 0] Dropped fully-empty rows/cols: {before} → {after}")

    print(f"[Phase 0] Setup complete. DataFrame shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_file(path: str, sheet_name: str | int = 0) -> pd.DataFrame:
    """Load Excel or CSV into a DataFrame."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[Phase 0] Input file not found: {path}")

    suffix = p.suffix.lower()

    if suffix in (".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"):
        df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, keep_default_na=False)
    elif suffix == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    elif suffix in (".tsv", ".txt"):
        df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    else:
        # Fallback: try CSV
        df = pd.read_csv(path, dtype=str, keep_default_na=False)

    # Replace empty strings with NaN so pandas null-handling works correctly
    df = df.replace(r"^\s*$", pd.NA, regex=True)
    return df


def _normalize_headers(df: pd.DataFrame, case_insensitive: bool = True) -> pd.DataFrame:
    """
    Strip whitespace from column names.
    Preserves original casing unless case_insensitive is True
    (in that case we keep original casing but strip surrounding spaces).
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _trim_string_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading/trailing whitespace from all string (object) columns."""
    df = df.copy()
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    return df


# Excel quirk helpers
_CURRENCY_RE   = re.compile(r"^\s*[$€£¥₹₩]\s*|[$€£¥₹₩]\s*$")
_PERCENT_RE    = re.compile(r"\s*%\s*$")
_THOUSAND_RE   = re.compile(r"(?<=\d),(?=\d{3})")
_BOOL_TRUE     = {"true", "yes", "1", "y"}
_BOOL_FALSE    = {"false", "no", "0", "n"}


def _try_numeric(series: pd.Series) -> Optional[pd.Series]:
    """Try to coerce a string series to numeric, handling Excel formatting."""
    cleaned = series.dropna().astype(str).map(
        lambda v: _THOUSAND_RE.sub("", _CURRENCY_RE.sub("", _PERCENT_RE.sub("", v.strip())))
    )
    coerced = pd.to_numeric(cleaned, errors="coerce")
    if coerced.notna().mean() >= 0.80:
        return pd.to_numeric(
            series.astype(str).map(
                lambda v: _THOUSAND_RE.sub("", _CURRENCY_RE.sub("", _PERCENT_RE.sub("", str(v).strip())))
                if pd.notna(v) else v
            ),
            errors="coerce",
        )
    return None


def _try_datetime(series: pd.Series) -> Optional[pd.Series]:
    """Try to coerce a string series to datetime."""
    try:
        converted = pd.to_datetime(series, errors="coerce", infer_datetime_format=True)
        if converted.notna().mean() >= 0.80:
            return converted
    except Exception:
        pass
    return None


def _try_boolean(series: pd.Series) -> Optional[pd.Series]:
    """Try to coerce a string series to boolean."""
    lower = series.dropna().astype(str).str.strip().str.lower()
    if lower.isin(_BOOL_TRUE | _BOOL_FALSE).mean() >= 0.90:
        return series.astype(str).str.strip().str.lower().map(
            lambda v: True if v in _BOOL_TRUE else (False if v in _BOOL_FALSE else pd.NA)
        )
    return None


def _infer_and_cast_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Walk every object/string column and attempt numeric → datetime → boolean
    inference in that priority order.
    """
    df = df.copy()
    for col in df.select_dtypes(include=["object", "string"]).columns:
        series = df[col]
        non_null = series.dropna()
        if len(non_null) == 0:
            continue

        # Numeric
        result = _try_numeric(non_null)
        if result is not None:
            df[col] = pd.to_numeric(
                series.astype(str).map(
                    lambda v: _THOUSAND_RE.sub(
                        "", _CURRENCY_RE.sub("", _PERCENT_RE.sub("", str(v).strip()))
                    ) if pd.notna(v) else v
                ),
                errors="coerce",
            )
            continue

        # Datetime
        result = _try_datetime(series)
        if result is not None:
            df[col] = result
            continue

        # Boolean
        result = _try_boolean(series)
        if result is not None:
            df[col] = result

    return df
