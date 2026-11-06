"""
utils/execution_helpers.py
==========================
Shared low-level helpers used across all execution phases.

Centralising these functions eliminates the four identical copies that
previously lived as file-private helpers in each execution phase module.

Usage
-----
    from utils.execution_helpers import col_name, col_exists

Functions
---------
col_name(ref)
    Extract a plain string column name from either a bare string or a
    column-object dict produced by the detection layer.

col_exists(df, col, on_missing, phase="")
    Check whether *col* is present in df.columns.
    Prints a warning and returns False when on_missing == "skip";
    raises KeyError otherwise.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def col_name(ref: Any) -> str:
    """
    Extract the string column name from a plain string or a column-object dict.

    Detection phases store column references as either:
      - a bare string:  "Amount"
      - a dict object:  {"name": "Amount", "dtype": "float64", ...}

    Both forms are accepted here so execution phases never need to branch
    on the reference type.
    """
    if isinstance(ref, dict):
        return ref.get("name", "")
    return str(ref)


def col_exists(
    df: pd.DataFrame,
    col: str,
    on_missing: str,
    phase: str = "",
) -> bool:
    """
    Return True when *col* is present in ``df.columns``.

    Args:
        df         : DataFrame to check.
        col        : Column name (plain string).
        on_missing : Policy from error_handling config.
                     "skip"  → print a warning and return False.
                     anything else → raise KeyError.
        phase      : Human-readable phase label, e.g. "Phase 1".
                     Included in warning / error messages when provided.

    Returns:
        True if the column exists, False when missing and on_missing=="skip".

    Raises:
        KeyError: when column is missing and on_missing != "skip".
    """
    if col not in df.columns:
        tag = f"[{phase}] " if phase else ""
        if on_missing == "skip":
            print(f"  {tag}Column '{col}' not found — skipping rule.")
            return False
        raise KeyError(f"{tag}Column '{col}' not found in DataFrame.")
    return True
