"""
execution/execution_phase_1_cleaning.py
========================================
PHASE 1 — Data Cleaning  (MUST run before everything else)
-----------------------------------------------------------
Why first? Every downstream phase depends on clean, consistent data.

Operations (in order):
  1. Trim / case normalisation   ← config["phase_6_cleaning"]["trim_clean"]
  2. Find & replace              ← config["phase_6_cleaning"]["find_replace"]
  3. Type conversion (strict)    ← config["phase_6_cleaning"]["type_conversion"]
  4. Missing value handling      ← config["phase_6_cleaning"]["missing_values"]

Config key : "phase_6_cleaning"  (detected in Phase 6 of the phases pipeline)
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from utils.execution_helpers import col_name, col_exists


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply all cleaning operations defined in config["phase_6_cleaning"].

    Args:
        df     : DataFrame produced by Phase 0.
        config : Full merged config dict.

    Returns:
        Cleaned DataFrame.
    """
    phase = config.get("phase_6_cleaning", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 1] No cleaning config found — skipping.")
        return df

    df = df.copy()

    # ── 1. Trim / clean ───────────────────────────────────────────────────────
    trim_cfg = phase.get("trim_clean", {})
    if trim_cfg.get("applied"):
        df = _apply_trim_clean(df, trim_cfg, on_missing_col)

    # ── 2. Find & Replace ─────────────────────────────────────────────────────
    replace_cfg = phase.get("find_replace", {})
    if replace_cfg.get("applied"):
        df = _apply_find_replace(df, replace_cfg, on_missing_col)

    # ── 3. Type conversion ────────────────────────────────────────────────────
    type_cfg = phase.get("type_conversion", {})
    if type_cfg.get("applied"):
        df = _apply_type_conversion(df, type_cfg, on_missing_col)

    # ── 4. Missing values ─────────────────────────────────────────────────────
    missing_cfg = phase.get("missing_values", {})
    if missing_cfg.get("applied"):
        df = _apply_missing_values(df, missing_cfg, on_missing_col)

    print(f"[Phase 1] Cleaning complete. Shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── 1. Trim / clean ──────────────────────────────────────────────────────────

def _apply_trim_clean(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    columns: List[Any] = cfg.get("details", {}).get("columns", [])
    for col_ref in columns:
        col = col_name(col_ref)
        if not col_exists(df, col, on_missing, "Phase 1"):
            continue
        if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
            print(f"  [Phase 1] Trimmed whitespace: '{col}'")
    return df


# ── 2. Find & Replace ────────────────────────────────────────────────────────

def _apply_find_replace(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    # Sort by execution_order if present
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    for rule in rules:
        col = col_name(rule.get("column", ""))
        if not col or not col_exists(df, col, on_missing, "Phase 1"):
            continue
        from_val = rule.get("from", "")
        to_val   = rule.get("to", "")
        df[col] = df[col].astype(str).replace(from_val, to_val)
        print(f"  [Phase 1] Find & Replace in '{col}': '{from_val}' → '{to_val}'")
    return df


# ── 3. Type conversion ───────────────────────────────────────────────────────

_COERCE: Dict[str, str] = {
    "float":    "pd.to_numeric(errors='coerce')",
    "int":      "Int64",
    "datetime": "datetime",
    "boolean":  "bool",
    "string":   "str",
    "category": "category",
}


def _apply_type_conversion(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    for rule in rules:
        col = col_name(rule.get("column", ""))
        if not col or not col_exists(df, col, on_missing, "Phase 1"):
            continue
        to_type = rule.get("to", "")
        try:
            if to_type in ("float", "int"):
                numeric = pd.to_numeric(df[col], errors="coerce")
                if to_type == "int":
                    df[col] = numeric.round().astype("Int64")
                else:
                    df[col] = numeric
            elif to_type == "datetime":
                df[col] = pd.to_datetime(df[col], errors="coerce", infer_datetime_format=True)
            elif to_type == "boolean":
                _BOOL_TRUE  = {"true", "yes", "1", "y"}
                _BOOL_FALSE = {"false", "no", "0", "n"}
                df[col] = (
                    df[col].astype(str).str.strip().str.lower()
                    .map(lambda v: True if v in _BOOL_TRUE else (False if v in _BOOL_FALSE else pd.NA))
                )
            elif to_type == "string":
                df[col] = df[col].astype(str)
            elif to_type == "category":
                df[col] = df[col].astype("category")
            print(f"  [Phase 1] Type conversion '{col}': {rule.get('from', '?')} → {to_type}")
        except Exception as exc:
            print(f"  [Phase 1] Type conversion failed for '{col}': {exc}")
    return df


# ── 4. Missing values ────────────────────────────────────────────────────────

def _apply_missing_values(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    rows_before = len(df)

    for rule in rules:
        col = col_name(rule.get("column", ""))
        if not col or not col_exists(df, col, on_missing, "Phase 1"):
            continue
        method = rule.get("method", "fill")
        value  = rule.get("value")

        if method == "drop":
            df = df.dropna(subset=[col]).reset_index(drop=True)
            print(f"  [Phase 1] Dropped rows with null in '{col}'")

        elif method == "ffill":
            df[col] = df[col].ffill()
            print(f"  [Phase 1] Forward-filled nulls in '{col}'")

        elif method == "bfill":
            df[col] = df[col].bfill()
            print(f"  [Phase 1] Backward-filled nulls in '{col}'")

        elif method == "interpolate":
            df[col] = pd.to_numeric(df[col], errors="coerce").interpolate(method="linear")
            print(f"  [Phase 1] Interpolated nulls in '{col}'")

        elif method == "fill":
            if value == "mean":
                fill_val = pd.to_numeric(df[col], errors="coerce").mean()
            elif value == "median":
                fill_val = pd.to_numeric(df[col], errors="coerce").median()
            elif value == "mode":
                mode = df[col].mode()
                fill_val = mode.iloc[0] if len(mode) > 0 else np.nan
            else:
                fill_val = value
            df[col] = df[col].fillna(fill_val)
            print(f"  [Phase 1] Filled nulls in '{col}' with {repr(fill_val)}")

        elif method == "flag":
            flag_col = str(value) if value else f"{col}_is_missing"
            df[flag_col] = df[col].isna().astype(int)
            print(f"  [Phase 1] Flagged nulls in '{col}' → new column '{flag_col}'")

    if len(df) != rows_before:
        print(f"  [Phase 1] Row count changed by missing-value rules: {rows_before} → {len(df)}")
    return df
