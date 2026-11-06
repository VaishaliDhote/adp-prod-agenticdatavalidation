"""
execution/execution_phase_2_structure.py
==========================================
PHASE 2 — Structural Transformations
--------------------------------------
Changes the SHAPE of the DataFrame.

Operations (mutually exclusive — only one executes per run):
  9.  Transpose             ← config["phase_1_structure"]["transpose"]
  10. Unpivot (melt)        ← config["phase_1_structure"]["unpivot"]
  11. Pivot                 ← config["phase_1_structure"]["pivot"]
  12. Hierarchy transform   ← config["phase_1_structure"]["hierarchy_transform"]

The active operation is selected via config["phase_1_structure"]["execution"].

Config key : "phase_1_structure"
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from utils.execution_helpers import col_name


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply the active structural transformation defined in config["phase_1_structure"].

    Args:
        df     : DataFrame produced by Phase 1.
        config : Full merged config dict.

    Returns:
        Structurally transformed DataFrame.
    """
    phase = config.get("phase_1_structure", {})
    if not phase:
        print("[Phase 2] No structural config found — skipping.")
        return df

    active_op = phase.get("execution", "none")
    print(f"[Phase 2] Active structural operation: '{active_op}'")

    if active_op == "transpose":
        df = _apply_transpose(df, phase.get("transpose", {}))
    elif active_op == "unpivot":
        rules = phase.get("unpivot", {}).get("details", {}).get("rules", [])
        rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
        for rule in rules:
            df = _apply_unpivot(df, rule)
    elif active_op == "pivot":
        rules = phase.get("pivot", {}).get("details", {}).get("rules", [])
        rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
        for rule in rules:
            df = _apply_pivot(df, rule)
    elif active_op == "none":
        print("[Phase 2] No structural transformation needed.")
    else:
        print(f"[Phase 2] Unknown structural op '{active_op}' — skipping.")

    # Hierarchy runs independently (does not conflict with pivot/unpivot/transpose)
    hier_cfg = phase.get("hierarchy_transform", {})
    if hier_cfg.get("applied"):
        df = _apply_hierarchy(df, hier_cfg)

    print(f"[Phase 2] Structural phase complete. Shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── 9. Transpose ─────────────────────────────────────────────────────────────

def _apply_transpose(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    if not cfg.get("applied"):
        return df
    print("  [Phase 2] Applying TRANSPOSE …")
    transposed = df.T.reset_index()
    # Promote first row as header
    transposed.columns = transposed.iloc[0].astype(str).tolist()
    transposed = transposed.iloc[1:].reset_index(drop=True)
    print(f"  [Phase 2] Transposed: {df.shape} → {transposed.shape}")
    return transposed


# ── 10. Unpivot (melt) ───────────────────────────────────────────────────────

def _apply_unpivot(df: pd.DataFrame, rule: Dict[str, Any]) -> pd.DataFrame:
    id_cols     = [col_name(c) for c in rule.get("id_columns",    [])]
    val_cols    = [col_name(c) for c in rule.get("value_columns", [])]
    var_name    = rule.get("var_name",   "variable")
    value_name  = rule.get("value_name", "value")

    # Fall back: use all non-id columns as value columns
    if not val_cols:
        val_cols = [c for c in df.columns if c not in id_cols]

    # Guard: keep only columns that actually exist
    id_cols  = [c for c in id_cols  if c in df.columns]
    val_cols = [c for c in val_cols if c in df.columns]

    if not val_cols:
        print("  [Phase 2] Unpivot skipped: no valid value columns found.")
        return df

    melted = df.melt(
        id_vars=id_cols,
        value_vars=val_cols,
        var_name=var_name,
        value_name=value_name,
    ).reset_index(drop=True)

    print(f"  [Phase 2] Unpivoted: id={id_cols}, vars={var_name}, values={value_name} → shape {melted.shape}")
    return melted


# ── 11. Pivot ────────────────────────────────────────────────────────────────

def _apply_pivot(df: pd.DataFrame, rule: Dict[str, Any]) -> pd.DataFrame:
    index_col  = col_name(rule.get("index",       ""))
    column_col = col_name(rule.get("columns",     ""))
    values_col = rule.get("values",      None)
    agg_func   = rule.get("aggregation", "sum")

    if not index_col or not column_col:
        print("  [Phase 2] Pivot skipped: missing index or columns field.")
        return df

    if index_col not in df.columns or column_col not in df.columns:
        print(f"  [Phase 2] Pivot skipped: '{index_col}' or '{column_col}' not in DataFrame.")
        return df

    # Resolve value columns
    if values_col is None:
        val_list = [c for c in df.columns if c not in (index_col, column_col)]
    elif isinstance(values_col, list):
        val_list = [col_name(v) for v in values_col]
    else:
        val_list = [col_name(values_col)]

    val_list = [c for c in val_list if c in df.columns]
    if not val_list:
        print("  [Phase 2] Pivot skipped: no valid value columns.")
        return df

    try:
        pivoted = df.pivot_table(
            index=index_col,
            columns=column_col,
            values=val_list if len(val_list) > 1 else val_list[0],
            aggfunc=agg_func,
        ).reset_index()
        # Flatten multi-level columns
        if isinstance(pivoted.columns, pd.MultiIndex):
            pivoted.columns = ["_".join(str(c) for c in col).strip("_")
                               for col in pivoted.columns]
        print(f"  [Phase 2] Pivoted: index={index_col}, cols={column_col} → shape {pivoted.shape}")
        return pivoted
    except Exception as exc:
        print(f"  [Phase 2] Pivot failed: {exc}")
        return df


# ── 12. Hierarchy transform ──────────────────────────────────────────────────

def _apply_hierarchy(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    details   = cfg.get("details", {})
    hier_type = details.get("type", "flatten")

    if hier_type == "flatten":
        # Find parent / child columns by common naming hints
        parent_col = next((c for c in df.columns if "parent" in c.lower()), None)
        level_cols: List[str] = [c for c in df.columns if "level" in c.lower()]

        if level_cols:
            # Merge hierarchical levels into a single path column
            df = df.copy()
            df["_hierarchy_path"] = df[level_cols].apply(
                lambda row: " > ".join(str(v) for v in row if pd.notna(v) and str(v).strip()),
                axis=1,
            )
            print(f"  [Phase 2] Hierarchy flattened into '_hierarchy_path'")
        else:
            print("  [Phase 2] Hierarchy flatten: no level columns detected — skipping.")

    elif hier_type == "create_hierarchical":
        print("  [Phase 2] Hierarchy create: not applicable in DataFrame context — skipping.")

    return df
