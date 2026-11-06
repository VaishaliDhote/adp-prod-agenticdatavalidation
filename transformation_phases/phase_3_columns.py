# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 3 – Column Operations Detection
======================================
Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Op-level execution_order stamps (select→rename→reorder→split→merge)
  ✅ Column object references on every rule
  ✅ metadata.intermediate_columns tracking
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.column_operations import (
    build_select_config,
    build_reorder_config,
    build_rename_config,
    build_split_config,
    build_merge_config,
    _remap_output,
)
from utils.config_utils import (
    upgrade_op,
    upgrade_col_list,
    wrap_phase,
)


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all column-level transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_3_columns": {
            "select_columns":  { "applied": bool, "execution_order": 1,
                                 "details": { "columns": [<col_obj>, ...] } },
            "rename_columns":  { "applied": bool, "execution_order": 2,
                                 "details": { "mappings": {...} } },
            "reorder_columns": { "applied": bool, "execution_order": 3,
                                 "details": { "order": [<col_obj>, ...] } },
            "split_columns":   { "applied": bool, "execution_order": 4,
                                 "details": { "rules": [...] } },
            "merge_columns":   { "applied": bool, "execution_order": 5,
                                 "details": { "rules": [...] } }
          },
          "metadata": { "phase": "phase_3_columns",
                        "intermediate_columns": [...] }
        }
    """
    print(f"[phase_3_columns] Starting detection with column_mapping: {column_mapping}")

    # Remap output columns to input namespace once; pass pre-remapped df to all
    # builder functions so they skip the remap internally (no-op on already-renamed cols).
    out_df_remapped = _remap_output(df_out, column_mapping)
    print(f"[phase_3_columns] Pre-remapped output columns: {list(out_df_remapped.columns)}")

    # ── D. SELECT / PROJECT ───────────────────────────────────────────────────
    select_cfg = build_select_config(input_df=df_in, output_df=out_df_remapped, column_mapping=column_mapping)
    print(f"select_cfg before column object upgrade: {select_cfg}")
    
    upgrade_col_list(select_cfg, df_out, "columns", ctx=ctx)
    select_cfg["execution_order"] = 1
    
    # ── F. RENAME ─────────────────────────────────────────────────────────────
    rename_cfg = build_rename_config(
        input_df=df_in, output_df=out_df_remapped, column_mapping=column_mapping,
    )
    rename_cfg["execution_order"] = 2
    

    # ── E. REORDER ────────────────────────────────────────────────────────────
    reorder_cfg = build_reorder_config(
        input_df=df_in, output_df=out_df_remapped, column_mapping=column_mapping,
    )
    print(f"reorder_cfg before column object upgrade: {reorder_cfg}")
    upgrade_col_list(reorder_cfg, df_out, "order", ctx=ctx)
    reorder_cfg["execution_order"] = 3

    # ── H. SPLIT ─────────────────────────────────────────────────────────────
    split_cfg = build_split_config(
        input_df=df_in, output_df=out_df_remapped, column_mapping=column_mapping,
    )
    print(f"split_cfg before column object upgrade: {split_cfg}")   
    upgrade_op(split_cfg, 4, df=df_in, keys=("source_column",), ctx=ctx)

    # ── I. MERGE / CONCATENATE ────────────────────────────────────────────────
    merge_cfg = build_merge_config(
        input_df=df_in, output_df=out_df_remapped, column_mapping=column_mapping,
    )
    print(f"merge_cfg before column object upgrade: {merge_cfg}")
    upgrade_op(merge_cfg, 5, df=df_in, keys=("sources",), ctx=ctx)

    # ── Build phase body ──────────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "select_columns":  select_cfg,
        "rename_columns":  rename_cfg,
        "reorder_columns": reorder_cfg,
        "split_columns":   split_cfg,
        "merge_columns":   merge_cfg,
    }
    print(f"[phase_3_columns] Phase body before metadata collection: {phase_body}")
    return wrap_phase("phase_3_columns", phase_body)