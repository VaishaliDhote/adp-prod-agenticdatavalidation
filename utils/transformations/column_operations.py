# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Column Operations Utilities
============================
Five focused builder functions for Phase 3 column-level transformation detection.

  build_select_config   → D. SELECT / PROJECT columns
  build_reorder_config  → E. REORDER columns
  build_rename_config   → F. RENAME columns
  build_split_config    → H. SPLIT columns  (1 → N)
  build_merge_config    → I. MERGE / CONCATENATE columns  (N → 1)

Each function accepts (input_df, output_df, column_mapping?) and returns a
self-contained config block that is consumed directly by phase_3_columns.detect().

Output schema example
---------------------
{
  "select_columns":  {"applied": bool, "details": {"columns": [...]}},
  "rename_columns":  {"applied": bool, "details": {"mappings": {"old": "new", ...}}},
  "reorder_columns": {"applied": bool, "details": {"order": [...]}},
  "split_columns":   {"applied": bool, "details": {"rules": [{"source": ..., "delimiter": ..., "targets": [...]}]}},
  "merge_columns":   {"applied": bool, "details": {"rules": [{"sources": [...], "target": ..., "separator": ...}]}}
}
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

_SIMILARITY_THRESHOLD = 0.75

# Common delimiters tried for split / merge detection, ordered by specificity
_DELIMITERS = [" ", "_", "-", ",", "|", "/", ";", ".", "@"]

# Keyword groups for semantic name matching
_NAME_PARTS   = {"first", "last", "given", "family", "middle", "fname", "lname"}
_ADDR_PARTS   = {"street", "city", "state", "zip", "postal", "country", "province"}
_DATE_PARTS   = {"year", "month", "day", "date", "quarter", "week", "weekday"}
_EMAIL_PARTS  = {"username", "user", "domain", "email", "mail"}
_CALC_HINTS   = {"total", "sum", "product", "difference", "ratio", "percentage",
                 "full", "combined", "complete", "concat", "merged", "address",
                 "name", "info", "desc", "description"}


def _norm(s: str) -> str:
    """Lowercase, strip underscores/spaces for fuzzy comparison."""
    return re.sub(r"[\s_\-]", "", s.lower())


