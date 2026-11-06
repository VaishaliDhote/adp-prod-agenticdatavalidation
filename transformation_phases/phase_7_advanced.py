# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 7 – Advanced Logic Detection
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

from utils.transformations.advanced_array_formulas      import build_array_formulas_config
from utils.transformations.advanced_indirect_reference  import build_indirect_reference_config
from utils.config_utils import (
    make_column_object,
    upgrade_op,
    wrap_phase,
)


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all advanced-logic transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_7_advanced": {
            "array_formulas": {
              "applied": bool, "execution_order": 1,
              "details": {
                "functions": [...],
                "filter":  { "conditions": [str, ...] },
                "unique":  { "unique_on": [<col_obj>, ...] },
                "sort":    { "sort_by": [{"column": <col_obj>, "order": str}, ...] }
              }
            },
            "indirect_reference": {
              "applied": bool, "execution_order": 2,
              "details": {
                "columns":    [<col_obj>, ...],
                "references": [{ "column": <col_obj>, "type": str, ... },
                               ...]
              }
            }
          },
          "metadata": { "phase": "phase_7_advanced",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}

    # ── 7.1  Array Formulas ───────────────────────────────────────────────────
    array_cfg = build_array_formulas_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    # Upgrade column refs inside sort_by sub-block
    sort_by = array_cfg.get("details", {}).get("sort", {}).get("sort_by", [])
    for item in sort_by:
        if isinstance(item.get("column"), str):
            col = item["column"]
            item["column"] = make_column_object(
                col, series=df_out[col] if col in df_out.columns else None
            )
    # Upgrade unique_on sub-block
    unique_on = array_cfg.get("details", {}).get("unique", {}).get("unique_on", [])
    if unique_on:
        array_cfg["details"]["unique"]["unique_on"] = [
            make_column_object(c, series=df_out[c] if c in df_out.columns else None)
            if isinstance(c, str) else c
            for c in unique_on
        ]
    array_cfg["execution_order"] = 1

    # ── 7.2  Indirect / Dynamic References ───────────────────────────────────
    indirect_cfg = build_indirect_reference_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    # Upgrade top-level columns list
    if indirect_cfg.get("details", {}).get("columns"):
        indirect_cfg["details"]["columns"] = [
            make_column_object(c, series=df_out[c] if c in df_out.columns else None)
            if isinstance(c, str) else c
            for c in indirect_cfg["details"]["columns"]
        ]
    # Upgrade per-reference column field
    upgrade_op(indirect_cfg, 2, df=df_out, keys=("column",), ctx=ctx)

    # ── Build phase body ──────────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "array_formulas":     array_cfg,
        "indirect_reference": indirect_cfg,
    }

    return wrap_phase("phase_7_advanced", phase_body)