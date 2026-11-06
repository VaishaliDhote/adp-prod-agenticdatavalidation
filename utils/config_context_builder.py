# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Targeted-editing CORE: build_edit_context / merge_edited_slice /
# format_changes (tasks 16, 21-23). REUSABLE but REPURPOSE.
# Rework: locate affected block by UUID and operate on JSON blocks instead of YAML phases.
# ============================================================================
"""
utils/config_context_builder.py
================================
Build minimal, focused context for config editing LLM calls.
Returns only the changes applied (not full YAML) for user display.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from deepdiff import DeepDiff  # pip install deepdiff


# ─────────────────────────────────────────────────────────────────────────────
# Phase Classification
# ─────────────────────────────────────────────────────────────────────────────

PHASE_KEYWORDS: dict[str, list[str]] = {
    "phase_2_rows": [
        "filter", "remove duplicate", "dedupe", "sort row", "row filter",
        "keep row", "delete row", "exclude row", "where", "condition",
    ],
    "phase_3_columns": [
        "rename", "reorder", "select column", "drop column", "remove column",
        "split column", "merge column", "column order", "column name",
    ],
    "phase_4_enrichment": [
        "calculate", "formula", "concat", "concatenate", "bonus", "band",
        "grade", "if condition", "lookup", "join", "derive", "new column",
        "add column", "computed", "text operation", "date operation",
    ],
    "phase_5_aggregation": [
        "group by", "groupby", "sum", "average", "count", "aggregate",
        "rank", "running total", "cumulative", "lag", "lead", "window",
    ],
    "phase_6_cleaning": [
        "trim", "replace", "find replace", "missing", "null", "empty",
        "type convert", "cast", "clean", "whitespace",
    ],
    "phase_7_advanced": [
        "unique", "distinct", "array formula", "indirect", "advanced",
    ],
    "phase_9_final": [
        "final", "save", "output", "remove intermediate", "resequence",
    ],
    "metadata": [
        "metadata", "column mapping", "source", "target",
    ],
}


def detect_affected_phases(user_query: str) -> list[str]:
    """
    Classify user query to determine which config phases are relevant.
    Returns list of phase keys that likely need editing.
    """
    q = user_query.lower()
    affected = []
    for phase, keywords in PHASE_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            affected.append(phase)
    # Default to enrichment if no match (most common edit target)
    return affected if affected else ["phase_4_enrichment"]


# ─────────────────────────────────────────────────────────────────────────────
# YAML Slicing
# ─────────────────────────────────────────────────────────────────────────────

def extract_phase_slice(full_yaml: str, phases: list[str]) -> str:
    """
    Extract only the specified phases from full YAML config.
    Returns a minimal YAML string containing just those sections.
    """
    try:
        config = yaml.safe_load(full_yaml) or {}
    except yaml.YAMLError:
        return full_yaml  # fallback to full if parse fails

    # Handle nested structures (e.g., full_state.yaml with phase_transformation_result.corrected_config)
    if "phase_transformation_result" in config:
        nested = config.get("phase_transformation_result", {})
        if "corrected_config" in nested:
            config = nested["corrected_config"]
            print("[extract_phase_slice] Using nested corrected_config")

    sliced = {}
    for phase in phases:
        if phase in config:
            sliced[phase] = config[phase]

    # Always include metadata.column_mapping for column reference
    if "metadata" in config and "metadata" not in sliced:
        sliced["_column_reference"] = {
            "available_columns": list(config.get("metadata", {}).get("column_mapping", {}).keys())
        }

    return yaml.dump(sliced, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge `override` into `base`.
    - Dict values are merged recursively (preserves keys not in override)
    - Non-dict values are replaced entirely
    - Lists are replaced entirely (not appended)

    This ensures that when LLM returns partial YAML like:
        phase_2_rows:
          filter:
            details:
              rules: [...]

    We preserve other keys like `filter.applied`, `filter.execution_order`,
    and sibling operations like `remove_duplicates`, `sort`, etc.
    """
    result = dict(base)  # shallow copy of base
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = _deep_merge(result[key], value)
        else:
            # Replace scalars and lists entirely
            result[key] = value
    return result


