"""
execution/execution_phase_4_rows.py
=====================================
PHASE 4 — Row Operations
--------------------------
Why AFTER structure + columns? Filters reference final column names.
Why BEFORE enrichment? Avoid computing on rows that will be removed.

Operations (in order):
  18. Filter rows          ← config["phase_2_rows"]["filter"]          (ALL rules, sequential)
  19. Remove duplicates    ← config["phase_2_rows"]["remove_duplicates"]
  20. Sort                 ← config["phase_2_rows"]["sort"]             (multi-level)
  21. Subtotal rows        ← config["phase_2_rows"]["subtotal_rows"]

Config key : "phase_2_rows"
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
    Apply all row operations defined in config["phase_2_rows"].

    Args:
        df     : DataFrame produced by Phase 3.
        config : Full merged config dict.

    Returns:
        Filtered, deduped, sorted DataFrame — optionally with subtotal rows.
    """
    phase          = config.get("phase_2_rows", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")

    if not phase:
        print("[Phase 4] No row-ops config found — skipping.")
        return df

    df = df.copy()
    rows_in = len(df)
    
    print(f"[Phase 4] Starting row operations. Current columns: {list(df.columns)}")

    # Build column name resolution map using multiple sources:
    # 1. metadata.column_mapping: {output_col: input_col}
    # 2. phase_3_columns.rename_columns.mappings: {new_name: old_name}
    # We need to resolve FINAL names (as used in filter rules) to CURRENT df column names
    
    col_resolve_map = {}
    
    # Source 1: metadata.column_mapping (output_col: input_col)
    col_mapping = config.get("metadata", {}).get("column_mapping", {})
    for out_col, inp_col in col_mapping.items():
        # If filter uses input_col (e.g., "ProductID") and df has out_col (e.g., "ProductID_Cleaned")
        if inp_col != out_col:
            col_resolve_map[inp_col] = out_col
    
    # Source 2: phase_3_columns.rename_columns.mappings (new_name: old_name)
    # After rename, old_name becomes new_name
    rename_mappings = config.get("phase_3_columns", {}).get("rename_columns", {}).get("details", {}).get("mappings", {})
    for new_name, old_name in rename_mappings.items():
        if new_name != old_name:
            # If filter uses old_name, resolve to new_name (which is now in df)
            col_resolve_map[old_name] = new_name
    
    # Log the resolution map for debugging
    if col_resolve_map:
        print(f"[Phase 4] Column resolution map: {col_resolve_map}")

    # ── 18. Filter (ALL rules in sequence) ───────────────────────────────────
    filter_cfg = phase.get("filter", {})
    if filter_cfg.get("applied"):
        df = _apply_filter(df, filter_cfg, on_missing_col, col_resolve_map)

    # ── 19. Remove duplicates ─────────────────────────────────────────────────
    dedup_cfg = phase.get("remove_duplicates", {})
    if dedup_cfg.get("applied"):
        df = _apply_dedup(df, dedup_cfg, on_missing_col, col_resolve_map)

    # ── 20. Sort (multi-level) ────────────────────────────────────────────────
    sort_cfg = phase.get("sort", {})
    if sort_cfg.get("applied"):
        df = _apply_sort(df, sort_cfg, on_missing_col, col_resolve_map)

    # ── 20b. Top N / Bottom N (sort-then-head) ────────────────────────────────
    top_n_cfg = phase.get("top_n", {})
    if top_n_cfg.get("applied"):
        df = _apply_top_n(df, top_n_cfg, on_missing_col)

    # ── 21. Subtotal rows ─────────────────────────────────────────────────────
    subtotal_cfg = phase.get("subtotal_rows", {})
    if subtotal_cfg.get("applied"):
        df = _apply_subtotals(df, subtotal_cfg, on_missing_col)

    print(f"[Phase 4] Row operations complete. {rows_in} → {len(df)} rows.")
    return df


def run_deferred_filters(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply ONLY filter operations from config["phase_2_rows"]["filter"].
    
    This is called POST-ENRICHMENT to apply filters that reference derived columns
    which weren't available during the initial Phase 4 execution.
    
    Args:
        df     : DataFrame produced after Phase 5 (Enrichment).
        config : Full merged config dict.
    
    Returns:
        Filtered DataFrame.
    """
    phase          = config.get("phase_2_rows", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")
    
    filter_cfg = phase.get("filter", {})
    if not filter_cfg.get("applied"):
        print(f"[Post-Enrichment Filter] No filter config or not applied — skipping.")
        return df
    
    # Get the filter rules
    rules = filter_cfg.get("details", {}).get("rules", [])
    if not rules:
        print(f"[Post-Enrichment Filter] No filter rules found — skipping.")
        return df
    
    df = df.copy()
    rows_in = len(df)
    
    print(f"[Post-Enrichment Filter] Starting. Current columns: {list(df.columns)}")
    print(f"[Post-Enrichment Filter] DataFrame shape: {df.shape}")
    print(f"[Post-Enrichment Filter] Filter rules to apply: {len(rules)}")
    for i, rule in enumerate(rules):
        col = rule.get("column", {})
        col_name = col.get("name") if isinstance(col, dict) else col
        operator = rule.get("operator", "==")
        value = rule.get("value", rule.get("values", ""))
        print(f"[Post-Enrichment Filter]   Rule {i+1}: {col_name} {operator} {value}")
    
    # Apply filters - now derived columns should exist
    df = _apply_filter(df, filter_cfg, on_missing_col, col_resolve_map=None)
    
    print(f"[Post-Enrichment Filter] Complete. {rows_in} → {len(df)} rows.")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── 18. Filter ───────────────────────────────────────────────────────────────

_OPERATOR_MAP = {
    "==": "eq", "!=": "ne", ">": "gt", ">=": "ge", "<": "lt", "<=": "le",
    "eq": "eq", "ne": "ne", "gt": "gt", "ge": "ge", "lt": "lt", "le": "le",
    "in": "in", "not_in": "not_in", "notin": "not_in",
    "between": "between", "not_between": "not_between",
    "contains": "contains", "not_contains": "not_contains", "notcontains": "not_contains",
    "starts_with": "starts_with", "ends_with": "ends_with",
    "startswith": "starts_with", "endswith": "ends_with",  # aliases without underscore
    "is_null": "is_null", "is_not_null": "is_not_null", "not_null": "is_not_null", "notnull": "is_not_null", "isnull": "is_null",
    "regex": "regex",
}


def _apply_single_filter_rule(df: pd.DataFrame, rule: Dict[str, Any], on_missing: str, col_resolve_map: Dict[str, str] = None) -> pd.DataFrame:

    col = col_name(rule.get("column", ""))
    original_col = col
    
    # Try to resolve column name if it's not in df.columns
    if col and col not in df.columns:
        if col_resolve_map:
            resolved = col_resolve_map.get(col)
            if resolved and resolved in df.columns:
                print(f"  [Phase 4] Resolved column '{col}' → '{resolved}'")
                col = resolved
            else:
                # Maybe it's already the resolved name? Check if any value in map matches
                for mapped_col in col_resolve_map.values():
                    if mapped_col in df.columns and mapped_col.lower().replace("_", "").replace("cleaned", "").replace("filled", "") == col.lower().replace("_", ""):
                        print(f"  [Phase 4] Fuzzy-matched column '{col}' → '{mapped_col}'")
                        col = mapped_col
                        break
    
    if not col:
        print(f"  [Phase 4] Filter skipped: empty column name in rule {rule}")
        return df
        
    if col not in df.columns:
        print(f"  [Phase 4] Filter error: column '{original_col}' not found.")
        print(f"           Tried resolving to: '{col}'")
        print(f"           Available columns: {list(df.columns)}")
        if on_missing == "skip":
            return df
        return df

    operator = _OPERATOR_MAP.get(str(rule.get("operator", "==")).lower().strip(), "eq")
    value    = rule.get("values") or rule.get("values_list") or rule.get("value")
    logic    = str(rule.get("logic", "AND")).upper()  # noqa: F841

    series = df[col]

    try:
        if operator == "eq":
            mask = series == value
        elif operator == "ne":
            mask = series != value
        elif operator == "gt":
            mask = pd.to_numeric(series, errors="coerce") > float(value)
        elif operator == "ge":
            mask = pd.to_numeric(series, errors="coerce") >= float(value)
        elif operator == "lt":
            mask = pd.to_numeric(series, errors="coerce") < float(value)
        elif operator == "le":
            mask = pd.to_numeric(series, errors="coerce") <= float(value)
        elif operator == "in":
            vals = value if isinstance(value, list) else [value]
            mask = series.isin(vals)
        elif operator == "not_in":
            vals = value if isinstance(value, list) else [value]
            mask = ~series.isin(vals)
        elif operator == "between":
            # value should be a list like [min_val, max_val]
            if isinstance(value, list) and len(value) >= 2:
                min_val, max_val = float(value[0]), float(value[1])
                inclusive = str(rule.get("inclusive", "true")).lower() in ("true", "1", "yes", "both")
                numeric_series = pd.to_numeric(series, errors="coerce")
                if inclusive:
                    mask = (numeric_series >= min_val) & (numeric_series <= max_val)
                else:
                    mask = (numeric_series > min_val) & (numeric_series < max_val)
            else:
                print(f"  [Phase 4] Between filter requires a list [min, max], got: {value}")
                mask = pd.Series([True] * len(df), index=df.index)
        elif operator == "not_between":
            if isinstance(value, list) and len(value) >= 2:
                min_val, max_val = float(value[0]), float(value[1])
                inclusive = str(rule.get("inclusive", "true")).lower() in ("true", "1", "yes", "both")
                numeric_series = pd.to_numeric(series, errors="coerce")
                if inclusive:
                    mask = (numeric_series < min_val) | (numeric_series > max_val)
                else:
                    mask = (numeric_series <= min_val) | (numeric_series >= max_val)
            else:
                print(f"  [Phase 4] Not-between filter requires a list [min, max], got: {value}")
                mask = pd.Series([True] * len(df), index=df.index)
        elif operator == "contains":
            mask = series.astype(str).str.contains(str(value), na=False, regex=False)
        elif operator == "not_contains":
            mask = ~series.astype(str).str.contains(str(value), na=False, regex=False)
        elif operator == "starts_with":
            mask = series.astype(str).str.startswith(str(value), na=False)
        elif operator == "ends_with":
            mask = series.astype(str).str.endswith(str(value), na=False)
        elif operator == "is_null":
            mask = series.isna()
        elif operator == "is_not_null":
            mask = series.notna()
        elif operator == "regex":
            mask = series.astype(str).str.match(str(value), na=False)
        else:
            mask = series == value

        filtered = df[mask].reset_index(drop=True)
        removed  = len(df) - len(filtered)
        print(f"  [Phase 4] Filter '{col}' {operator} {repr(value)}: removed {removed} rows")
        return filtered

    except Exception as exc:
        print(f"  [Phase 4] Filter error on '{col}': {exc} — skipping rule.")
        return df


def _apply_single_filter_mask(df: pd.DataFrame, rule: Dict[str, Any], on_missing: str, col_resolve_map: Dict[str, str] = None) -> "pd.Series":
    """Return a boolean mask for a single filter rule (does not modify df)."""
    import numpy as np
    col = col_name(rule.get("column", ""))
    original_col = col

    if col and col not in df.columns and col_resolve_map:
        resolved = col_resolve_map.get(col)
        if resolved and resolved in df.columns:
            col = resolved

    if not col or col not in df.columns:
        print(f"  [Phase 4] Filter skipped: column '{original_col}' not found.")
        return pd.Series([True] * len(df), index=df.index)

    operator = _OPERATOR_MAP.get(str(rule.get("operator", "==")).lower().strip(), "eq")
    value    = rule.get("values") or rule.get("values_list") or rule.get("value")
    series   = df[col]

    try:
        if operator == "eq":           mask = series == value
        elif operator == "ne":         mask = series != value
        elif operator == "gt":         mask = pd.to_numeric(series, errors="coerce") > float(value)
        elif operator == "ge":         mask = pd.to_numeric(series, errors="coerce") >= float(value)
        elif operator == "lt":         mask = pd.to_numeric(series, errors="coerce") < float(value)
        elif operator == "le":         mask = pd.to_numeric(series, errors="coerce") <= float(value)
        elif operator == "in":         mask = series.isin(value if isinstance(value, list) else [value])
        elif operator == "not_in":     mask = ~series.isin(value if isinstance(value, list) else [value])
        elif operator == "between":
            # value should be a list like [min_val, max_val]
            if isinstance(value, list) and len(value) >= 2:
                min_val, max_val = float(value[0]), float(value[1])
                inclusive = str(rule.get("inclusive", "true")).lower() in ("true", "1", "yes", "both")
                numeric_series = pd.to_numeric(series, errors="coerce")
                mask = numeric_series.between(min_val, max_val, inclusive="both" if inclusive else "neither")
            else:
                mask = pd.Series([True] * len(df), index=df.index)
        elif operator == "not_between":
            if isinstance(value, list) and len(value) >= 2:
                min_val, max_val = float(value[0]), float(value[1])
                inclusive = str(rule.get("inclusive", "true")).lower() in ("true", "1", "yes", "both")
                numeric_series = pd.to_numeric(series, errors="coerce")
                mask = ~numeric_series.between(min_val, max_val, inclusive="both" if inclusive else "neither")
            else:
                mask = pd.Series([True] * len(df), index=df.index)
        elif operator == "contains":   mask = series.astype(str).str.contains(str(value), na=False, regex=False)
        elif operator == "not_contains": mask = ~series.astype(str).str.contains(str(value), na=False, regex=False)
        elif operator == "starts_with": mask = series.astype(str).str.startswith(str(value), na=False)
        elif operator == "ends_with":  mask = series.astype(str).str.endswith(str(value), na=False)
        elif operator == "is_null":    mask = series.isna()
        elif operator == "is_not_null": mask = series.notna()
        elif operator == "regex":      mask = series.astype(str).str.match(str(value), na=False)
        else:                          mask = series == value
        return mask
    except Exception as exc:
        print(f"  [Phase 4] Filter mask error on '{col}': {exc} — treating as pass-through.")
        return pd.Series([True] * len(df), index=df.index)


def _apply_filter(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str, col_resolve_map: Dict[str, str] = None) -> pd.DataFrame:
    details: Dict = cfg.get("details", {})
    rules: List[Dict] = details.get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    # logic can live at details level OR inside individual rules
    logic = str(details.get("logic", "AND")).upper()

    if not rules:
        return df

    masks = [_apply_single_filter_mask(df, rule, on_missing, col_resolve_map) for rule in rules]

    if logic == "OR":
        combined = masks[0]
        for m in masks[1:]:
            combined = combined | m
    else:  # AND (default)
        combined = masks[0]
        for m in masks[1:]:
            combined = combined & m

    filtered = df[combined].reset_index(drop=True)
    print(f"  [Phase 4] Filter ({logic} of {len(rules)} rules): {len(df)} → {len(filtered)} rows")
    return filtered


# ── 18b. Top N / Bottom N ────────────────────────────────────────────────────

def _apply_top_n(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Keep only the top N (or bottom N) rows, optionally after sorting.

    Config shape:
      top_n:
        applied: true
        details:
          n: 5
          sort_column: EmployeeID       # optional — sort before slicing
          ascending: true               # default true (nsmallest)
          direction: top | bottom       # default top
    """
    details   = cfg.get("details", {})
    n         = int(details.get("n", 0))
    sort_col  = col_name(details.get("sort_column", details.get("column", "")))
    ascending = str(details.get("ascending", "true")).lower() not in ("false", "0", "no", "desc")
    direction = str(details.get("direction", "top")).lower()

    if n <= 0:
        print("  [Phase 4] top_n: n <= 0 — skipping.")
        return df

    before = len(df)

    if sort_col and sort_col in df.columns:
        # Sort first, then slice — mirrors the sort+head detection pattern
        df = df.sort_values(by=sort_col, ascending=ascending).reset_index(drop=True)
        print(f"  [Phase 4] top_n: sorted by '{sort_col}' ascending={ascending}")
    elif sort_col:
        print(f"  [Phase 4] top_n: sort_column '{sort_col}' not found — slicing without sort")

    if direction == "bottom":
        df = df.tail(n).reset_index(drop=True)
    else:
        df = df.head(n).reset_index(drop=True)

    print(f"  [Phase 4] top_n: {before} → {len(df)} rows (n={n}, direction={direction})")
    return df


# ── 19. Remove duplicates ─────────────────────────────────────────────────────

def _resolve_col_ref(ref: Any, col_resolve_map: Dict[str, str] = None, df_columns: List[str] = None) -> str:
    """
    Resolve a column reference to a plain string name.
    Accepts either a plain string or a dict with a 'name' key
    (e.g. {"name": "Product", "dtype": "string"}).
    Also applies column resolution if the column is not found in df_columns.
    """
    if isinstance(ref, dict):
        col = col_name(ref.get("name", ""))
    else:
        col = col_name(ref)
    
    # Try to resolve if column not in df_columns
    if col and df_columns and col not in df_columns and col_resolve_map:
        resolved = col_resolve_map.get(col)
        if resolved and resolved in df_columns:
            return resolved
    
    return col


def _apply_dedup(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str, col_resolve_map: Dict[str, str] = None) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    df_columns = list(df.columns)

    for rule in rules:
        columns_ref = rule.get("columns", [])
        keep        = str(rule.get("keep", "first"))   # "first" | "last" | "none" | "max:<col>" | "min:<col>"

        # Resolve column references (plain strings OR {"name": ..., "dtype": ...} dicts)
        subset: Optional[List[str]] = None
        if columns_ref:
            cols_list = columns_ref if isinstance(columns_ref, list) else [columns_ref]
            subset = [_resolve_col_ref(c, col_resolve_map, df_columns) for c in cols_list]
            subset = [c for c in subset if c in df.columns]

        before = len(df)

        if keep in ("none", "False") or keep is False:
            # Remove ALL duplicates (keep no copy)
            df = df.drop_duplicates(subset=subset or None, keep=False).reset_index(drop=True)

        elif keep.startswith("max:") or keep.startswith("min:"):
            # keep="max:<col>" → keep the row with max value in <col> per group
            # keep="min:<col>" → keep the row with min value in <col> per group
            strategy, sort_col = keep.split(":", 1)
            sort_col = sort_col.strip()

            # Try to resolve sort_col if not found
            if sort_col not in df.columns and col_resolve_map:
                resolved = col_resolve_map.get(sort_col)
                if resolved and resolved in df.columns:
                    sort_col = resolved

            if sort_col not in df.columns:
                print(f"  [Phase 4] Dedup keep-{strategy}: column '{sort_col}' not found — falling back to 'first'.")
                df = df.drop_duplicates(subset=subset or None, keep="first").reset_index(drop=True)
            else:
                grp_cols = subset if subset else list(df.columns)
                # Use idxmax/idxmin to find the row to keep per group, then select
                # by sorted original index to preserve the original row order.
                if strategy == "max":
                    idx = df.groupby(grp_cols, sort=False)[sort_col].idxmax()
                else:
                    idx = df.groupby(grp_cols, sort=False)[sort_col].idxmin()
                df = df.loc[sorted(idx)].reset_index(drop=True)

        else:
            # Standard pandas keep: "first" or "last"
            pandas_keep = keep if keep in ("first", "last") else "first"
            df = df.drop_duplicates(subset=subset or None, keep=pandas_keep).reset_index(drop=True)

        print(f"  [Phase 4] Dedup on {subset or 'ALL'} (keep={keep}): removed {before - len(df)} duplicate rows")

    return df


# ── 20. Sort ──────────────────────────────────────────────────────────────────

def _apply_sort(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str, col_resolve_map: Dict[str, str] = None) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    df_columns = list(df.columns)

    sort_keys:  List[str] = []
    sort_asc:   List[bool] = []

    for rule in rules:
        col   = col_name(rule.get("column", ""))
        order = str(rule.get("order", "asc")).lower()

        # Try to resolve column name if not found
        if col and col not in df.columns and col_resolve_map:
            resolved = col_resolve_map.get(col)
            if resolved and resolved in df.columns:
                col = resolved

        if not col or not col_exists(df, col, on_missing, "Phase 4"):
            continue

        sort_keys.append(col)
        sort_asc.append(order != "desc")

    if sort_keys:
        df = df.sort_values(by=sort_keys, ascending=sort_asc).reset_index(drop=True)
        pairs = list(zip(sort_keys, ["asc" if a else "desc" for a in sort_asc]))
        print(f"  [Phase 4] Sorted by: {pairs}")

    return df


# ── 21. Subtotal rows ─────────────────────────────────────────────────────────

def _apply_subtotals(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Insert subtotal rows after each group.
    Rule shape:
      {"column": agg_col, "group_by": group_col, "function": "sum"|"avg"|...,
       "label": str}
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    if not rules:
        return df

    frames: List[pd.DataFrame] = []

    for rule in rules:
        agg_col   = col_name(rule.get("column",   ""))
        group_ref = rule.get("group_by", "")
        group_col = col_name(group_ref) if group_ref else None
        func      = rule.get("function", "sum").lower()
        label     = rule.get("label", f"Subtotal ({func})")

        if agg_col and agg_col not in df.columns:
            print(f"  [Phase 4] Subtotal: agg column '{agg_col}' not found — skipping.")
            continue
        if group_col and group_col not in df.columns:
            print(f"  [Phase 4] Subtotal: group column '{group_col}' not found — skipping.")
            continue

        numeric_col = pd.to_numeric(df[agg_col], errors="coerce") if agg_col else None

        if group_col:
            result_frames: List[pd.DataFrame] = []
            for group_val, grp in df.groupby(group_col, sort=False):
                result_frames.append(grp)
                # Build subtotal row
                sub_row = {c: "" for c in df.columns}
                sub_row[group_col] = f"{label} — {group_val}"
                if agg_col and numeric_col is not None:
                    grp_numeric = pd.to_numeric(grp[agg_col], errors="coerce")
                    if func == "sum":
                        sub_row[agg_col] = grp_numeric.sum()
                    elif func in ("avg", "mean"):
                        sub_row[agg_col] = grp_numeric.mean()
                    elif func == "count":
                        sub_row[agg_col] = grp_numeric.count()
                    elif func == "min":
                        sub_row[agg_col] = grp_numeric.min()
                    elif func == "max":
                        sub_row[agg_col] = grp_numeric.max()
                result_frames.append(pd.DataFrame([sub_row]))
            df = pd.concat(result_frames, ignore_index=True)
            print(f"  [Phase 4] Subtotal rows inserted for '{group_col}' → '{agg_col}' ({func})")
        else:
            # Grand total at the bottom
            grand_row = {c: "" for c in df.columns}
            grand_row[list(df.columns)[0]] = label
            if agg_col and numeric_col is not None:
                num = pd.to_numeric(df[agg_col], errors="coerce")
                if func == "sum":
                    grand_row[agg_col] = num.sum()
                elif func in ("avg", "mean"):
                    grand_row[agg_col] = num.mean()
                elif func == "count":
                    grand_row[agg_col] = num.count()
                elif func == "min":
                    grand_row[agg_col] = num.min()
                elif func == "max":
                    grand_row[agg_col] = num.max()
            df = pd.concat([df, pd.DataFrame([grand_row])], ignore_index=True)
            print(f"  [Phase 4] Grand total row inserted for '{agg_col}' ({func})")

    return df
