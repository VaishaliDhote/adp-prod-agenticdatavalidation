# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation Rule Identification (detection orchestrator). REUSABLE.
# Rework: emit a transformation RULE INVENTORY (not bespoke YAML phase-config);
# add supported-API mapping + unsupported-flagging (tasks 14-16) and source/target
# column UUIDs (tasks 18-19). Detection only - no data execution.
# ============================================================================
"""
phases/main.py
==============
Unified phase orchestrator.

Runs all nine detection phases in the canonical execution order and merges
their outputs into a single, production-safe config document that contains:

  • Global preprocessing block          (top-level, shared by all phases)
  • Canonical execution_order list      (top-level)
  • Global error_handling block         (top-level)
  • Per-phase blocks (phase_1_structure … phase_9_final)
  • Merged top-level metadata block
      – intermediate_columns  deduped across all phases
      – phase_summary         applied/skipped status for every phase

Architecture
------------
  1. Reverse-engineer  → detect_all()  produces the raw+upgraded config
  2. Normalise config  → already done inside each phase (column objects, etc.)
  3. Validate config   → (LLM or rule engine – external to this file)
  4. Apply transforms  → transformation_applier_service reads this output
  5. Validate output   → compare with expected sheet
"""

from typing import Any, Dict, List, Optional
import os
from pathlib import Path
import yaml
import pandas as pd
import traceback

# ── Phase imports ─────────────────────────────────────────────────────────────
from transformation_phases.phase_1_structure  import detect as p1
from transformation_phases.phase_2_rows       import detect as p2
from transformation_phases.phase_3_columns    import detect as p3
from transformation_phases.phase_4_enrichment import detect as p4
from transformation_phases.phase_5_aggregation import detect as p5
from transformation_phases.phase_6_cleaning   import detect as p6
from transformation_phases.phase_7_advanced   import detect as p7
from transformation_phases.phase_8_formatting import detect as p8
from transformation_phases.phase_9_final      import detect as p9

# ── Shared config constants ───────────────────────────────────────────────────
from utils.config_utils import (
    PREPROCESSING_BLOCK,
    EXECUTION_ORDER,
    ERROR_HANDLING_BLOCK,
    collect_intermediate_columns,
)
from utils.serializer import make_serializable
from utils.transformations import column_mapping
from utils.transformations.column_mapping import detect_column_mapping
from utils.phase_context import PhaseContext


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _phase_is_active(phase_dict: Dict[str, Any], phase_key: str) -> bool:
    """Return True when at least one operation inside a phase is applied."""
    body = phase_dict.get(phase_key, {})
    for op_cfg in body.values():
        if isinstance(op_cfg, dict) and op_cfg.get("applied") is True:
            return True
    return False


