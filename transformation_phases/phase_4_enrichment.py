# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 4 – Enrichment Detection
================================
Delegates detection to six focused utils:

  build_lookup_config              → 4.1  LOOKUP  (VLOOKUP / XLOOKUP / INDEX-MATCH)
  build_calculated_columns_config  → 4.2  CALCULATED COLUMNS
  build_conditional_columns_config → 4.3  CONDITIONAL COLUMNS  (IF / IFS / SWITCH)
  build_text_operations_config     → 4.4  TEXT OPERATIONS
  build_date_operations_config     → 4.5  DATE / TIME OPERATIONS
  build_math_operations_config     → 4.6  MATH / STATISTICAL OPERATIONS

Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Op-level execution_order stamps
  ✅ Column object references on every rule
  ✅ Safe formula objects  (expression + inputs + output_dtype)
  ✅ Fully-defined lookup rules  (left_key + right_key + source + join_type)
  ✅ Rule-level execution_order (priority) stamps
  ✅ metadata.intermediate_columns tracking
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.enrichment_lookup      import build_lookup_config
from utils.transformations.enrichment_calculated  import build_calculated_columns_config
from utils.transformations.enrichment_conditional import build_conditional_columns_config
from utils.transformations.enrichment_text        import build_text_operations_config
from utils.transformations.enrichment_date        import build_date_operations_config
from utils.transformations.enrichment_math        import build_math_operations_config
from utils.config_utils import (
    add_rule_priority,
    make_lookup_rule,
    upgrade_op,
    upgrade_formula_refs,
    wrap_phase,
)


# ─────────────────────────────────────────────────────────────────────────────
# Lookup rule upgrader
# ─────────────────────────────────────────────────────────────────────────────

def _upgrade_lookup_rules(rules: list) -> list:
    """
    Expand bare lookup rules that only carry a 'key' field into fully-defined
    lookup rules with source, join_type, and right_key populated.

    Before:  {"key": "customer_id", "columns_added": ["region"]}
    After:   {"left_key": "customer_id", "right_key": "customer_id",
               "source": "same_sheet", "join_type": "left",
               "lookup_type": "VLOOKUP", "columns_added": ["region"],
               "confidence": 0.85}
    """
    upgraded = []
    for r in rules:
        key = r.get("key") or r.get("left_key", "")
        upgraded.append(
            make_lookup_rule(
                left_key      = key,
                right_key     = r.get("right_key", key),
                columns_added = r.get("columns_added", []),
                source        = r.get("source", "same_sheet"),
                join_type     = r.get("join_type", "left"),
                lookup_type   = r.get("lookup_type", "VLOOKUP"),
                confidence    = r.get("confidence", 0.85),
            )
        )
    return upgraded


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all enrichment transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_4_enrichment": {
            "lookup":               { "applied": bool, "execution_order": 1,
                                      "details": { "rules": [<full lookup rule>] } },
            "calculated_columns":   { "applied": bool, "execution_order": 2,
                                      "details": { "rules": [
                                        { "column": <col_obj>,
                                          "formula": { "expression": str,
                                                       "inputs": [...],
                                                       "output_dtype": str },
                                          ... }] } },
            "conditional_columns":  { "applied": bool, "execution_order": 3, ... },
            "text_operations":      { "applied": bool, "execution_order": 4, ... },
            "date_operations":      { "applied": bool, "execution_order": 5, ... },
            "math_operations":      { "applied": bool, "execution_order": 6, ... }
          },
          "metadata": { "phase": "phase_4_enrichment",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}
    print(f"Starting phase_4_enrichment detect: df_in shape {df_in.shape}, df_out shape {df_out.shape}, column_mapping {cm}")

    # ── 4.1  LOOKUP ───────────────────────────────────────────────────────────
    print("Building lookup config")
    lookup_cfg = build_lookup_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if lookup_cfg.get("details", {}).get("rules"):
        lookup_cfg["details"]["rules"] = _upgrade_lookup_rules(
            lookup_cfg["details"]["rules"]
        )
        add_rule_priority(lookup_cfg["details"]["rules"])
    lookup_cfg["execution_order"] = 1  # lookup has custom _upgrade_lookup_rules, keep as-is
    print(f"Lookup cfg: applied={lookup_cfg.get('applied')}, rules={len(lookup_cfg['details']['rules'])}")

    # ── 4.2  CALCULATED COLUMNS ───────────────────────────────────────────────
    print("Building calculated columns config")
    calc_cfg = build_calculated_columns_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if calc_cfg.get("details", {}).get("rules"):
        upgrade_formula_refs(calc_cfg["details"]["rules"], key="formula")
    upgrade_op(calc_cfg, 2, df=df_out, keys=("column",), ctx=ctx, ctx_df_key="out")
    print(f"Calculated cfg: applied={calc_cfg.get('applied')}, rules={len(calc_cfg['details']['rules'])}")

    # ── 4.3  CONDITIONAL COLUMNS ──────────────────────────────────────────────
    print("Building conditional columns config")
    cond_cfg = build_conditional_columns_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(cond_cfg, 3, df=df_out, keys=("column",), ctx=ctx, ctx_df_key="out")
    print(f"Conditional cfg: applied={cond_cfg.get('applied')}, rules={len(cond_cfg['details']['rules'])}")

    # ── 4.4  TEXT OPERATIONS ──────────────────────────────────────────────────
    print("Building text operations config")
    text_cfg = build_text_operations_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(text_cfg, 4, df=df_out, keys=("column",), ctx=ctx, ctx_df_key="out")
    print(f"Text cfg: applied={text_cfg.get('applied')}, rules={len(text_cfg['details']['rules'])}")

    # ── 4.5  DATE OPERATIONS ──────────────────────────────────────────────────
    print("Building date operations config")
    date_cfg = build_date_operations_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(date_cfg, 5, df=df_out, keys=("column",), ctx=ctx, ctx_df_key="out")
    print(f"Date cfg: applied={date_cfg.get('applied')}, rules={len(date_cfg['details']['rules'])}")

    # ── 4.6  MATH OPERATIONS ──────────────────────────────────────────────────
    # Extract columns already detected by calculated_columns to avoid duplication
    calc_detected_cols = [
        r.get("column", {}).get("name") if isinstance(r.get("column"), dict) else r.get("column")
        for r in calc_cfg.get("details", {}).get("rules", [])
        if r.get("formula") != "unknown"
    ]
    calc_detected_cols = [c for c in calc_detected_cols if c]  # filter out None
    print(f"Columns detected by calculated_columns (to skip in math): {calc_detected_cols}")
    
    print("Building math operations config")
    math_cfg = build_math_operations_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
        skip_columns=calc_detected_cols,
    )
    if math_cfg.get("details", {}).get("rules"):
        upgrade_formula_refs(math_cfg["details"]["rules"], key="formula")
    upgrade_op(math_cfg, 6, df=df_out, keys=("column",), ctx=ctx, ctx_df_key="out")
    print(f"Math cfg: applied={math_cfg.get('applied')}, rules={len(math_cfg['details']['rules'])}")

    # ── Build phase body ──────────────────────────────────────────────────────
    print("Building phase body")
    phase_body: Dict[str, Any] = {
        "lookup":              lookup_cfg,
        "calculated_columns":  calc_cfg,
        "conditional_columns": cond_cfg,
        "text_operations":     text_cfg,
        "date_operations":     date_cfg,
        "math_operations":     math_cfg,
    }

    result = wrap_phase("phase_4_enrichment", phase_body)
    print("Returning result")
    return result