def merge_edited_slice(full_yaml: str, edited_slice_yaml: str) -> str:
    """
    Merge LLM's edited slice back into the full config.
    Uses deep merge to preserve keys that weren't explicitly changed.
    
    Handles nested structures where config lives inside:
      - phase_transformation_result.corrected_config
      - phase_transformation_result.original_config
    """
    try:
        full_config = yaml.safe_load(full_yaml) or {}
        edited = yaml.safe_load(edited_slice_yaml) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error during merge: {e}")

    # Remove helper keys that shouldn't be merged
    edited.pop("_column_reference", None)

    # Detect nested structure and merge into correct location
    # This mirrors the logic in extract_phase_slice
    if "phase_transformation_result" in full_config:
        nested = full_config.get("phase_transformation_result", {})
        if "corrected_config" in nested:
            # Merge into the nested corrected_config
            print("[merge_edited_slice] Merging into nested corrected_config")
            nested["corrected_config"] = _deep_merge(nested["corrected_config"], edited)
            # Also update original_config if it exists (to keep them in sync)
            if "original_config" in nested:
                nested["original_config"] = _deep_merge(nested["original_config"], edited)
            merged = full_config
        else:
            # phase_transformation_result exists but no corrected_config
            merged = _deep_merge(full_config, edited)
    else:
        # Standard flat config - merge at root level
        merged = _deep_merge(full_config, edited)

    return yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ─────────────────────────────────────────────────────────────────────────────
# Column Reference Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_column_reference(full_yaml: str) -> str:
    """
    Extract column names for LLM reference, presenting both input (pre-rename)
    and output (post-rename) column names so the LLM knows which names to use
    at each phase (filters use input names; post-rename phases use output names).

    Returns a multi-line string like:
        Input columns (use for filtering/row operations):
          SL No (int64), Name (string), Age (int64), Status (string), ...
        Output columns (use for enrichment/aggregation/final phases):
          Employee Count, Employee Name, Age, Status, ...
        Rename mapping (output ← input):
          Employee Count ← SL No, Employee Name ← Name, ...
    """
    try:
        config = yaml.safe_load(full_yaml) or {}
    except yaml.YAMLError as e:
        print(f"[build_column_reference] YAML parse error: {e}")
        return ""

    # Handle nested structures (e.g., full_state.yaml with phase_transformation_result.corrected_config)
    if "phase_transformation_result" in config:
        nested = config.get("phase_transformation_result", {})
        if "corrected_config" in nested:
            config = nested["corrected_config"]
            print("[build_column_reference] Using nested corrected_config")

    # ── 1. Input columns from phase_3_columns.select_columns ─────────────────
    select_cols: list[str] = []
    p3 = config.get("phase_3_columns", {})
    select_details = p3.get("select_columns", {}).get("details", {})
    for col_obj in select_details.get("columns", []):
        # Handle both string columns and dict columns with name/dtype
        if isinstance(col_obj, str):
            select_cols.append(col_obj)
        elif isinstance(col_obj, dict):
            name  = col_obj.get("name", "")
            dtype = col_obj.get("dtype", "")
            if name:
                select_cols.append(f"{name} ({dtype})" if dtype else name)

    # ── 2. Rename mapping from phase_3_columns.rename_columns ────────────────
    #    format in YAML: {output_col: input_col}
    rename_mappings: dict[str, str] = p3.get("rename_columns", {}).get("details", {}).get("mappings", {}) or {}
    rename_parts: list[str] = []
    output_cols: list[str] = []
    for out_col, in_col in rename_mappings.items():
        if in_col and "DERIVED" not in str(in_col).upper():
            rename_parts.append(f"{out_col} ← {in_col}")
        else:
            rename_parts.append(f"{out_col} (derived/new)")
        output_cols.append(out_col)

    # ── 3. Fallback: metadata.column_mapping ─────────────────────────────────
    if not select_cols and not rename_parts:
        col_mapping = config.get("metadata", {}).get("column_mapping", {})
        if col_mapping:
            print(f"[build_column_reference] Using metadata.column_mapping fallback")
            # Extract unique input column names (values) and output names (keys)
            input_names = set()
            output_names = list(col_mapping.keys())
            for out_col, in_col in col_mapping.items():
                if in_col and "DERIVED" not in str(in_col).upper():
                    input_names.add(in_col)
            
            lines = []
            if input_names:
                lines.append("Input columns (use for phase_2_rows filter/sort/dedup):")
                lines.append("  " + ", ".join(sorted(input_names)))
            if output_names:
                lines.append("Output columns (after rename):")
                lines.append("  " + ", ".join(output_names))
            if lines:
                return "\n".join(lines)

    # ── 4. Fallback: Extract from phase_2_rows filter rules ──────────────────
    if not select_cols and not output_cols:
        p2 = config.get("phase_2_rows", {})
        filter_rules = p2.get("filter", {}).get("details", {}).get("rules", [])
        cols_from_rules = set()
        for rule in filter_rules:
            col = rule.get("column", {})
            col_name = col.get("name") if isinstance(col, dict) else str(col)
            if col_name:
                cols_from_rules.add(col_name)
        if cols_from_rules:
            print(f"[build_column_reference] Extracted columns from filter rules: {cols_from_rules}")
            select_cols = list(cols_from_rules)

    # ── 5. Build human-readable reference ────────────────────────────────────
    if not select_cols and not output_cols:
        print("[build_column_reference] WARNING: No columns found in config!")
        return "⚠️ No column information found in configuration"
    
    lines = []
    if select_cols:
        lines.append("Input columns (use for phase_2_rows filter/sort/dedup):")
        lines.append("  " + ", ".join(select_cols))
    if output_cols:
        lines.append("Output columns (use for enrichment/aggregation/final phases):")
        lines.append("  " + ", ".join(output_cols))
    if rename_parts:
        lines.append("Rename mapping (output ← input):")
        lines.append("  " + ", ".join(rename_parts))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Context Builder
