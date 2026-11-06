# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 6 – Data Cleaning Detection
====================================
Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Op-level execution_order stamps (trim→replace→type→missing)
  ✅ Column object references on every rule
  ✅ Data-type safety on type_conversion rules (coerce strategy)
  ✅ Rule-level execution_order (priority) stamps
  ✅ metadata.intermediate_columns tracking
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.data_cleaning_operations import (
    build_trim_clean_config,
    build_find_replace_config,
    build_type_conversion_config,
    build_missing_values_config,
)
from utils.config_utils import (
    add_rule_priority,
    make_column_object,
    upgrade_op,
    wrap_phase,
    ERROR_HANDLING_BLOCK,
)


# ─────────────────────────────────────────────────────────────────────────────
# Type-conversion rule upgrader
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_COERCE_STRATEGY: Dict[str, str] = {
    "float":    "pd.to_numeric(errors='coerce')",
    "int":      "pd.to_numeric(errors='coerce').astype('Int64')",
    "datetime": "pd.to_datetime(errors='coerce')",
    "boolean":  "astype(bool)",
    "string":   "astype(str)",
    "category": "astype('category')",
}


def _upgrade_type_conversion_rules(rules: list, df: pd.DataFrame) -> list:
    """
    Enrich each type-conversion rule with:
      • column object  (name + aliases + dtype)
      • coerce_strategy  – safe pandas expression to apply the conversion
      • on_error          – what to do when conversion fails (from ERROR_HANDLING_BLOCK)

    Before:  {"column": "amount", "from": "string", "to": "float"}
    After:   {"column": {name, aliases, dtype},
               "from": "string", "to": "float",
               "coerce_strategy": "pd.to_numeric(errors='coerce')",
               "on_error": "coerce"}
    """
    for rule in rules:
        col = rule.get("column", "")
        if isinstance(col, str) and col:
            series = df[col] if col in df.columns else None
            rule["column"] = make_column_object(col, series=series)
        to_type = rule.get("to", "")
        rule["coerce_strategy"] = _TYPE_COERCE_STRATEGY.get(to_type, "astype(str)")
        rule["on_error"] = ERROR_HANDLING_BLOCK["on_type_error"]
    return rules


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all data-cleaning transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_6_cleaning": {
            "trim_clean":      { "applied": bool, "execution_order": 1,
                                 "details": { "columns": [<col_obj>, ...] } },
            "find_replace":    { "applied": bool, "execution_order": 2,
                                 "details": { "rules": [
                                   { "column": <col_obj>, "from": str, "to": str,
                                     "execution_order": N }] } },
            "type_conversion": { "applied": bool, "execution_order": 3,
                                 "details": { "rules": [
                                   { "column": <col_obj>,
                                     "from": str, "to": str,
                                     "coerce_strategy": str,
                                     "on_error": "coerce",
                                     "execution_order": N }] } },
            "missing_values":  { "applied": bool, "execution_order": 4,
                                 "details": { "rules": [
                                   { "column": <col_obj>,
                                     "method": str, "value": Any,
                                     "execution_order": N }] } }
          },
          "metadata": { "phase": "phase_6_cleaning",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}

    # ── 6.1  TRIM / CASE NORMALISATION ───────────────────────────────────────
    trim_cfg = build_trim_clean_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if trim_cfg.get("details", {}).get("columns"):
        trim_cfg["details"]["columns"] = [
            make_column_object(c, series=df_in[c] if c in df_in.columns else None)
            if isinstance(c, str) else c
            for c in trim_cfg["details"]["columns"]
        ]
    trim_cfg["execution_order"] = 1

    # ── 6.2  FIND & REPLACE ───────────────────────────────────────────────────
    replace_cfg = build_find_replace_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(replace_cfg, 2, df=df_in, keys=("column",), ctx=ctx)

    # ── 6.3  TYPE CONVERSION ──────────────────────────────────────────────────
    type_cfg = build_type_conversion_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    if type_cfg.get("details", {}).get("rules"):
        # ✅ upgrade with coerce_strategy + on_error + column object
        _upgrade_type_conversion_rules(type_cfg["details"]["rules"], df_in)
        add_rule_priority(type_cfg["details"]["rules"])
    type_cfg["execution_order"] = 3

    # ── 6.4  MISSING VALUES ───────────────────────────────────────────────────
    missing_cfg = build_missing_values_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(missing_cfg, 4, df=df_in, keys=("column",), ctx=ctx)

    # ── Build phase body ──────────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "trim_clean":      trim_cfg,
        "find_replace":    replace_cfg,
        "type_conversion": type_cfg,
        "missing_values":  missing_cfg,
    }

    return wrap_phase("phase_6_cleaning", phase_body)