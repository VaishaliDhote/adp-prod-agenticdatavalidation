"""
execution/execution_phase_8_finalize.py
=========================================
PHASE 8 — Finalization  (DATA)
--------------------------------
Cleans up the DataFrame before formatting.

Operations:
  34. Formula → Value   : drop any lingering formula-string cells,
                          coerce to correct dtype
                          ← config["phase_9_final"]["formula_to_value"]
  35. Remove intermediate columns : drop temp/helper/staging columns
                          ← config["phase_9_final"]["remove_intermediate_columns"]
  36. Resequence serial columns   : re-number 1..N index columns whose values
                          became sparse after row filtering
                          ← config["phase_9_final"]["resequence"]
  37. Final column reorder : apply reorder_columns from phase_3 AFTER all
                          columns have been created (fixes ordering of
                          columns created in phase_4 enrichment)
                          ← config["phase_3_columns"]["reorder_columns"]

Config key : "phase_9_final"
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import pandas as pd

from utils.execution_helpers import col_name


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply finalization operations defined in config["phase_9_final"].

    Args:
        df     : DataFrame produced by Phase 7.
        config : Full merged config dict.

    Returns:
        Finalized DataFrame — static values, no intermediate columns.
    """
    phase          = config.get("phase_9_final", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 8] No finalization config found — skipping.")
        return df

    df = df.copy()

    # ── 34. Formula → Value ───────────────────────────────────────────────────
    f2v_cfg = phase.get("formula_to_value", {})
    if f2v_cfg.get("applied"):
        df = _apply_formula_to_value(df, f2v_cfg, on_missing_col)

    # ── 35. Remove intermediate columns ──────────────────────────────────────
    rm_cfg = phase.get("remove_intermediate_columns", {})
    if rm_cfg.get("applied"):
        df = _apply_remove_intermediate(df, rm_cfg, on_missing_col, full_config=config)

    # ── 36. Resequence serial / index columns ─────────────────────────────────
    # Must run LAST — after all row operations are done — so the final 1..N
    # sequence reflects the true output row count.
    reseq_cfg = phase.get("resequence", {})
    if reseq_cfg.get("applied"):
        df = _apply_resequence(df, reseq_cfg, on_missing_col)

    # ── 37. Final column reorder ─────────────────────────────────────────────
    # Re-apply column ordering from phase_3_columns.reorder_columns AFTER all
    # columns have been created (phase_4 calculated_columns, phase_5 aggregations,
    # etc.). This ensures columns like "Employee Count" which are created after
    # phase_3 end up in the correct position.
    df = _apply_final_reorder(df, config)

    print(f"[Phase 8] Finalization complete. Shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_FORMULA_RE   = re.compile(r"^\s*=", re.IGNORECASE)
_EXCEL_ERRORS = {"#ref!", "#n/a", "#value!", "#div/0!", "#name?", "#null!", "#num!"}

# Intermediate / helper column name hints (mirrors phase_9_final detection)
_INTERMEDIATE_HINTS = [
    "temp", "tmp", "helper", "calc", "intermediate", "staging",
    "stg", "work", "scratch", "raw_", "_raw", "draft", "wip",
    "interim", "aux", "flag_", "_flag", "lookup_", "_lookup",
    "step_", "_step", "partial", "debug", "prev_", "old_",
    "orig_", "copy_", "bak_", "__",
]


# ── 34. Formula → Value ───────────────────────────────────────────────────────

def _apply_formula_to_value(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Freeze formula-string cells to their computed values.

    For each column listed in config:
      • If the column contains Excel formula strings (starting with "="),
        attempt to evaluate them via pandas.eval / Python eval.
      • If the column contains Excel error sentinels (#N/A, #REF!, …),
        replace them with NaN.
      • Coerce the result to numeric where possible.
    """
    columns: List[Any] = cfg.get("details", {}).get("columns", [])
    cols = [col_name(c) for c in columns if col_name(c)]

    # If no columns explicitly listed, auto-detect formula-string columns
    if not cols:
        cols = _auto_detect_formula_cols(df)

    for col in cols:
        if col not in df.columns:
            if on_missing == "skip":
                print(f"  [Phase 8] Formula→Value: '{col}' not found — skipping.")
            continue

        series = df[col]

        # Replace Excel error sentinels with NaN
        str_s = series.astype(str).str.strip().str.lower()
        error_mask = str_s.isin(_EXCEL_ERRORS)
        if error_mask.any():
            df[col] = series.where(~error_mask, other=pd.NA)
            print(f"  [Phase 8] Cleared {error_mask.sum()} Excel errors in '{col}'")

        # Try numeric coercion (handles "$1,234", "12.5%", formula results)
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().mean() >= 0.80:
            df[col] = coerced
            print(f"  [Phase 8] Formula→Value coerced '{col}' to numeric")

    return df


def _auto_detect_formula_cols(df: pd.DataFrame) -> List[str]:
    """Find columns where ≥ 50% of non-null string values start with '='."""
    found = []
    for col in df.columns:
        if not (pd.api.types.is_object_dtype(df[col]) or
                pd.api.types.is_string_dtype(df[col])):
            continue
        non_null = df[col].dropna().astype(str)
        if len(non_null) == 0:
            continue
        formula_frac = non_null.map(lambda v: bool(_FORMULA_RE.match(v))).mean()
        if formula_frac >= 0.50:
            found.append(col)
    return found


# ── 35. Remove intermediate columns ──────────────────────────────────────────

def _apply_remove_intermediate(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
    full_config: Dict[str, Any] = None,
) -> pd.DataFrame:
    """
    Drop intermediate / helper / staging columns.

    Uses explicit column list from config first.
    Falls back to name-hint auto-detection for any columns not listed.

    Columns that are part of the final output (reorder_columns / select_columns
    in phase_3_columns, or groupby columns/aggregation outputs in phase_5)
    are NEVER dropped, even if listed in the config, to prevent misconfigured
    LLM-generated configs from wiping the output.
    """
    columns: List[Any] = cfg.get("details", {}).get("columns", [])
    explicit = [col_name(c) for c in columns if col_name(c)]

    # ── Build protected set from final output column definitions ──────────────
    protected: set = set()
    if full_config:
        # 1. Protect columns from phase_3_columns reorder/select
        p3 = full_config.get("phase_3_columns", {})
        for op_key in ("reorder_columns", "select_columns"):
            op_cfg = p3.get(op_key, {})
            if op_cfg.get("applied"):
                details = op_cfg.get("details", {})
                col_list = details.get("order") or details.get("columns") or []
                for c in col_list:
                    name = col_name(c)
                    if name:
                        protected.add(name)
        
        # 2. Protect groupby columns and aggregation output columns from phase_5
        p5 = full_config.get("phase_5_aggregation", {})
        groupby_cfg = p5.get("groupby", {})
        if groupby_cfg.get("applied"):
            rules = groupby_cfg.get("details", {}).get("rules", [])
            for rule in rules:
                # Protect group_by / group_columns
                group_cols = rule.get("group_columns") or rule.get("group_by") or []
                for gc in group_cols:
                    name = col_name(gc) if isinstance(gc, dict) else str(gc)
                    if name:
                        protected.add(name)
                # Protect aggregation output column names
                aggs = rule.get("aggregations", {})
                for out_col in aggs.keys():
                    protected.add(out_col)
        
        # 3. Protect unique columns from phase_7_advanced (these should be in output)
        p7 = full_config.get("phase_7_advanced", {})
        array_cfg = p7.get("array_formulas", {})
        if array_cfg.get("applied"):
            details = array_cfg.get("details", {})
            unique_on = details.get("unique", {}).get("unique_on", [])
            for c in unique_on:
                name = col_name(c)
                if name:
                    protected.add(name)
    
    print(f"  [Phase 8] Protected columns: {protected}")
    print(f"  [Phase 8] Current df columns: {list(df.columns)}")
    
    if protected:
        protected_skipped = [c for c in explicit if c in protected]
        if protected_skipped:
            print(
                f"  [Phase 8] Skipping removal of {len(protected_skipped)} "
                f"protected output columns: {protected_skipped}"
            )
        explicit = [c for c in explicit if c not in protected]

    # Auto-detect additional intermediate columns not already listed
    auto = [
        c for c in df.columns
        if c not in explicit and c not in protected and _looks_intermediate(c)
    ]

    to_drop = list(dict.fromkeys(explicit + auto))   # dedupe, preserve order
    to_drop = [c for c in to_drop if c in df.columns]

    if to_drop:
        df = df.drop(columns=to_drop)
        print(f"  [Phase 8] Removed intermediate columns: {to_drop}")
    else:
        print("  [Phase 8] No intermediate columns to remove.")

    return df


def _looks_intermediate(col_name: str) -> bool:
    n = re.sub(r"[^a-z0-9]+", "_", col_name.lower()).strip("_")
    return any(hint.strip("_") in n for hint in _INTERMEDIATE_HINTS)


# ── 36. Resequence serial / index columns ─────────────────────────────────────

def _apply_resequence(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Re-number serial / index columns to a clean 1..N sequence.

    For each rule in config:
      column   : column to resequence (name or column object with "name" key)
      start_at : first value (default 1)
      step     : increment (default 1)

    Resolution order when the column name is not found in df:
      1. Exact name match
      2. Case-insensitive name match
      3. Aliases list match  (column object may carry "aliases": [...])
         — also tries normalized (lower + collapse non-alphanum) comparison
           so "SL No" matches alias "sl_no" or "sl no" etc.
      4. Input-name fallback via aliases that look like serial-index column
         names (e.g. "sl_no", "sr_no", "sno", "index", "id", "no", "num")
      5. Position fallback — scans ALL numeric columns for the one that
         looks most like a serial index (has gaps / is sparse after filtering)
         rather than blindly picking column index 0.
    """
    rules: List[Any] = cfg.get("details", {}).get("rules", [])

    # Ensure a clean 0-based index before any assignment so list → series
    # alignment is always positional (not label-based).
    df = df.reset_index(drop=True)

    # Pre-build a lower-map and a normalised-name map once for all rules.
    lower_map  = {c.lower(): c for c in df.columns}
    norm_map   = {re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_"): c
                  for c in df.columns}

    for rule in rules:
        col_ref  = rule.get("column", "")
        col      = col_name(col_ref)
        start_at = int(rule.get("start_at", 1))
        step     = int(rule.get("step", 1))

        if not col:
            continue

        # ── 1. Exact match ────────────────────────────────────────────────────
        resolved = col if col in df.columns else None

        # ── 2. Case-insensitive match ─────────────────────────────────────────
        if resolved is None:
            resolved = lower_map.get(col.lower())

        # ── 3. Normalised + alias match ───────────────────────────────────────
        if resolved is None:
            # Normalise the target name itself
            col_norm = re.sub(r"[^a-z0-9]+", "_", col.lower()).strip("_")
            resolved = norm_map.get(col_norm)

        if resolved is None and isinstance(col_ref, dict):
            aliases = col_ref.get("aliases", [])
            for alias in aliases:
                alias_str  = str(alias)
                # exact
                candidate  = lower_map.get(alias_str.lower())
                if not candidate:
                    # normalised
                    alias_norm = re.sub(r"[^a-z0-9]+", "_", alias_str.lower()).strip("_")
                    candidate  = norm_map.get(alias_norm)
                if candidate:
                    resolved = candidate
                    print(f"  [Phase 8] Resequence: '{col}' resolved via alias "
                          f"'{alias_str}' → '{resolved}'")
                    break

        # ── 4. Serial-hint name scan ──────────────────────────────────────────
        # Covers renamed columns: output name is "Employee Count" but df still
        # has "SL No" / "Sr No" / "Index" etc. when no rename phase ran.
        if resolved is None:
            _SERIAL_HINTS = {
                "sl_no", "slno", "sr_no", "srno", "sno", "s_no",
                "serial", "serial_no", "serial_number", "seq", "sequence",
                "row_no", "rowno", "row_num", "rownum", "row_number",
                "index", "idx", "no", "num", "number", "id",
                "rank", "order", "pos", "position",
            }
            for df_col_norm, df_col in norm_map.items():
                if df_col_norm in _SERIAL_HINTS and pd.api.types.is_numeric_dtype(df[df_col]):
                    resolved = df_col
                    print(f"  [Phase 8] Resequence: '{col}' not found — "
                          f"serial-hint match on '{df_col}' (normalised: '{df_col_norm}')")
                    break

        # ── 5. Position fallback — first numeric column that has gaps ─────────
        # Scan ALL numeric columns; prefer the one that is sparse (has gaps
        # after filtering) rather than the first numeric column blindly.
        if resolved is None:
            best_col   = None
            best_score = -1          # higher = more evidence of serial index

            for df_col in df.columns:
                if not pd.api.types.is_numeric_dtype(df[df_col]):
                    continue
                vals = pd.to_numeric(df[df_col], errors="coerce").dropna()
                if len(vals) < 2:
                    continue
                float_vals = vals.astype(float)
                # Must be integer-valued
                if not (float_vals % 1 == 0).all():
                    continue
                int_vals = float_vals.astype(int)
                mn, mx   = int(int_vals.min()), int(int_vals.max())
                # Must start at 0 or 1
                if mn not in (0, 1):
                    continue
                # Must be unique
                if int_vals.nunique() != len(int_vals):
                    continue
                # max must be ≤ 2 × row count (reject phone numbers / IDs)
                if mx > len(int_vals) * 2:
                    continue

                # Score: gap presence (sparse → needs resequence) beats clean seq
                expected = list(range(mn, mn + len(int_vals)))
                actual   = sorted(int_vals.tolist())
                has_gaps = actual != expected
                score    = 2 if has_gaps else 1   # sparse column scores higher

                if score > best_score:
                    best_score = score
                    best_col   = df_col

            if best_col is not None:
                print(f"  [Phase 8] Resequence: '{col}' not found by name — "
                      f"position fallback resolved to '{best_col}' (score={best_score})")
                resolved = best_col
            else:
                if on_missing == "skip":
                    print(f"  [Phase 8] Resequence: '{col}' not found — skipping.")
                continue

        n        = len(df)
        new_vals = list(range(start_at, start_at + n * step, step))
        df[resolved] = new_vals
        print(f"  [Phase 8] Resequenced '{resolved}': "
              f"{new_vals[:3]}...{new_vals[-1]}  "
              f"(start={start_at}, step={step}, n={n})")

    return df


# ── 37. Final column reorder ──────────────────────────────────────────────────

def _apply_final_reorder(
    df: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Re-apply column ordering from phase_3_columns.reorder_columns AFTER all
    columns have been created. This is necessary because:

    1. Phase 3 runs reorder_columns BEFORE Phase 4 creates calculated columns
    2. Columns created in Phase 4 (e.g., "Employee Count") get appended at the end
    3. This final reorder puts them in the correct position

    Reads the desired order from config["phase_3_columns"]["reorder_columns"]["details"]["order"]
    and applies it to the current DataFrame columns.
    """
    reorder_cfg = config.get("phase_3_columns", {}).get("reorder_columns", {})
    if not reorder_cfg.get("applied"):
        return df

    order: List[Any] = reorder_cfg.get("details", {}).get("order", [])
    if not order:
        return df

    desired = [col_name(c) for c in order if col_name(c)]

    # Deduplicate while preserving first-occurrence order
    seen: set = set()
    desired_deduped: List[str] = []
    for c in desired:
        if c not in seen:
            seen.add(c)
            desired_deduped.append(c)
    desired = desired_deduped

    # Now all columns should exist - filter to only those that do
    existing_ordered = [c for c in desired if c in df.columns]
    remaining = [c for c in df.columns if c not in existing_ordered]

    # Append any columns not mentioned in the order list at the end
    final_order = existing_ordered + remaining

    # Only reorder if it actually changes something
    if list(df.columns) != final_order:
        df = df[final_order]
        print(f"  [Phase 8] Final column reorder: {final_order}")
    else:
        print(f"  [Phase 8] Final column reorder: columns already in correct order")

    return df