# ─────────────────────────────────────────────────────────────────────────────

def _extract_summary_regex(yaml_content: str) -> str:
    """
    Regex fallback: extract the summary value from raw YAML text when
    yaml.safe_load cannot parse the document.  Handles block scalars
    (|, |-) and plain / quoted single-line values.
    """
    block_re = re.compile(
        r'^[ \t]*summary:\s*\|[-+]?\n((?:(?:[ \t]+.*)\n?)+)',
        re.MULTILINE,
    )
    scalar_re = re.compile(
        r'^[ \t]*summary:\s+(.+)$',
        re.MULTILINE,
    )

    m = block_re.search(yaml_content)
    if m:
        block_text = m.group(1)
        lines = block_text.splitlines()
        indent = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
        return "\n".join(l[indent:] for l in lines).strip()

    m = scalar_re.search(yaml_content)
    if m:
        return m.group(1).strip().strip('"').strip("'")

    return ""


def extract_summary_from_config(full_yaml: str) -> str:
    """Extract the transformation summary from the config YAML.
    Falls back to regex extraction when the YAML cannot be parsed.
    """
    try:
        config = yaml.safe_load(full_yaml) or {}
    except yaml.YAMLError:
        # YAML has a parse error elsewhere in the document; extract summary
        # directly from the raw text so we don't lose it.
        return _extract_summary_regex(full_yaml)

    # Try nested location first (full_state.yaml format)
    if "phase_transformation_result" in config:
        summary = config.get("phase_transformation_result", {}).get("summary")
        if summary:
            return summary

    # Try top-level summary
    return config.get("summary", "")


