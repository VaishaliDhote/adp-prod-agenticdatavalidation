# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 1 – Structural Transformation Detection
==============================================
Delegates detection to the four focused utils:

  structural_transpose  → transpose config block
  structural_pivot      → pivot config block
  structural_unpivot    → unpivot config block
  structural_hierarchy  → hierarchy_transform config block

Production-safety upgrades applied here:
  ✅ Mutually-exclusive execution field (pivot > unpivot > transpose)
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ metadata.intermediate_columns tracking
  ✅ Rule-level execution_order (priority) stamps
  ✅ Column object references via make_column_object()
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.structural_transpose import build_transpose_config
from utils.transformations.structural_pivot     import build_pivot_config
from utils.transformations.structural_unpivot   import build_unpivot_config
from utils.transformations.structural_hierarchy import build_hierarchy_config
from typing import Optional as _Opt  # noqa: F401 (used in ctx type hint)
from utils.config_utils import (
    add_rule_priority,
    resolve_active_structural_op,
    wrap_phase,
)


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, Any]] = None,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all structural transformations between df_in and df_out.

    Args:
        df_in:          Input DataFrame.
        df_out:         Output DataFrame.
        column_mapping: Optional top-level mapping dict with optional sub-keys:
                        {
                          "pivot":     { "index": ..., "columns": ...,
                                         "values": ..., "aggregation": ... },
                          "unpivot":   { "id_columns": ..., "value_columns": ...,
                                         "var_name": ..., "value_name": ... },
                          "hierarchy": { "type": "flatten" | "create_hierarchical" }
                        }

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_1_structure": {
            "execution": "pivot"|"unpivot"|"transpose"|"none",
            "transpose":           { "applied": bool, "details": {...} },
            "pivot":               { "applied": bool, "details": {"rules": [...]} },
            "unpivot":             { "applied": bool, "details": {"rules": [...]} },
            "hierarchy_transform": { "applied": bool, "details": {...} }
          },
          "metadata": { "phase": "phase_1_structure",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}

    # ── 1. Transpose ──────────────────────────────────────────────────────────
    transpose_config = build_transpose_config(
        input_df=df_in,
        output_df=df_out,
    )

    # ── 2. Pivot ──────────────────────────────────────────────────────────────
    pivot_config = build_pivot_config(
        input_df=df_in,
        output_df=df_out,
        column_mapping=cm.get("pivot"),
    )
    # Add rule priorities
    if pivot_config.get("details", {}).get("rules"):
        add_rule_priority(pivot_config["details"]["rules"])

    # ── 3. Unpivot ────────────────────────────────────────────────────────────
    unpivot_config = build_unpivot_config(
        input_df=df_in,
        output_df=df_out,
        column_mapping=cm.get("unpivot"),
    )
    if unpivot_config.get("details", {}).get("rules"):
        add_rule_priority(unpivot_config["details"]["rules"])

    # ── 4. Hierarchy ──────────────────────────────────────────────────────────
    hierarchy_config = build_hierarchy_config(
        input_df=df_in,
        output_df=df_out,
        column_mapping=cm.get("hierarchy"),
    )

    # ── 5. Resolve mutually-exclusive structural operation ────────────────────
    active_op = resolve_active_structural_op(
        transpose_config, pivot_config, unpivot_config
    )

    # ── 6. Build phase body ───────────────────────────────────────────────────
    phase_body: Dict[str, Any] = {
        "execution":           active_op,   # ✅ mutually-exclusive flag
        "transpose":           transpose_config,
        "pivot":               pivot_config,
        "unpivot":             unpivot_config,
        "hierarchy_transform": hierarchy_config,
    }

    # ── 7 & 8. Wrap with shared metadata ─────────────────────────────────────
    return wrap_phase("phase_1_structure", phase_body)