def _build_phase_summary(results: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """
    Return a dict mapping each phase key → "active" | "skipped".
    Used in the top-level metadata block.
    """
    summary: Dict[str, str] = {}
    for phase_key in EXECUTION_ORDER:
        if phase_key not in results:
            summary[phase_key] = "skipped"
            continue
        summary[phase_key] = (
            "active" if _phase_is_active(results[phase_key], phase_key)
            else "skipped"
        )
    return summary


def _merge_intermediate_columns(results: Dict[str, Dict[str, Any]]) -> List[str]:
    """Collect and deduplicate intermediate_columns across all phase metadata."""
    seen = set()
    merged: List[str] = []
    for phase_result in results.values():
        cols = phase_result.get("metadata", {}).get("intermediate_columns", [])
        for c in cols:
            name = c if isinstance(c, str) else c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                merged.append(name)
    # Also do a deep scan of all phase bodies for additional intermediate names
    deep_found = collect_intermediate_columns(results)
    for c in deep_found:
        if c not in seen:
            seen.add(c)
            merged.append(c)
    return sorted(merged)


def _apply_detected_filters(
    df: pd.DataFrame,
    phase_2_result: Dict[str, Any],
) -> pd.DataFrame:
    """
    Apply the row-filter rules detected in phase_2 back onto df so that
    downstream phases (enrichment, aggregation, etc.) see the same reduced
    set of rows that the output DataFrame contains.

    Only numeric / string equality and comparison operators are supported
    here because those are the only operators emitted by the value-filter
    and text-filter detectors.

    Unrecognised operators are silently skipped (safe degradation).
    """
    filter_rules = (
        phase_2_result
        .get("phase_2_rows", {})
        .get("filter", {})
        .get("details", {})
        .get("rules", [])
    )

    if not filter_rules:
        return df

    result = df.copy()

    for rule in filter_rules:
        # column may be a string or a column-object dict
        col_info = rule.get("column", "")
        col = col_info if isinstance(col_info, str) else col_info.get("name", "")
        operator = rule.get("operator", "")
        value = rule.get("value")

        if not col or not operator or col not in result.columns:
            continue

        try:
            if operator == "==":
                result = result[result[col] == value]
            elif operator == "!=":
                result = result[result[col] != value]
            elif operator == ">":
                result = result[result[col] > value]
            elif operator == "<":
                result = result[result[col] < value]
            elif operator == ">=":
                result = result[result[col] >= value]
            elif operator == "<=":
                result = result[result[col] <= value]
            elif operator in ("contains", "startswith", "endswith"):
                col_str = result[col].astype(str)
                if operator == "contains":
                    result = result[col_str.str.contains(str(value), na=False)]
                elif operator == "startswith":
                    result = result[col_str.str.startswith(str(value), na=False)]
                elif operator == "endswith":
                    result = result[col_str.str.endswith(str(value), na=False)]
            # Unknown operators are skipped
        except Exception as exc:
            print(f"[detect_all] ⚠️  Could not apply filter '{col} {operator} {value}': {exc}")
            continue

    return result.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_all(
    df_in: pd.DataFrame,
    df_out: pd.DataFrame,
    column_mapping: column_mapping,
    run_formatting: bool = True,
    input_file_path: Optional[str] = None,
    df_in_full: Optional[pd.DataFrame] = None,  # ← original unrenamed+unfiltered df_in
) -> Dict[str, Any]:
    """
    Run all detection phases and return a single, production-safe config.

    Args:
        df_in:           Input DataFrame (raw / before transformation).
        df_out:          Output DataFrame (after transformation).
        column_mapping:  Optional top-level column mapping dict forwarded to
                         every phase.  Sub-keys recognised by individual phases
                         (e.g. "pivot", "unpivot", "hierarchy") are extracted
                         automatically.
        run_formatting:  Set False to skip phase_8_formatting (faster, useful
                         when output is headless / CSV).
        input_file_path: Optional path to the input file. When provided, the
                         merged config is saved to
                         data/generated_configs/<stem>_config.yaml before
                         being returned.
        df_in_full:      Original unrenamed+unfiltered df_in.

    Returns:
        {
          "preprocessing":   { ... },   ← global, top-level
          "execution_order": [ ... ],   ← canonical phase order
          "error_handling":  { ... },   ← global error policy
          "phase_1_structure":  { ... },
          "phase_2_rows":       { ... },
          "phase_3_columns":    { ... },
          "phase_4_enrichment": { ... },
          "phase_5_aggregation":{ ... },
          "phase_6_cleaning":   { ... },
          "phase_7_advanced":   { ... },
          "phase_8_formatting": { ... },   ← omitted when run_formatting=False
          "phase_9_final":      { ... },
          "metadata": {
            "intermediate_columns": [...],   ← deduped across all phases
            "phase_summary":        {        ← active / skipped per phase
              "phase_1_structure":  "active"|"skipped",
              ...
            }
          }
        }
    """
    # ── Fix transposed output (same logic as detect_column_mapping) ──────────
    first_col = df_out.columns[0]
    if str(first_col) == "Unnamed: 0" and all(isinstance(c, int) for c in df_out.columns[1:]):
        df_out = df_out.set_index(first_col).T.reset_index(drop=True)
        df_out.columns.name = None
        print(f"[detect_all] ⚠️  Transposed output detected — un-transposed. New columns: {df_out.columns.tolist()}")

    # ── Run every phase ───────────────────────────────────────────────────────
    raw: Dict[str, Dict[str, Any]] = {}

    print("\n[detect_all] 🔍 Starting phase detection...")
    print(f"  df_in  columns: {list(df_in.columns)}")
    print(f"  df_out columns: {list(df_out.columns)}")
    print(f"  column_mapping: {column_mapping}\n")

    # ── Build shared phase context (column objects computed once for all phases)
    ctx = PhaseContext.build(df_in, df_out, column_mapping=column_mapping)

    print("[detect_all] Running phase_1_structure...")
    try:
        raw["phase_1_structure"] = p1(df_in, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_1_structure completed",raw["phase_1_structure"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_1_structure FAILED: {e}")
        raise

    print("[detect_all] Running phase_2_rows...")
    try:
        raw["phase_2_rows"] = p2(df_in, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_2_rows completed",raw["phase_2_rows"])
    except Exception as e:
        print(f"[detect_all] ❌ phase_2_rows FAILED: {e}")
        raise

    # Apply detected filters to df_in to produce df_in_filtered.
    # Downstream phases (3–9) receive this narrowed DataFrame so they compare
    # apples-to-apples against df_out and don't produce false-positive
    # enrichment / aggregation detections caused by rows removed by filters.
    df_in_filtered = _apply_detected_filters(df_in, raw)

    # ── Convergence diagnostic ─────────────────────────────────────────────
    # The filtered df should have >= df_out rows (aggregation may reduce further).
    # If filtered rows < df_out rows, the filter over-fired.
    # If filtered rows >> df_out rows, filters are likely incomplete — warn so
    # downstream agg verification uses wrong group members.
    _n_filtered = len(df_in_filtered)
    _n_out      = len(df_out)
    _n_raw      = len(df_in)

    if _n_filtered == _n_raw:
        print("[detect_all] ℹ️  No row filters applied — phases 3-9 use full df_in")
    elif _n_filtered < _n_out:
        print(
            f"[detect_all] ⚠️  Over-filtered: df_in_filtered ({_n_filtered} rows) "
            f"< df_out ({_n_out} rows). A filter rule may be too aggressive."
        )
    elif _n_filtered == _n_out:
        print(
            f"[detect_all] ✅ Perfect filter convergence: "
            f"{_n_raw} → {_n_filtered} rows (matches df_out exactly, no aggregation)"
        )
    else:
        # _n_filtered > _n_out — normal when aggregation follows filtering
        reduction_pct = round((_n_raw - _n_filtered) / _n_raw * 100, 1)
        remaining_pct = round((_n_filtered - _n_out)  / _n_filtered * 100, 1)
        print(
            f"[detect_all] 🔀 Filters applied: {_n_raw} → {_n_filtered} rows "
            f"({reduction_pct}% removed). "
            f"Aggregation will reduce a further {_n_filtered - _n_out} rows "
            f"({remaining_pct}% of filtered). "
            f"Phases 3-9 use the filtered DataFrame."
        )
        if _n_filtered > _n_out * 3:
            print(
                f"[detect_all] ⚠️  Large gap between filtered ({_n_filtered}) and "
                f"output ({_n_out}) rows. Check whether all filter rules were detected — "
                f"incomplete filters cause wrong group members in aggregation verification."
            )

    print("[detect_all] Running phase_3_columns...")
    try:
        raw["phase_3_columns"] = p3(df_in_filtered, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_3_columns completed",raw["phase_3_columns"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_3_columns FAILED: {e}")
        traceback.print_exc()
        raise

    print("[detect_all] Running phase_4_enrichment...")
    try:
        raw["phase_4_enrichment"] = p4(df_in_filtered, df_out, column_mapping= column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_4_enrichment completed",raw["phase_4_enrichment"])
    except Exception as e:
        print(f"[detect_all] ❌ phase_4_enrichment FAILED: {e}")
        raise

    print("[detect_all] Running phase_5_aggregation...")
    try:
        raw["phase_5_aggregation"] = p5(df_in_filtered, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_5_aggregation completed",raw["phase_5_aggregation"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_5_aggregation FAILED: {e}")
        raise

    print("[detect_all] Running phase_6_cleaning...")
    try:
        raw["phase_6_cleaning"] = p6(df_in_filtered, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_6_cleaning completed",raw["phase_6_cleaning"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_6_cleaning FAILED: {e}")
        raise

    print("[detect_all] Running phase_7_advanced...")
    try:
        raw["phase_7_advanced"] = p7(df_in_filtered, df_out, column_mapping=column_mapping, ctx=ctx)
        print("[detect_all] ✅ phase_7_advanced completed",raw["phase_7_advanced"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_7_advanced FAILED: {e}")
        raise

    if run_formatting:
        print("[detect_all] Running phase_8_formatting...")
        try:
            raw["phase_8_formatting"] = p8(df_in_filtered, df_out, column_mapping=column_mapping, ctx=ctx)
            print("[detect_all] ✅ phase_8_formatting completed",raw["phase_8_formatting"] )
        except Exception as e:
            print(f"[detect_all] ❌ phase_8_formatting FAILED: {e}")
            raise

    print("[detect_all] Running phase_9_final...")
    try:
        raw["phase_9_final"] = p9(
            df_in_filtered,
            df_out,
            column_mapping=column_mapping,
            df_in_full=df_in,   # ← pass original unfiltered df_in for resequence detection
            ctx=ctx,
        )
        print("[detect_all] ✅ phase_9_final completed",raw["phase_9_final"] )
    except Exception as e:
        print(f"[detect_all] ❌ phase_9_final FAILED: {e}")
        raise

    print("\n[detect_all] 🎉 All phases completed successfully!")

    # ── Build merged top-level metadata ──────────────────────────────────────
    top_metadata: Dict[str, Any] = {
        "intermediate_columns": _merge_intermediate_columns(raw),
        "phase_summary":        _build_phase_summary(raw),
        # Persist the full input→output column mapping so execution phases
        # (Phase 3, Phase 9, Phase 10) can apply correct output header names
        # even when no explicit rename rule was detected (e.g. the column names
        # differ but rename detection was not triggered).
        "column_mapping": {k: v for k, v in column_mapping.items() if not k.startswith("__")},
    }

    # ── Assemble final output ─────────────────────────────────────────────────
    # Top-level blocks come first; then each phase body (extracted from its
    # per-phase wrapper so the final doc is flat and non-redundant).
    output: Dict[str, Any] = {
        "preprocessing":   PREPROCESSING_BLOCK,
        "execution_order": EXECUTION_ORDER,
        "error_handling":  ERROR_HANDLING_BLOCK,
    }

    for phase_key, phase_result in raw.items():
        # Each phase wraps its body under its own key; extract just that body.
        output[phase_key] = phase_result.get(phase_key, phase_result)

    output["metadata"] = top_metadata

    # ── Persist config to data/generated_configs/ ─────────────────────────────
    if input_file_path:
        stem = Path(input_file_path).stem
        configs_dir = Path(__file__).resolve().parent.parent / "data" / "generated_configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        config_path = configs_dir / f"{stem}_config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                make_serializable(output),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    return output