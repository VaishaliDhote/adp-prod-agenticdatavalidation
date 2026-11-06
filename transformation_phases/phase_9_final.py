# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 9 – Finalization Detection
====================================
Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Op-level execution_order stamps
  ✅ Column object references on every rule
  ✅ Rule-level execution_order (priority) stamps
  ✅ metadata.intermediate_columns tracking
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.finalization_operations import (
    build_formula_to_value_config,
    build_remove_intermediate_columns_config,
    build_final_save_config,
    build_resequence_config,
)
from utils.config_utils import (
    make_column_object,
    upgrade_op,
    wrap_phase,
)


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]] = None,
    df_in_full: Optional[pd.DataFrame] = None,   # ← original unfiltered df_in
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all finalization transformations between df_in and df_out.

    Args:
        df_in:       Filtered input DataFrame (used for formula/intermediate detection).
        df_out:      Output DataFrame.
        column_mapping: Optional column rename map.
        df_in_full:  The ORIGINAL unfiltered input DataFrame — required for
                     resequence detection.  When rows are filtered out, df_in
                     (filtered) carries sparse index values like [1,3,4,6,…]
                     which are identical to df_out — making resequence
                     undetectable.  df_in_full still has the clean 1..N
                     sequence that proves resequencing is needed.
                     Falls back to df_in when not provided.
    """
    cm = column_mapping or {}

    # ── 9.1  FORMULA → VALUE ──────────────────────────────────────────────────
    formula_cfg = build_formula_to_value_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if formula_cfg.get("details", {}).get("columns"):
        formula_cfg["details"]["columns"] = [
            make_column_object(c, series=df_out[c] if c in df_out.columns else None)
            if isinstance(c, str) else c
            for c in formula_cfg["details"]["columns"]
        ]
    formula_cfg["execution_order"] = 1

    # ── 9.2  REMOVE INTERMEDIATE COLUMNS ─────────────────────────────────────
    intermediate_cfg = build_remove_intermediate_columns_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if intermediate_cfg.get("details", {}).get("columns"):
        intermediate_cfg["details"]["columns"] = [
            make_column_object(c, series=df_in[c] if c in df_in.columns else None)
            if isinstance(c, str) else c
            for c in intermediate_cfg["details"]["columns"]
        ]
    intermediate_cfg["execution_order"] = 2

    # ── 9.3  FINAL SAVE ───────────────────────────────────────────────────────
    save_cfg = build_final_save_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    save_cfg["execution_order"] = 3

    # ── 9.4  RESEQUENCE SERIAL / INDEX COLUMNS ────────────────────────────────
    # CRITICAL: use df_in_full (original unfiltered DataFrame) not df_in_filtered.
    # After filtering, df_in["Employee Count"] = [1,3,4,6,...] (sparse) which
    # matches df_out exactly — so the detector can't tell resequencing is needed.
    # df_in_full["Employee Count"] = [1,2,3,...,100] proves the column was
    # originally sequential and the output should be re-numbered 1..N.
    _df_for_reseq = df_in_full if df_in_full is not None else df_in

    print(f"\n  [phase9 resequence] df_in_full provided: {df_in_full is not None}")
    print(f"  [phase9 resequence] _df_for_reseq rows: {len(_df_for_reseq)}")
    print(f"  [phase9 resequence] df_out rows: {len(df_out)}")
    print(f"  [phase9 resequence] _df_for_reseq cols: {list(_df_for_reseq.columns)}")
    for c in _df_for_reseq.columns:
        if _df_for_reseq[c].dtype.kind in ('i', 'f', 'u'):
            print(f"  [phase9 resequence]   col '{c}' numeric, first 5: {_df_for_reseq[c].tolist()[:5]}")

    resequence_cfg = build_resequence_config(
        input_df=_df_for_reseq, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(resequence_cfg, 4, df=df_out, keys=("column",), ctx=ctx)

    # ── Build phase body ──────────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "formula_to_value":            formula_cfg,
        "remove_intermediate_columns": intermediate_cfg,
        "final_save":                  save_cfg,
        "resequence":                  resequence_cfg,
    }

    # Collect intermediate columns across this phase
    return wrap_phase("phase_9_final", phase_body)