def _similarity(a: str, b: str) -> float:
    """Normalised SequenceMatcher ratio on normalised strings."""
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _best_match(target: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    """Return (best_candidate, score) from *candidates* for *target*."""
    best, best_score = None, 0.0
    for c in candidates:
        s = _similarity(target, c)
        if s > best_score:
            best, best_score = c, s
    return best, best_score


# Sentinel values for derived/new columns that have no input counterpart
_DERIVED_SENTINELS = frozenset({
    "DERIVED COLUMN",
    "MULTISOURCE COLUMN",
    "UNKNOWN MAPPING TYPE",
})


def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to input-namespace when a mapping is given.

    column_mapping is {output_col: input_col}. We use it directly to rename
    output_df columns to their input equivalents, making set-intersection
    comparisons with input_df work correctly.

    Sentinel values like "DERIVED COLUMN" are excluded — those are new columns
    that have no counterpart in input, so they should remain under their
    output names.
    """
    if not column_mapping:
        return output_df

    # Use mapping directly (output→input), excluding sentinel values
    rename_map = {
        out_col: in_col
        for out_col, in_col in column_mapping.items()
        if in_col not in _DERIVED_SENTINELS
    }

    print(f"[_remap_output] Original output columns: {list(output_df.columns)}")
    print(f"[_remap_output] Rename mapping (output→input): {rename_map}")

    remapped_df = output_df.rename(columns=rename_map)
    print(f"[_remap_output] Remapped output columns: {list(remapped_df.columns)}")
    return remapped_df


def _to_output_col(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Translate an input-namespace column name to the output namespace.
    
    column_mapping is {output_col: input_col}. We invert it to find which
    output column corresponds to the given input column name.
    Sentinel values are excluded from inversion.
    """
    if not column_mapping:
        return col
    # Build inverse: {input_col: output_col}, excluding sentinels
    inv = {
        in_col: out_col
        for out_col, in_col in column_mapping.items()
        if in_col not in _DERIVED_SENTINELS
    }
    return inv.get(col, col)


# ─────────────────────────────────────────────────────────────────────────────
# D. SELECT / PROJECT COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def build_select_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect SELECT / PROJECT: output contains a subset of input columns.

    Edge cases handled
    ------------------
    - All input columns present in output  → applied=False
    - Empty DataFrames                     → applied=False
    - Renamed columns (via column_mapping) → mapped before comparison
    - New calculated columns in output     → ignored (not counted as removed)
    - Only one column remaining            → still detected

    Returns
    -------
    {"applied": bool, "details": {"columns": [output_col, ...]}}
    The *columns* list contains the output-namespace names that were kept.
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"columns": []}}

    # output_df is expected to already be remapped to input namespace by the caller.
    # _remap_output is a no-op if called again, but we skip it here for clarity.
    out_df = output_df
    print(f"[build_select_config] Output columns (pre-remapped by caller): {list(out_df.columns)}")

    in_cols  = set(input_df.columns)
    out_cols = set(out_df.columns)

    # Columns kept (present in both namespaces)
    kept_input = in_cols & out_cols

    # Columns that are genuinely new (not a renamed input col)
    genuinely_new: List[str] = []
    for oc in out_cols - in_cols:
        _, score = _best_match(oc, list(in_cols))
        if score < _SIMILARITY_THRESHOLD:
            genuinely_new.append(oc)

    removed = in_cols - out_cols
    # A "removed" column that actually appears as a rename is not truly removed
    accounted_renames: set = set()
    for oc in out_cols - in_cols:
        match, score = _best_match(oc, list(removed))
        if score >= _SIMILARITY_THRESHOLD and match:
            accounted_renames.add(match)

    truly_removed = removed - accounted_renames

    if not truly_removed:
        return {"applied": False, "details": {"columns": []}}

    # Report kept columns in output namespace
    kept_output = [
        _to_output_col(c, column_mapping) for c in sorted(kept_input)
    ]
    return {"applied": True, "details": {"columns": kept_output}}