def build_edit_context(
    user_query: str,
    full_yaml: str,
    conversation_history: list[dict[str, str]] | None = None,
    max_history_turns: int = 2,
) -> tuple[str, list[str]]:
    """
    Build minimal, focused context for LLM config editing.

    Args:
        user_query: User's edit request in natural language
        full_yaml: Complete YAML config
        conversation_history: List of {"role": str, "content": str} dicts
        max_history_turns: How many recent turns to include (each turn = user + assistant)

    Returns:
        Tuple of (context_string, affected_phases_list)
    """
    # Debug: Print YAML length and first 200 chars
    print(f"[build_edit_context] YAML length: {len(full_yaml) if full_yaml else 0}")
    print(f"[build_edit_context] YAML preview: {full_yaml[:300] if full_yaml else 'EMPTY'}...")
    
    # 1. Detect which phases need editing
    affected_phases = detect_affected_phases(user_query)
    print(f"[build_edit_context] Affected phases: {affected_phases}")

    # 2. Extract only relevant YAML slice
    yaml_slice = extract_phase_slice(full_yaml, affected_phases)
    print(f"[build_edit_context] YAML slice length: {len(yaml_slice)}")

    # 3. Get column reference
    column_ref = build_column_reference(full_yaml)
    print(f"[build_edit_context] Column reference: {column_ref[:200] if column_ref else 'EMPTY'}...")

    # 4. Get current summary from YAML
    current_summary = extract_summary_from_config(full_yaml)
    print(f"[build_edit_context] Current summary: {current_summary[:100] if current_summary else 'NONE'}...")

    # 5. Build recent history (limit to last N turns)
    history_text = ""
    if conversation_history:
        recent = conversation_history[-(max_history_turns * 2):]  # 2 messages per turn
        history_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}" for msg in recent
        )

    # 6. Construct focused context
    #
    # IMPORTANT: We deliberately do NOT instruct the LLM to emit a "Changes"
    # bullet list or an UPDATED SUMMARY block here. The user-facing reply must
    # stay minimal (1-2 sentence explanation + the edited YAML block). The
    # structured `changes` array and the regenerated summary are produced by
    # other code paths (see services/config_editor_service.py).
    context = f"""## TASK
Edit the YAML config section(s) below based on the user request.
Return ONLY the modified section(s) — not the full config.

## AVAILABLE COLUMNS
{column_ref}

## CONFIG SECTION(S) TO EDIT
```yaml
{yaml_slice}
```

## CURRENT SUMMARY (for your reference only — do NOT echo back to the user)
{current_summary or "No summary available."}

## RECENT CONVERSATION
{history_text or "None"}

## USER REQUEST
{user_query}

## RULES
1. Only modify keys relevant to the user's request
2. Preserve all other keys and values unchanged
3. Use exact column names from AVAILABLE COLUMNS
4. Return the edited section(s) in a single ```yaml``` block
5. Reply with ONLY: a 1-2 sentence explanation followed by the ```yaml``` block.
   Do NOT include a "Changes:" bullet list.
   Do NOT include a ```summary``` block or restate the current summary.
   Do NOT include a confirmation question — the system appends it automatically.
"""

    return context, affected_phases


# ─────────────────────────────────────────────────────────────────────────────
# Change Detection & Display
# ─────────────────────────────────────────────────────────────────────────────

def compute_yaml_diff(old_yaml: str, new_yaml: str) -> dict[str, Any]:
    """
    Compute structured diff between two YAML configs.
    Returns a dict with added, removed, and changed items.
    """
    try:
        old_config = yaml.safe_load(old_yaml) or {}
        new_config = yaml.safe_load(new_yaml) or {}
    except yaml.YAMLError:
        return {"error": "Failed to parse YAML"}

    diff = DeepDiff(old_config, new_config, ignore_order=True, verbose_level=2)
    return dict(diff)


