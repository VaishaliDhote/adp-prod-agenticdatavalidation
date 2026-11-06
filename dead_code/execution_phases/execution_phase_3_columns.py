"""
execution/execution_phase_3_columns.py
========================================
PHASE 3 — Column Operations
-----------------------------
CRITICAL ORDER (respects name dependencies):
  13. Rename columns    ← FIRST — names must be correct before select/reorder
  14. Split columns     ← config["phase_3_columns"]["split_columns"]
  15. Merge columns     ← config["phase_3_columns"]["merge_columns"]
  16. Select columns    ← config["phase_3_columns"]["select_columns"]
  17. Reorder columns   ← config["phase_3_columns"]["reorder_columns"]

Config key : "phase_3_columns"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from utils.execution_helpers import col_name, col_exists


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply all column operations defined in config["phase_3_columns"].

    Args:
        df     : DataFrame produced by Phase 2.
        config : Full merged config dict.

    Returns:
        DataFrame with columns renamed, split, merged, selected, and reordered.
    """
    phase          = config.get("phase_3_columns", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 3] No column-ops config found — skipping.")
        return df

    df = df.copy()

    # ── Check if aggregation is enabled (columns will be created later) ───────
    agg_phase = config.get("phase_5_aggregation", {})
    groupby_cfg = agg_phase.get("groupby", {})
    aggregation_enabled = groupby_cfg.get("applied", False) and not groupby_cfg.get("details", {}).get("already_aggregated", False)
    
    # Get aggregation source columns if aggregation is enabled
    agg_source_columns = set()
    if aggregation_enabled:
        rules = groupby_cfg.get("details", {}).get("rules", [])
        for rule in rules:
            # Add group columns
            for gc in rule.get("group_columns", []):
                agg_source_columns.add(gc)
            # Add aggregation source columns
            for out_col, agg_info in rule.get("aggregations", {}).items():
                src_col = agg_info.get("agg_column")
                if src_col:
                    agg_source_columns.add(src_col)
        if agg_source_columns:
            print(f"[Phase 3] Aggregation enabled — will preserve source columns: {sorted(agg_source_columns)}")

    # ── 13. Rename FIRST (critical — all downstream ops use new names) ────────
    rename_cfg = phase.get("rename_columns", {})
    rename_applied = bool(rename_cfg.get("applied"))
    # Build {input_name → output_name} for post-rename name translation.
    # Mapping in YAML is stored as {output_name: input_name}.
    rename_in_to_out: Dict[str, str] = {}
    if rename_applied:
        for new_name, old_name in (rename_cfg.get("details", {}).get("mappings") or {}).items():
            if isinstance(old_name, str) and isinstance(new_name, str):
                rename_in_to_out[old_name] = new_name
        df = _apply_rename(df, rename_cfg, on_missing_col)

    # ── 14. Split columns ─────────────────────────────────────────────────────
    split_cfg = phase.get("split_columns", {})
    if split_cfg.get("applied"):
        df = _apply_split(df, split_cfg, on_missing_col)

    # ── 15. Merge / concatenate columns ───────────────────────────────────────
    merge_cfg = phase.get("merge_columns", {})
    if merge_cfg.get("applied"):
        df = _apply_merge(df, merge_cfg, on_missing_col)

    # ── 16. Select (project) columns ──────────────────────────────────────────
    select_cfg = phase.get("select_columns", {})
    if select_cfg.get("applied"):
        # If aggregation is enabled, ensure we keep the source columns needed for aggregation
        df = _apply_select(
            df,
            select_cfg,
            on_missing_col,
            agg_source_columns if aggregation_enabled else None,
            rename_in_to_out,
        )

    # ── 17. Reorder columns ───────────────────────────────────────────────────
    reorder_cfg = phase.get("reorder_columns", {})
    if reorder_cfg.get("applied"):
        df = _apply_reorder(df, reorder_cfg, on_missing_col, rename_in_to_out)

    # ── 18. Apply residual column_mapping renames ─────────────────────────────
    # metadata.column_mapping holds the FULL input→output name map detected
    # during the detect phase (e.g. {"SL No": "Employee Count", ...}).
    # When rename_columns.applied is False (renaming was not detected as an
    # explicit op) but the column names in the config reference output names,
    # we still need to make df.columns match those output names so that
    # Phase 9 formatting lookups and Phase 10 header writing are correct.
    df = _apply_residual_mapping(df, config)

    print(f"[Phase 3] Column operations complete. Columns: {list(df.columns)}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── 13. Rename ────────────────────────────────────────────────────────────────

# def _apply_rename(
#     df: pd.DataFrame,
#     cfg: Dict[str, Any],
#     on_missing: str,
# ) -> pd.DataFrame:
#     """
#     Rename columns using the mappings dict.
#     Config shape:
#       {"details": {"mappings": {"old_name": "new_name", ...}}}
#     """
#     mappings: Dict[str, str] = cfg.get("details", {}).get("mappings", {})
#     if not mappings:
#         return df

#     safe_map = {
#         old: new
#         for old, new in mappings.items()
#         if old in df.columns
#     }
#     skipped = {old: new for old, new in mappings.items() if old not in df.columns}
#     if skipped and on_missing == "skip":
#         for old, new in skipped.items():
#             print(f"  [Phase 3] Rename skipped: '{old}' not found.")

#     df = df.rename(columns=safe_map)
#     for old, new in safe_map.items():
#         print(f"  [Phase 3] Renamed: '{old}' → '{new}'")
#     return df
def _apply_rename(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Rename columns using the mappings dict.
    Config shape:
      {"details": {"mappings": {"new_name": "old_name", ...}}}
    If old_name not present, add new_name as empty column.
    """
    mappings: Dict[str, str] = cfg.get("details", {}).get("mappings", {})
    if not mappings:
        return df

    # Build rename map: {old_name: new_name} for those old_names that exist
    rename_map = {
        old: new
        for new, old in mappings.items()
        if old in df.columns
    }
    # For mappings where old_name does not exist, add new_name as empty column
    for new, old in mappings.items():
        if old not in df.columns:
            if new not in df.columns:
                df[new] = pd.NA
                print(f"  [Phase 3] Added new column '{new}' (old name '{old}' not found).")
            else:
                print(f"  [Phase 3] Column '{new}' already exists, not adding again.")

    if rename_map:
        df = df.rename(columns=rename_map)
        for old, new in rename_map.items():
            print(f"  [Phase 3] Renamed: '{old}' → '{new}'")
    return df


# ── 14. Split ────────────────────────────────────────────────────────────────

def _apply_split(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Split a source column into multiple new columns.
    Rule shape:
      {"source": col, "delimiter": str, "output_columns": [col1, col2, ...],
       "max_splits": int}
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        # support both key variants: "source_column" (YAML) and "source" (legacy)
        src = col_name(rule.get("source_column") or rule.get("source", ""))
        print(f"  [Phase 3][Split DEBUG] rule keys={list(rule.keys())}, resolved src='{src}', df cols={list(df.columns)}")
        if not src:
            print(f"  [Phase 3][Split DEBUG] Skipping rule — no source column resolved.")
            continue
        if not col_exists(df, src, on_missing, "Phase 3"):
            print(f"  [Phase 3][Split DEBUG] Skipping rule — '{src}' not found in df.")
            continue

        delimiter = rule.get("delimiter", " ")
        # support both key variants: "new_column_names" (YAML) and "output_columns" (legacy)
        out_cols  = rule.get("new_column_names") or rule.get("output_columns", [])
        # support both key variants: "n_splits" (YAML) and "max_splits" (legacy)
        max_splits = rule.get("n_splits") if rule.get("n_splits") is not None else \
                     rule.get("max_splits", len(out_cols) - 1 if out_cols else -1)

        print(f"  [Phase 3][Split DEBUG] delimiter='{delimiter}', out_cols={out_cols}, max_splits={max_splits}")

        # Multi-delimiter: 'primary|secondary' means split by primary first,
        # then split the last part by secondary (e.g. 'Street, City, ST 12345')
        if isinstance(delimiter, str) and '|' in delimiter and len(delimiter.split('|')) == 2:
            primary, secondary = delimiter.split('|', 1)
            primary_parts = df[src].astype(str).str.split(primary, expand=True)
            print(f"  [Phase 3][Split DEBUG] multi-delim primary='{primary}' secondary='{secondary}', primary_parts shape={primary_parts.shape}")
            # Split the last primary column by secondary
            last_col_idx = primary_parts.shape[1] - 1
            secondary_parts = primary_parts.iloc[:, last_col_idx].str.strip().str.split(secondary, n=1, expand=True)
            # Combine: all primary parts except last + secondary parts
            combined_parts = pd.concat(
                [primary_parts.iloc[:, :last_col_idx], secondary_parts], axis=1
            ).reset_index(drop=True)
            combined_parts.columns = range(combined_parts.shape[1])
            expanded = combined_parts
        else:
            expanded = df[src].astype(str).str.split(delimiter, n=max_splits, expand=True)

        print(f"  [Phase 3][Split DEBUG] expanded shape={expanded.shape}, sample=\n{expanded.head(2)}")

        if out_cols:
            n_parts = min(len(out_cols), expanded.shape[1])
            for i in range(n_parts):
                df[out_cols[i]] = expanded.iloc[:, i].values
                print(f"  [Phase 3][Split DEBUG] wrote '{out_cols[i]}' sample={df[out_cols[i]].head(2).tolist()}")
            if len(out_cols) > n_parts:
                missing_out = out_cols[n_parts:]
                print(f"  [Phase 3][Split WARN] not enough split parts for: {missing_out} (expanded only had {expanded.shape[1]} cols, data may lack delimiter)")
            print(f"  [Phase 3] Split '{src}' → {out_cols[:n_parts]}")
        else:
            for i in range(expanded.shape[1]):
                auto_name = f"{src}_{i + 1}"
                df[auto_name] = expanded.iloc[:, i].values
            print(f"  [Phase 3] Split '{src}' into {expanded.shape[1]} auto-named columns")

    return df


# ── 15. Merge / concatenate ───────────────────────────────────────────────────

def _apply_merge(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
) -> pd.DataFrame:
    """
    Concatenate multiple source columns into one new column.
    Rule shape:
      {"sources": [col1, col2, ...], "output_column": str,
       "separator": str, "drop_sources": bool}
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        sources    = [col_name(s) for s in rule.get("sources", [])]
        out_col    = rule.get("output_column", "_merged")
        separator  = rule.get("separator", " ")
        drop_src   = rule.get("drop_sources", False)

        valid_sources = [c for c in sources if c in df.columns]
        missing = [c for c in sources if c not in df.columns]
        if missing and on_missing == "skip":
            for m in missing:
                print(f"  [Phase 3] Merge source '{m}' not found — skipping that part.")

        if not valid_sources:
            print(f"  [Phase 3] Merge skipped: no valid source columns.")
            continue

        df[out_col] = df[valid_sources].apply(
            lambda row: separator.join(str(v) for v in row if pd.notna(v) and str(v).strip()),
            axis=1,
        )
        print(f"  [Phase 3] Merged {valid_sources} → '{out_col}' (sep='{separator}')")

        if drop_src:
            df = df.drop(columns=[c for c in valid_sources if c != out_col])
            print(f"  [Phase 3] Dropped source columns: {valid_sources}")

    return df


# ── 16. Select ───────────────────────────────────────────────────────────────

def _apply_select(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
    agg_source_columns: Optional[set] = None,
    rename_in_to_out: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Keep only the specified columns (project).
    Config shape:
      {"details": {"columns": [col_obj_or_str, ...]}}

    If agg_source_columns is provided, ensure those columns are preserved
    even if they weren't explicitly listed (needed for later aggregation).

    If rename_in_to_out is provided, any requested column name that does not
    exist in df will be translated through the rename map (input→output) so
    that select still works when the YAML lists pre-rename input names while
    the actual df columns are already post-rename output names.
    """
    columns: List[Any] = cfg.get("details", {}).get("columns", [])
    if not columns:
        return df

    raw_keep = [col_name(c) for c in columns]
    raw_keep = [c for c in raw_keep if c]  # remove empty strings

    # Translate input-namespace names to output-namespace names where needed.
    keep: List[str] = []
    if rename_in_to_out:
        for c in raw_keep:
            if c in df.columns:
                keep.append(c)
            elif c in rename_in_to_out and rename_in_to_out[c] in df.columns:
                translated = rename_in_to_out[c]
                keep.append(translated)
                print(f"  [Phase 3] Select: translated input name '{c}' → output name '{translated}'")
            else:
                keep.append(c)  # will be caught as missing below
    else:
        keep = raw_keep

    # Only keep columns that actually exist (safe_mode)
    existing = [c for c in keep if c in df.columns]
    dropped  = [c for c in keep if c not in df.columns]

    if dropped and on_missing == "skip":
        for c in dropped:
            print(f"  [Phase 3] Select: column '{c}' not found — will not be included.")

    # If aggregation source columns provided, ensure they're included
    if agg_source_columns:
        for agg_col in agg_source_columns:
            if agg_col in df.columns and agg_col not in existing:
                existing.append(agg_col)
                print(f"  [Phase 3] Select: added '{agg_col}' (needed for aggregation)")

    if not existing:
        print("  [Phase 3] Select: no valid columns — keeping all.")
        return df

    df = df[existing]
    print(f"  [Phase 3] Selected {len(existing)} columns: {existing}")
    return df


# ── 17. Reorder ──────────────────────────────────────────────────────────────

def _apply_reorder(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
    rename_in_to_out: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Reorder columns according to the specified order.
    Config shape:
      {"details": {"order": [col_obj_or_str, ...]}}

    If rename_in_to_out is provided, any requested name that does not exist
    in df is translated through the rename map (input→output).
    """
    order: List[Any] = cfg.get("details", {}).get("order", [])
    if not order:
        return df

    desired_raw = [col_name(c) for c in order if col_name(c)]

    # Translate input-namespace names → output-namespace names when needed.
    if rename_in_to_out:
        translated: List[str] = []
        for c in desired_raw:
            if c in df.columns:
                translated.append(c)
            elif c in rename_in_to_out and rename_in_to_out[c] in df.columns:
                translated.append(rename_in_to_out[c])
            else:
                translated.append(c)
        desired = translated
    else:
        desired = desired_raw

    # Deduplicate while preserving first-occurrence order — the config may list
    # the same column name twice (e.g. from duplicate reorder entries), which
    # would cause pandas to produce ".1"-suffixed duplicate columns.
    seen: set = set()
    desired_deduped: List[str] = []
    for c in desired:
        if c not in seen:
            seen.add(c)
            desired_deduped.append(c)
    desired = desired_deduped

    existing_ordered = [c for c in desired if c in df.columns]
    remaining = [c for c in df.columns if c not in existing_ordered]

    # Append any columns not mentioned in the order list at the end
    final_order = existing_ordered + remaining
    df = df[final_order]
    print(f"  [Phase 3] Reordered columns: {final_order}")
    return df


# ── 18. Residual column_mapping renames ───────────────────────────────────────

def _apply_residual_mapping(
    df: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Apply any input→output column renames that are recorded in
    metadata.column_mapping but were NOT already handled by an explicit
    rename_columns rule (i.e. rename_columns.applied was False, or the
    mapping contains entries not listed in rename_columns.details.mappings).

    This guarantees that df.columns always uses output names after Phase 3,
    so Phase 9 formatting lookups and Phase 10 header writing are correct
    regardless of whether a rename was explicitly detected.

    Only renames columns that:
      • Still exist in df under their INPUT name  (not yet renamed)
      • Have a DIFFERENT output name in the mapping  (identity mappings skipped)
      • Do NOT collide with an existing df column of the same output name
    """
    col_mapping: Dict[str, str] = config.get("metadata", {}).get("column_mapping", {})
    if not col_mapping:
        return df

    # Build the set of renames already applied by the explicit rename step
    # so we don't log them again as residual.
    already_mapped: set = set()
    explicit_mappings = (
        config.get("phase_3_columns", {})
              .get("rename_columns", {})
              .get("details", {})
              .get("mappings", {})
    )
    if isinstance(explicit_mappings, dict):
        already_mapped = set(explicit_mappings.keys())

    # column_mapping is {output_col: input_col} (lineage direction).
    # To convert remaining input-namespace names to output names we must iterate
    # in reverse: for each (output_col, input_col) pair rename input_col->output_col
    # when input_col is still present in df (not yet renamed by explicit step).
    residual: Dict[str, str] = {}
    for out_col, in_col in col_mapping.items():  # note: out_col=key, in_col=value
        if out_col == in_col:
            continue                          # identity — nothing to do
        if in_col not in df.columns:
            continue                          # already renamed or doesn't exist
        if out_col in df.columns and out_col != in_col:
            # Target output name already present — skip to avoid clash
            print(f"  [Phase 3] Residual mapping skipped: "
                  f"target '{out_col}' already exists in df.")
            continue
        if in_col in already_mapped:
            continue                          # handled by explicit rename rule
        residual[in_col] = out_col

    if residual:
        df = df.rename(columns=residual)
        for old, new in residual.items():
            print(f"  [Phase 3] Residual rename (from column_mapping): '{old}' -> '{new}'")
    else:
        print("  [Phase 3] No residual column_mapping renames needed.")

    return df