# ─────────────────────────────────────────────────────────────────────────────
# E. REORDER COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def build_reorder_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect REORDER: same column set but different sequence.

    Edge cases handled
    ------------------
    - Column sets differ (select/rename also present) → align to common cols
    - Trivially identical order                        → applied=False
    - Single column DataFrames                         → applied=False
    - Positional shifts caused purely by renames       → ignored via mapping

    Returns
    -------
    {"applied": bool, "details": {"order": [output_col, ...]}}
    """
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"order": []}}

    # output_df is expected to already be remapped to input namespace by the caller.
    out_df = output_df

    # Work only with columns present in both
    common = [c for c in input_df.columns if c in out_df.columns]
    if len(common) < 2:
        return {"applied": False, "details": {"order": []}}

    in_order  = [c for c in input_df.columns  if c in common]
    out_order = [c for c in out_df.columns    if c in common]

    if in_order == out_order:
        return {"applied": False, "details": {"order": []}}

    # Translate back to output namespace for the reported order
    full_out_order = [
        _to_output_col(c, column_mapping) for c in out_df.columns
    ]
    return {"applied": True, "details": {"order": full_out_order}}


# ─────────────────────────────────────────────────────────────────────────────
# F. RENAME COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def _build_rename_map_from_mapping(
    column_mapping: Dict[str, str],
) -> Dict[str, str]:
    """Extract renames directly from an explicit column_mapping dict."""
    return {
        old: new
        for old, new in column_mapping.items()
        if old != new
    }


def _infer_rename_map(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> Dict[str, str]:
    """
    Infer renames heuristically when no mapping is supplied.

    Strategy
    --------
    1. Columns that appear verbatim in both → not renamed.
    2. Unmatched output columns paired with unmatched input columns by
       similarity score (greedy best-match).
    3. Only pairs above _SIMILARITY_THRESHOLD are accepted.
    4. Handles camelCase ↔ snake_case, abbreviations (amt → amount),
       prefix/suffix changes.
    """
    in_cols  = list(input_df.columns)
    out_cols = list(output_df.columns)

    exact_in  = set(in_cols) & set(out_cols)
    unmatched_in  = [c for c in in_cols  if c not in exact_in]
    unmatched_out = [c for c in out_cols if c not in exact_in]

    rename_map: Dict[str, str] = {}
    used_in: set = set()

    for oc in unmatched_out:
        best_ic, best_score = _best_match(oc, [c for c in unmatched_in if c not in used_in])
        if best_ic and best_score >= _SIMILARITY_THRESHOLD:
            rename_map[best_ic] = oc
            used_in.add(best_ic)

    return rename_map


def build_rename_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect RENAME: column headers changed.

    Edge cases handled
    ------------------
    - Explicit column_mapping provided            → used directly
    - No mapping: fuzzy similarity pairing        → greedy best-match
    - camelCase ↔ snake_case (FirstName/first_name) 
    - Abbreviation expansions (amt → amount, qty → quantity)
    - Case-only changes (NAME → name)
    - Partial renames (some columns renamed, others kept)
    - Renames alongside selects / reorders        → handled independently

    Returns
    -------
    {"applied": bool, "details": {"mappings": {"input_col": "output_col", ...}}}
    """
    print(f"[_build_rename_config] Starting rename detection with column_mapping: {column_mapping}...")
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"mappings": {}}}

    if column_mapping:
        rename_map = _build_rename_map_from_mapping(column_mapping)
    else:
        rename_map = _infer_rename_map(input_df, output_df)
    print(f"[_build_rename_config] Detected rename mappings: {rename_map}")
    return {
        "applied": len(rename_map) > 0,
        "details": {"mappings": rename_map},
    }


# ─────────────────────────────────────────────────────────────────────────────
# H. SPLIT COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def _keyword_group(col: str) -> Optional[str]:
    """Return the semantic group a column name belongs to, or None."""
    n = _norm(col)
    if any(k in n for k in _NAME_PARTS):   return "name"
    if any(k in n for k in _ADDR_PARTS):   return "address"
    if any(k in n for k in _DATE_PARTS):   return "date"
    if any(k in n for k in _EMAIL_PARTS):  return "email"
    return None


def _detect_delimiter_for_split(
    source_series: pd.Series,
    target_series_list: List[pd.Series],
) -> Optional[str]:
    """
    Try each delimiter and return the first one where splitting *source*
    consistently produces exactly len(target_series_list) parts that match
    the target column values for a majority of rows.

    Also handles multi-delimiter splits where the primary delimiter produces
    fewer parts than targets, and the last part is further split by a secondary
    delimiter (e.g. 'Street, City, ST 12345' → primary=',', secondary=' ').
    Returns 'primary|secondary' string in that case.
    """
    n_targets = len(target_series_list)
    min_rows  = max(1, min(len(source_series), *(len(t) for t in target_series_list)))
    sample_n  = min(min_rows, 20)

    # ── Single delimiter ──────────────────────────────────────────────────────
    for delim in _DELIMITERS:
        match_count = 0
        total       = 0
        for i in range(sample_n):
            try:
                src_val = str(source_series.iloc[i])
                parts   = src_val.split(delim)
                if len(parts) != n_targets:
                    continue
                row_ok = all(
                    str(target_series_list[j].iloc[i]).strip() == parts[j].strip()
                    for j in range(n_targets)
                )
                total += 1
                if row_ok:
                    match_count += 1
            except (IndexError, Exception):
                continue
        if total > 0 and match_count / total >= 0.7:
            return delim

    # ── Multi-delimiter: primary splits into (n_targets-1) parts, last part
    #    is further split by secondary delimiter — emit as two chained rules
    #    by returning a sentinel so the caller can build two rules instead of one.
    for primary in _DELIMITERS:
        for secondary in _DELIMITERS:
            if primary == secondary:
                continue
            match_count = 0
            total       = 0
            for i in range(sample_n):
                try:
                    src_val       = str(source_series.iloc[i])
                    primary_parts = [p.strip() for p in src_val.split(primary)]
                    if len(primary_parts) != n_targets - 1:
                        continue
                    last_parts = primary_parts[-1].split(secondary, 1)
                    if len(last_parts) != 2:
                        continue
                    all_parts = primary_parts[:-1] + last_parts
                    if len(all_parts) != n_targets:
                        continue
                    row_ok = all(
                        str(target_series_list[j].iloc[i]).strip() == all_parts[j].strip()
                        for j in range(n_targets)
                    )
                    total += 1
                    if row_ok:
                        match_count += 1
                except (IndexError, Exception):
                    continue
            if total > 0 and match_count / total >= 0.7:
                # Return structured dict so caller emits two chained rules
                return {"type": "chained", "primary": primary, "secondary": secondary}

    return None


