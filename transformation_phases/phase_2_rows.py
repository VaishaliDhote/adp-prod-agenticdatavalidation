# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection phase). REUSABLE.
# Rework: emit rule inventory + API mapping/unsupported flags + source/target UUIDs.
# ============================================================================
"""
Phase 2 – Row Operations Detection
====================================
Production-safety upgrades applied here:
  ✅ Global preprocessing block
  ✅ Canonical execution_order list
  ✅ Error-handling block
  ✅ Rule-level execution_order stamps (filter → dedup → sort)
  ✅ Column object references on every rule
  ✅ metadata.intermediate_columns tracking
  ✅ already_aggregated safety flag on subtotals
"""

from typing import Any, Dict, Optional
import pandas as pd

from utils.transformations.row_filter_value import build_value_filter_config
from utils.transformations.row_filter_text  import build_text_filter_config
from utils.transformations.row_sort         import build_sort_config
from utils.transformations.row_dedup        import build_dedup_config
from utils.transformations.row_subtotals    import build_subtotals_config
from utils.config_utils import (
    upgrade_op,
    wrap_phase,
)


def detect(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    verbose: bool = False,
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Detect all row-level transformations between df_in and df_out.

    Returns production-safe config:
        {
          "preprocessing": {...},
          "execution_order": [...],
          "error_handling": {...},
          "phase_2_rows": {
            "filter":            { "applied": bool, "execution_order": 1,
                                   "details": { "rules": [...] } },
            "remove_duplicates": { "applied": bool, "execution_order": 2,
                                   "details": { "rules": [...] } },
            "sort":              { "applied": bool, "execution_order": 3,
                                   "details": { "rules": [...] } },
            "subtotal_rows":     { "applied": bool, "execution_order": 4,
                                   "details": { "rules": [...] } }
          },
          "metadata": { "phase": "phase_2_rows",
                        "intermediate_columns": [...] }
        }
    """
    cm = column_mapping or {}
    print(f"Starting detect: df_in shape {df_in.shape}, df_out shape {df_out.shape}, column_mapping {cm}")

    # ══════════════════════════════════════════════════════════════════════════
    # IMPORTANT: Detect DEDUP FIRST, then only detect filters if dedup doesn't
    # fully explain the row reduction. This prevents false-positive filters
    # like "SaleID != 101" when the actual logic is "dedup on Product, keep max Price".
    # ══════════════════════════════════════════════════════════════════════════

    # ── 4. Remove duplicates (DETECT FIRST) ───────────────────────────────────
    print("Building dedup config")
    dedup_cfg = build_dedup_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(dedup_cfg, 2, df=df_in, keys=("columns",), list_key="rules", ctx=ctx)
    dedup_applied = dedup_cfg.get('applied', False)
    print(f"Dedup cfg: applied={dedup_applied}, rules={len(dedup_cfg['details']['rules']) if dedup_cfg.get('details', {}).get('rules') else 0}")

    # ── Check if dedup explains the row reduction ─────────────────────────────
    row_reduction = len(df_in) - len(df_out)
    dedup_explains_reduction = False
    df_after_dedup: pd.DataFrame = df_in   # will be updated to post-dedup state if dedup confirmed

    if dedup_applied and row_reduction > 0:
        # Simulate dedup to see if it explains the row count difference
        for rule in dedup_cfg.get('details', {}).get('rules', []):
            cols = rule.get('columns', [])
            keep = rule.get('keep', 'first')
            
            # Extract column names from column objects if needed
            col_names = []
            for c in cols:
                if isinstance(c, dict):
                    col_names.append(c.get('name', c))
                else:
                    col_names.append(c)
            
            # Map output column names back to input column names
            input_col_names = []
            for col_name in col_names:
                # Find the input column name
                if cm:
                    # cm is {output_col: input_col}
                    input_col_names.append(cm.get(col_name, col_name))
                else:
                    input_col_names.append(col_name)
            
            # Check if all columns exist in input
            valid_cols = [c for c in input_col_names if c in df_in.columns]
            if valid_cols and len(valid_cols) == len(input_col_names):
                try:
                    if keep in ('first', 'last'):
                        after_dedup = df_in.drop_duplicates(subset=valid_cols, keep=keep)
                    elif keep.startswith('max:'):
                        keep_col = keep.split(':', 1)[1]
                        # Map keep_col from output to input namespace
                        if cm:
                            keep_col = cm.get(keep_col, keep_col)
                        if keep_col in df_in.columns:
                            idx = df_in.groupby(valid_cols, sort=False)[keep_col].idxmax()
                            after_dedup = df_in.loc[sorted(idx)]  # preserve original row order
                        else:
                            continue
                    elif keep.startswith('min:'):
                        keep_col = keep.split(':', 1)[1]
                        if cm:
                            keep_col = cm.get(keep_col, keep_col)
                        if keep_col in df_in.columns:
                            idx = df_in.groupby(valid_cols, sort=False)[keep_col].idxmin()
                            after_dedup = df_in.loc[sorted(idx)]  # preserve original row order
                        else:
                            continue
                    else:
                        after_dedup = df_in.drop_duplicates(subset=valid_cols, keep='first')
                    
                    if len(after_dedup) == len(df_out):
                        dedup_explains_reduction = True
                        df_after_dedup = after_dedup.reset_index(drop=True)
                        print(f"  Dedup fully explains row reduction: {len(df_in)} → {len(after_dedup)}")
                        break
                except Exception as e:
                    print(f"  Error checking dedup: {e}")
                    pass

    # ── 1. Value filters (only if dedup doesn't explain the reduction) ────────
    if dedup_explains_reduction:
        print("Skipping filter detection - dedup explains row reduction")
        value_filter_cfg = {"applied": False, "details": {"rules": []}}
    else:
        print("Building value filter config")
        value_filter_cfg = build_value_filter_config(
            input_df=df_in, output_df=df_out,
            column_mapping=cm or None, verbose=verbose,
        )
    print(f"Value filter cfg: applied={value_filter_cfg.get('applied')}, rules={len(value_filter_cfg['details']['rules'])}")

    # ── 2. Text filters ───────────────────────────────────────────────────────
    if dedup_explains_reduction:
        text_filter_cfg = {"applied": False, "details": {"rules": []}}
    else:
        print("Building text filter config")
        text_filter_cfg = build_text_filter_config(
            input_df=df_in, output_df=df_out,
            column_mapping=cm or None, verbose=verbose,
        )
    print(f"Text filter cfg: applied={text_filter_cfg.get('applied')}, rules={len(text_filter_cfg['details']['rules'])}")

    # ── 3. Merge filter rules & upgrade column refs ───────────────────────────
    print("Merging filter rules")
    merged_rules = (
        value_filter_cfg["details"]["rules"]
        + text_filter_cfg["details"]["rules"]
    )
    print(f"Merged rules count: {len(merged_rules)}")
    filter_cfg = {
        "applied":         len(merged_rules) > 0,
        "details":         {"rules": merged_rules},
    }
    upgrade_op(filter_cfg, 1, df=df_in, keys=("column",), ctx=ctx)
    print(f"Filter cfg: applied={filter_cfg['applied']}")

    # ── 5. Sort ───────────────────────────────────────────────────────────────
    # Use the post-dedup state as the sort reference when dedup was applied.
    # This way _col_order_changed compares post-dedup row order vs output row
    # order, rather than comparing the raw 5-row input to the 4-row output.
    print("Building sort config")
    sort_cfg = build_sort_config(
        input_df=df_after_dedup, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(sort_cfg, 3, df=df_in, keys=("column",), ctx=ctx)
    print(f"Sort cfg: applied={sort_cfg.get('applied')}, rules={len(sort_cfg['details']['rules']) if sort_cfg.get('details', {}).get('rules') else 0}")

    # ── 6. Subtotal rows ──────────────────────────────────────────────────────
    print("Building subtotals config")
    subtotals_cfg = build_subtotals_config(
        input_df=df_in, output_df=df_out, column_mapping=cm or None,
    )
    upgrade_op(subtotals_cfg, 4, df=df_in, keys=("column", "group_by"), ctx=ctx)
    print(f"Subtotals cfg: applied={subtotals_cfg.get('applied')}, rules={len(subtotals_cfg['details']['rules']) if subtotals_cfg.get('details', {}).get('rules') else 0}")

    # ── 7. Build phase body ───────────────────────────────────────────────────
    print("Building phase body")
    phase_body: Dict[str, Any] = {
        "filter":            filter_cfg,
        "remove_duplicates": dedup_cfg,
        "sort":              sort_cfg,
        "subtotal_rows":     subtotals_cfg,
    }

    result = wrap_phase("phase_2_rows", phase_body)
    print("Returning result")
    return result