def format_changes_for_display(old_yaml: str, new_yaml: str) -> str:
    """
    Generate human-readable summary of changes between two configs.
    Returns formatted string suitable for showing to users.
    """
    try:
        old_config = yaml.safe_load(old_yaml) or {}
        new_config = yaml.safe_load(new_yaml) or {}
    except yaml.YAMLError as e:
        return f"⚠️ Could not parse YAML: {e}"

    diff = DeepDiff(old_config, new_config, ignore_order=True, verbose_level=2)

    if not diff:
        return "✅ No changes detected."

    lines = ["## Changes Applied\n"]

    # Values changed
    if "values_changed" in diff:
        lines.append("### Modified Values")
        for path, change in diff["values_changed"].items():
            clean_path = _clean_diff_path(path)
            old_val = _format_value(change.get("old_value"))
            new_val = _format_value(change.get("new_value"))
            lines.append(f"- **{clean_path}**")
            lines.append(f"  - Was: `{old_val}`")
            lines.append(f"  - Now: `{new_val}`")
        lines.append("")

    # Items added
    if "dictionary_item_added" in diff:
        lines.append("### Added")
        for path in diff["dictionary_item_added"]:
            clean_path = _clean_diff_path(path)
            value = _get_value_at_path(new_config, path)
            lines.append(f"- **{clean_path}**: `{_format_value(value)}`")
        lines.append("")

    # Items removed
    if "dictionary_item_removed" in diff:
        lines.append("### Removed")
        for path in diff["dictionary_item_removed"]:
            clean_path = _clean_diff_path(path)
            lines.append(f"- ~~{clean_path}~~")
        lines.append("")

    # Iterable items added (list elements)
    if "iterable_item_added" in diff:
        lines.append("### Added to Lists")
        for path, value in diff["iterable_item_added"].items():
            clean_path = _clean_diff_path(path)
            lines.append(f"- **{clean_path}**: `{_format_value(value)}`")
        lines.append("")

    # Iterable items removed
    if "iterable_item_removed" in diff:
        lines.append("### Removed from Lists")
        for path, value in diff["iterable_item_removed"].items():
            clean_path = _clean_diff_path(path)
            lines.append(f"- ~~{clean_path}~~: `{_format_value(value)}`")
        lines.append("")

    return "\n".join(lines)


def _clean_diff_path(path: str) -> str:
    """Convert DeepDiff path like root['phase_2_rows']['filter'] to phase_2_rows.filter"""
    clean = path.replace("root", "").replace("['", ".").replace("']", "").replace("[", ".").replace("]", "")
    return clean.lstrip(".")


def _format_value(value: Any, max_len: int = 80) -> str:
    """Format a value for display, truncating if too long."""
    if value is None:
        return "null"
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _get_value_at_path(config: dict, path: str) -> Any:
    """Extract value from config at DeepDiff path."""
    # Parse path like root['phase_2_rows']['filter']['applied']
    keys = re.findall(r"\['([^']+)'\]|\[(\d+)\]", path)
    current = config
    for key_tuple in keys:
        key = key_tuple[0] or int(key_tuple[1])
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int):
            current = current[key] if key < len(current) else None
        else:
            return None
    return current


# ─────────────────────────────────────────────────────────────────────────────
# Compact Change Summary (for API responses)
# ─────────────────────────────────────────────────────────────────────────────

def get_change_summary(old_yaml: str, new_yaml: str) -> list[dict[str, Any]]:
    """
    Return a structured list of changes for API consumption.

    Returns:
        [
            {"type": "modified", "path": "phase_2_rows.filter.applied", "old": False, "new": True},
            {"type": "added", "path": "phase_4_enrichment.calculated_columns.details.rules[2]", "value": {...}},
            ...
        ]
    """
    try:
        old_config = yaml.safe_load(old_yaml) or {}
        new_config = yaml.safe_load(new_yaml) or {}
    except yaml.YAMLError:
        return [{"type": "error", "message": "Failed to parse YAML"}]

    diff = DeepDiff(old_config, new_config, ignore_order=True, verbose_level=2)
    changes = []

    if "values_changed" in diff:
        for path, change in diff["values_changed"].items():
            changes.append({
                "type": "modified",
                "path": _clean_diff_path(path),
                "old": change.get("old_value"),
                "new": change.get("new_value"),
            })

    if "dictionary_item_added" in diff:
        for path in diff["dictionary_item_added"]:
            changes.append({
                "type": "added",
                "path": _clean_diff_path(path),
                "value": _get_value_at_path(new_config, path),
            })

    if "dictionary_item_removed" in diff:
        for path in diff["dictionary_item_removed"]:
            changes.append({
                "type": "removed",
                "path": _clean_diff_path(path),
                "value": _get_value_at_path(old_config, path),
            })

    if "iterable_item_added" in diff:
        for path, value in diff["iterable_item_added"].items():
            changes.append({
                "type": "list_added",
                "path": _clean_diff_path(path),
                "value": value,
            })

    if "iterable_item_removed" in diff:
        for path, value in diff["iterable_item_removed"].items():
            changes.append({
                "type": "list_removed",
                "path": _clean_diff_path(path),
                "value": value,
            })

    return changes