def _infer_split_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    in_cols  = set(input_df.columns)
    out_cols = set(output_df.columns)

    new_out_cols    = [c for c in output_df.columns if c not in in_cols]
    removed_in_cols = [c for c in input_df.columns  if c not in out_cols]

    print(f"[_infer_split_rules] removed_in_cols={removed_in_cols}")
    print(f"[_infer_split_rules] new_out_cols={new_out_cols}")

    rules: List[Dict[str, Any]] = []
    used_new: set = set()
    used_src: set = set()

    _MAX_COMBO_SIZE = 4  # raised from 3 to support 4-target splits (Street/City/State/Zip)

    for src in removed_in_cols:
        if src in used_src:
            continue
        src_series = input_df[src].dropna().reset_index(drop=True).astype(str)
        available_new = [c for c in new_out_cols if c not in used_new]
        best_rule: Optional[Dict[str, Any]] = None

        print(f"[_infer_split_rules] Trying src='{src}', available_new={available_new}")

        for r in range(2, min(len(available_new), _MAX_COMBO_SIZE) + 1):
            for combo in combinations(available_new, r):
                target_series = [
                    output_df[c].dropna().reset_index(drop=True).astype(str)
                    for c in combo
                ]
                delim = _detect_delimiter_for_split(src_series, target_series)
                print(f"[_infer_split_rules]   combo={combo} → delim={delim!r}")
                if delim is not None:
                    best_rule = {"source": src, "delimiter": delim, "targets": list(combo)}
                    break
            if best_rule:
                break

        # ── Semantic fallback ─────────────────────────────────────────────────
        if not best_rule:
            src_group = _keyword_group(src)
            if src_group:
                semantic_targets = [
                    c for c in available_new
                    if _keyword_group(c) == src_group or _similarity(src, c) >= 0.45
                ]
                if len(semantic_targets) >= 2:
                    best_rule = {"source": src, "delimiter": None, "targets": semantic_targets}
            print(f"[_infer_split_rules]   semantic fallback for '{src}': group={src_group}, best_rule={best_rule}")

        if best_rule:
            rules.append(best_rule)
            used_src.add(src)
            for t in best_rule["targets"]:
                used_new.add(t)

    return rules

