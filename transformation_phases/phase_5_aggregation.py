# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 5 – Aggregation Detection
==================================
Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Op-level execution_order stamps
  ✅ already_aggregated safety flag on groupby
  ✅ Column object references on every rule
  ✅ Rule-level execution_order (priority) stamps
  ✅ metadata.intermediate_columns tracking
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.aggregations import (
    build_groupby_config,
    build_conditional_aggregation_config,
    build_running_total_config,
    build_ranking_config,
)
from utils.transformations.aggregations_extended import (
    build_lag_lead_config,
    build_advanced_ranking_config,
    build_value_window_config,
)
from utils.config_utils import (
    make_groupby_safe,
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
    Detect all aggregation-level transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_5_aggregation": {
            "groupby": {
              "applied": bool, "execution_order": 1,
              "details": {
                "already_aggregated": false,        ← ✅ safety flag
                "rules": [
                  { "group_by": [<col_obj>, ...],
                    "aggregations": { "<col>": "<func>", ... },
                    "execution_order": N }
                ]
              }
            },
            "conditional_aggregation": { "applied": bool, "execution_order": 2, ... },
            "running_total":            { "applied": bool, "execution_order": 3, ... },
            "ranking":                  { "applied": bool, "execution_order": 4, ... },
            "lag_lead":                 { "applied": bool, "execution_order": 5, ... },
            "advanced_ranking":         { "applied": bool, "execution_order": 6, ... },
            "value_windows":            { "applied": bool, "execution_order": 7, ... }
          },
          "metadata": { "phase": "phase_5_aggregation",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}

    # ── 5.1  GROUP BY ─────────────────────────────────────────────────────────
    groupby_cfg = build_groupby_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )    # ✅ inject already_aggregated safety flag
    make_groupby_safe(groupby_cfg)
    upgrade_op(groupby_cfg, 1, df=df_in, keys=("group_by",), ctx=ctx)

    # ── 5.3  CONDITIONAL AGGREGATION ─────────────────────────────────────────
    cond_agg_cfg = build_conditional_aggregation_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(cond_agg_cfg, 2, df=df_out, keys=("column",), ctx=ctx)

    # ── 5.4a RUNNING TOTAL ────────────────────────────────────────────────────
    running_total_cfg = build_running_total_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(running_total_cfg, 3, df=df_in, keys=("column", "order_by"), ctx=ctx)

    # ── 5.4b RANKING ──────────────────────────────────────────────────────────
    ranking_cfg = build_ranking_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(ranking_cfg, 4, df=df_in, keys=("column",), ctx=ctx)

    # ── 5.5  LAG / LEAD ───────────────────────────────────────────────────────
    lag_lead_cfg = build_lag_lead_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(lag_lead_cfg, 5, df=df_in, keys=("column",), ctx=ctx)

    # ── 5.6  ADVANCED RANKING ─────────────────────────────────────────────────
    advanced_ranking_cfg = build_advanced_ranking_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(advanced_ranking_cfg, 6, df=df_in, keys=("column",), ctx=ctx)

    # ── 5.7  VALUE WINDOWS ────────────────────────────────────────────────────
    value_window_cfg = build_value_window_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(value_window_cfg, 7, df=df_in, keys=("column",), ctx=ctx)

    # ── Build phase body ──────────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "groupby":                groupby_cfg,
        "conditional_aggregation": cond_agg_cfg,
        "running_total":          running_total_cfg,
        "ranking":                ranking_cfg,
        "lag_lead":               lag_lead_cfg,
        "advanced_ranking":       advanced_ranking_cfg,
        "value_windows":          value_window_cfg,
    }

    return wrap_phase("phase_5_aggregation", phase_body)