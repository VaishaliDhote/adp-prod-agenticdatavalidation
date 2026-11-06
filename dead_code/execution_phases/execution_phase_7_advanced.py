"""
execution/execution_phase_7_advanced.py
=========================================
PHASE 7 — Advanced Logic
--------------------------
Operations:
  32. Array formulas (FILTER/UNIQUE/SORT equivalents)
      ← config["phase_7_advanced"]["array_formulas"]
  33. Indirect / dynamic references
      ← config["phase_7_advanced"]["indirect_reference"]

Config key : "phase_7_advanced"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from utils.execution_helpers import col_name, col_exists


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply advanced logic operations defined in config["phase_7_advanced"].

    Args:
        df     : DataFrame produced by Phase 6.
        config : Full merged config dict.

    Returns:
        DataFrame with advanced transformations applied.
    """
    phase          = config.get("phase_7_advanced", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 7] No advanced config found — skipping.")
        return df

    df = df.copy()

    # ── 32. Array formulas ────────────────────────────────────────────────────
    array_cfg = phase.get("array_formulas", {})
    if array_cfg.get("applied"):
        df = _apply_array_formulas(df, array_cfg, on_missing_col)

    # ── 33. Indirect / dynamic references ────────────────────────────────────
    indirect_cfg = phase.get("indirect_reference", {})
    if indirect_cfg.get("applied"):
        df = _apply_indirect_reference(df, indirect_cfg, on_missing_col)

    print(f"[Phase 7] Advanced logic complete. Shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── 32. Array formulas ────────────────────────────────────────────────────────

def _apply_array_formulas(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Apply FILTER / UNIQUE / SORT array-formula equivalents.

    Config shape:
      {
        "details": {
          "functions": ["FILTER", "UNIQUE", "SORT"],   ← which ops were detected
          "filter":  {"conditions": ["col == 'val'", ...]},
          "unique":  {"unique_on": [col_obj, ...]},
          "sort":    {"sort_by": [{"column": col_obj, "order": "asc"|"desc"}, ...]}
        }
      }
    """
    details   = cfg.get("details", {})
    functions = [f.upper() for f in details.get("functions", [])]

    # ── FILTER ────────────────────────────────────────────────────────────────
    if "FILTER" in functions:
        conditions: List[str] = details.get("filter", {}).get("conditions", [])
        for cond in conditions:
            if not cond:
                continue
            try:
                df = df[df.eval(cond)].reset_index(drop=True)
                print(f"  [Phase 7] Array FILTER: '{cond}' → {len(df)} rows")
            except Exception as exc:
                print(f"  [Phase 7] Array FILTER condition '{cond}' failed: {exc}")

    # ── UNIQUE ────────────────────────────────────────────────────────────────
    if "UNIQUE" in functions:
        unique_on_refs: List[Any] = details.get("unique", {}).get("unique_on", [])
        unique_cols = [col_name(c) for c in unique_on_refs]
        unique_cols = [c for c in unique_cols if c and c in df.columns]
        if unique_cols:
            before = len(df)
            df = df.drop_duplicates(subset=unique_cols).reset_index(drop=True)
            print(f"  [Phase 7] Array UNIQUE on {unique_cols}: {before} → {len(df)} rows")
        else:
            print("  [Phase 7] Array UNIQUE: no valid columns — skipping.")

    # ── SORT ──────────────────────────────────────────────────────────────────
    if "SORT" in functions:
        sort_by: List[Dict] = details.get("sort", {}).get("sort_by", [])
        sort_keys: List[str]  = []
        sort_asc:  List[bool] = []
        for item in sort_by:
            col   = col_name(item.get("column", ""))
            order = str(item.get("order", "asc")).lower()
            if col and col in df.columns:
                sort_keys.append(col)
                sort_asc.append(order != "desc")
        if sort_keys:
            df = df.sort_values(by=sort_keys, ascending=sort_asc).reset_index(drop=True)
            print(f"  [Phase 7] Array SORT by {sort_keys}")

    return df


# ── 33. Indirect / dynamic references ────────────────────────────────────────

def _apply_indirect_reference(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Resolve indirect / dynamic column references.

    Config shape:
      {
        "details": {
          "columns":    [col_obj, ...],          ← columns that use indirect refs
          "references": [
            {"column": col_obj, "type": "row_ref"|"column_ref"|"range_ref", ...}
          ]
        }
      }

    In a pandas context, "indirect reference" most commonly means a column
    whose values point to another column name.  We resolve this by looking up
    each row's value in the referenced column.
    """
    details    = cfg.get("details", {})
    references = details.get("references", [])

    for ref in references:
        src_col  = col_name(ref.get("column", ""))
        ref_type = ref.get("type", "column_ref")

        if not src_col or not col_exists(df, src_col, on_missing, "Phase 7"):
            continue

        if ref_type == "column_ref":
            # Each cell in src_col names another column; resolve to that column's value
            out_col = f"{src_col}_resolved"
            try:
                def _resolve_row(row: pd.Series) -> Any:
                    target_col = str(row[src_col])
                    return row.get(target_col, pd.NA)

                df[out_col] = df.apply(_resolve_row, axis=1)
                print(f"  [Phase 7] Indirect ref '{src_col}' resolved → '{out_col}'")
            except Exception as exc:
                print(f"  [Phase 7] Indirect ref '{src_col}' failed: {exc}")

        elif ref_type == "row_ref":
            # Row-offset reference — shift the column by a given offset
            offset = int(ref.get("offset", 1))
            out_col = f"{src_col}_offset_{offset}"
            df[out_col] = df[src_col].shift(offset)
            print(f"  [Phase 7] Row ref '{src_col}' shifted by {offset} → '{out_col}'")

        else:
            print(f"  [Phase 7] Indirect ref type '{ref_type}' not supported — skipping.")

    return df