def build_split_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect SPLIT: one input column becomes multiple output columns (1:N).
    """
    print(f"[build_split_config] Starting split detection with column_mapping: {column_mapping}...")
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    if len(input_df) != len(output_df):
        print(f"[build_split_config] Row count mismatch — skipping.")
        return {"applied": False, "details": {"rules": []}}

    candidate_types = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}

    # output columns whose source is a sentinel (truly derived/split targets)
    split_target_cols: List[str] = []
    if column_mapping:
        split_target_cols = [k for k, v in column_mapping.items() if v in candidate_types]
    else:
        split_target_cols = [c for c in output_df.columns if c not in input_df.columns]

    print(f"[build_split_config] split_target_cols={split_target_cols}")

    if not split_target_cols:
        return {"applied": False, "details": {"rules": []}}

    # Build a working output df that only contains the split target columns
    # plus any input columns (so _infer_split_rules sees them as "new")
    # We pass input_df as-is and a synthetic output_df that:
    #   - keeps all input columns (so removed_in_cols stays empty for non-sources)
    #   - adds the split target columns with their actual output data
    # Instead, we directly call _infer_split_rules_for_targets:
    rules = _infer_split_rules_for_targets(
        input_df=input_df,
        output_df=output_df,
        split_target_cols=split_target_cols,
        column_mapping=column_mapping,
    )

    print(f"[build_split_config] Detected split rules: {rules}")
    return {"applied": len(rules) > 0, "details": {"rules": rules}}


def _infer_split_rules_for_targets(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    split_target_cols: List[str],
    column_mapping: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    For each input column, check if any subset of split_target_cols can be
    produced by splitting it with a single or multi delimiter.
    """
    _MAX_COMBO_SIZE = 5
    rules: List[Dict[str, Any]] = []
    used_targets: set = set()

    # All input columns are potential sources
    for src in input_df.columns:
        src_series = input_df[src].dropna().reset_index(drop=True).astype(str)
        available = [c for c in split_target_cols if c in output_df.columns and c not in used_targets]

        # print(f"[_infer_split_rules_for_targets] src='{src}', available targets={available}")

        best_rule: Optional[Dict[str, Any]] = None

        for r in range(2, min(len(available), _MAX_COMBO_SIZE) + 1):
            for combo in combinations(available, r):
                target_series = [
                    output_df[c].dropna().reset_index(drop=True).astype(str)
                    for c in combo
                ]
                delim = _detect_delimiter_for_split(src_series, target_series)
                # print(f"[_infer_split_rules_for_targets]   combo={combo} → delim={delim!r}")
                if delim is not None:
                    best_rule = {
                        "source_column": _to_output_col(src, column_mapping),
                        "delimiter":     delim,
                        "new_column_names": list(combo),
                        "expand":        True,
                        "n_splits":      len(combo) - 1,
                    }
                    break
            if best_rule:
                break

        if best_rule:
            rules.append(best_rule)
            for t in best_rule["new_column_names"]:
                used_targets.add(t)

    return rules
# ─────────────────────────────────────────────────────────────────────────────
# I. MERGE / CONCATENATE COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def _detect_separator_for_merge(
    source_series_list: List[pd.Series],
    target_series: pd.Series,
) -> Optional[str]:
    """
    Return the separator that, when used to join the source columns row-by-row,
    reproduces the target column values for a majority of rows.
    Tries the empty string (no separator) as well.
    """
    candidates = _DELIMITERS + [""]
    sample_n   = min(20, len(target_series))

    for sep in candidates:
        match_count = 0
        total       = 0
        for i in range(sample_n):
            try:
                parts = [str(s.iloc[i]).strip() for s in source_series_list]
                # Skip all-NaN / empty rows
                parts = [p for p in parts if p and p.lower() not in ("nan", "none", "")]
                if not parts:
                    continue
                expected = sep.join(parts)
                actual   = str(target_series.iloc[i]).strip()
                total   += 1
                if expected == actual:
                    match_count += 1
            except (IndexError, Exception):
                continue
        if total > 0 and match_count / total >= 0.7:
            return sep if sep != "" else None   # None means "no separator"
    return "UNKNOWN"


