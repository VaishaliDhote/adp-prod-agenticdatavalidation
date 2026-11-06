
# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - Sheet Loading Helper. REUSABLE.
# Rework: extend supported formats and preserve sheet/column order explicitly.
# ============================================================================
from __future__ import annotations

import os
from typing import Any

import pandas as pd


def load_sheets_from_file_state(
    file_state: dict[str, Any],
    key: str,
) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Load all sheets for every file listed under *key* inside *file_state*.

    Parameters
    ----------
    file_state : dict
        A FileState-compatible dict that contains ``{key: {file_name: {path: ...}}}``.
    key : str
        The sub-dict to iterate over, e.g. ``"input_files"`` or ``"output_files"``.

    Returns
    -------
    dict[str, dict[str, pd.DataFrame]]
        ``{file_name: {sheet_name: DataFrame}}``
    """
    result: dict[str, dict[str, pd.DataFrame]] = {}

    files: dict[str, Any] = (file_state or {}).get(key, {}) or {}

    for file_name, file_info in files.items():
        file_path: str | None = file_info.get("path")

        if not file_path or not os.path.exists(file_path):
            print(f"[file_loader_util] File not found or path missing [{key}]: {file_path}")
            continue

        ext = os.path.splitext(file_path)[-1].lower()

        if ext in (".xlsx", ".xls"):
            sheets: dict[str, pd.DataFrame] = pd.read_excel(
                file_path, sheet_name=None, parse_dates=True
            )
        elif ext == ".csv":
            sheets = {"Sheet1": pd.read_csv(file_path)}
        else:
            print(f"[file_loader_util] Unsupported file type '{ext}' — skipping: {file_path}")
            sheets = {}

        result[file_name] = sheets
        print(f"[file_loader_util] Loaded [{key}] {file_path} | Sheets: {list(sheets.keys())}")

    return result