def _infer_merge_rules(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """
    Infer merge rules: find output columns whose data is a concatenation of
    multiple input columns.

    Approach
    --------
    1. New output columns (not in input) are merge target candidates.
    2. Removed input columns (not in output) are merge source candidates.
    3. For each new output column, try every pair / group of removed input
       columns and verify concatenation row-by-row.
    4. Semantic grouping used as fallback when data verification is inconclusive.
    """
    in_cols  = set(input_df.columns)
    out_cols = set(output_df.columns)

    new_out_cols    = [c for c in output_df.columns if c not in in_cols]
    removed_in_cols = [c for c in input_df.columns  if c not in out_cols]

    rules: List[Dict[str, Any]] = []
    used_src: set  = set()
    used_tgt: set  = set()

    _MAX_COMBO_SIZE = 3  # cap to avoid exponential blowup on large column sets

    for target in new_out_cols:
        if target in used_tgt:
            continue
        target_series = output_df[target].dropna().reset_index(drop=True).astype(str)

        available_src = [c for c in removed_in_cols if c not in used_src]
        best_rule: Optional[Dict[str, Any]] = None

        # Try subsets of 2+ removed columns as sources (capped to avoid exponential blowup)
        for r in range(2, min(len(available_src), _MAX_COMBO_SIZE) + 1):
            for combo in combinations(available_src, r):
                src_series_list = [
                    input_df[c].reset_index(drop=True) for c in combo
                ]
                sep = _detect_separator_for_merge(src_series_list, target_series)
                if sep != "UNKNOWN":
                    best_rule = {
                        "sources":   list(combo),
                        "target":    target,
                        "separator": sep,
                    }
                    break
            if best_rule:
                break

        # Semantic fallback
        if not best_rule:
            tgt_group = _keyword_group(target)
            if tgt_group:
                semantic_srcs = [
                    c for c in available_src
                    if _keyword_group(c) == tgt_group
                       or _similarity(target, c) >= 0.45
                ]
                if len(semantic_srcs) >= 2:
                    best_rule = {
                        "sources":   semantic_srcs,
                        "target":    target,
                        "separator": None,
                    }
            # Also check: target name is semantically similar to a combo of source names
            if not best_rule and len(available_src) >= 2:
                matched_srcs = [
                    c for c in available_src
                    if _similarity(target, c) >= 0.4
                ]
                if len(matched_srcs) >= 2:
                    best_rule = {
                        "sources":   matched_srcs,
                        "target":    target,
                        "separator": None,
                    }

        if best_rule:
            rules.append(best_rule)
            used_tgt.add(target)
            for s in best_rule["sources"]:
                used_src.add(s)

    return rules


def build_merge_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect MERGE / CONCATENATE: multiple input columns combined into one (N:1).

    Uses column_mapping to skip direct columns and only check derived/multisource/unknown columns.
    """
    print(f"[build_merge_config] Starting merge detection with column_mapping: {column_mapping}...")
    if input_df.empty or output_df.empty:
        return {"applied": False, "details": {"rules": []}}

    # Merge/concatenate is row-preserving: if row counts differ, it cannot have occurred.
    if len(input_df) != len(output_df):
        print(f"[build_merge_config] Row count mismatch ({len(input_df)} vs {len(output_df)}) — skipping merge detection.")
        return {"applied": False, "details": {"rules": []}}

    # Identify candidate output columns for merge detection
    candidate_types = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    candidate_cols = set()
    if column_mapping:
        candidate_cols = {k for k, v in column_mapping.items() if v in candidate_types}
    else:
        # If no mapping, fall back to all output columns not in input
        candidate_cols = set(output_df.columns) - set(input_df.columns)

    # output_df is expected to already be remapped to input namespace by the caller.
    out_df = output_df
    print(f"[build_merge_config] Output columns (pre-remapped by caller): {list(out_df.columns)}")
    # Only consider new output columns that are candidates
    out_cols = set(out_df.columns)
    merge_targets = [c for c in out_cols if c in candidate_cols]

    # Infer merge rules only for candidate columns
    rules: List[Dict[str, Any]] = []
    if merge_targets:
        all_rules = _infer_merge_rules(input_df, out_df)
        for rule in all_rules:
            if rule["target"] in merge_targets:
                rules.append({
                    "sources":   [_to_output_col(s, column_mapping) for s in rule["sources"]],
                    "target":    _to_output_col(rule["target"], column_mapping),
                    "separator": rule["separator"],
                })
    print(f"[build_merge_config] Detected merge rules: {rules}")
    return {"applied": len(rules) > 0, "details": {"rules": rules}}