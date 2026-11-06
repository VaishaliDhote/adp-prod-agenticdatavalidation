# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Detection support: phase blocks, execution order, intermediate
# columns (task 13). REUSABLE.
# Rework: align emitted structure with the transformation rule inventory / JSON contract.
# ============================================================================
"""
config_utils.py
===============
Shared utilities that make every phase config production-safe:

  1.  make_column_object()          – rich column descriptor (name + aliases + dtype)
  2.  make_formula_object()         – safe formula struct (expression + inputs + output_dtype)
  3.  PREPROCESSING_BLOCK           – global preprocessing defaults
  4.  EXECUTION_ORDER               – canonical phase execution order
  5.  ERROR_HANDLING_BLOCK          – default error-handling policy
  6.  add_rule_priority()           – stamp execution_order index on each rule
  7.  wrap_phase()                  – wrap any phase dict with all metadata blocks
  8.  collect_intermediate_columns()– scan rules for derived column names
  9.  infer_dtype()                 – infer pandas dtype → canonical string
  10. make_lookup_rule()            – fully-defined lookup rule struct
  11. make_groupby_safe()           – add already_aggregated safety flag
  12. resolve_active_structural_op()– pick mutually-exclusive structural op
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 1.  COLUMN OBJECT
# ─────────────────────────────────────────────────────────────────────────────

def make_column_object(
    name: str,
    dtype: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    series: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """
    Replace a plain column string reference with a rich, machine-safe object.

    Args:
        name:    Canonical column name (as it appears in the output sheet).
        dtype:   Explicit dtype string. If None, inferred from *series*.
        aliases: Alternative names / casings for this column.
        series:  Optional pandas Series used to infer dtype and auto-aliases.

    Returns:
        {
          "name":    str,
          "aliases": [str, ...],
          "dtype":   str          # "string" | "float" | "int" | "boolean" |
                                  # "datetime" | "category" | "unknown"
        }

    Example
    -------
    >>> make_column_object("status", series=df["status"])
    {"name": "status", "aliases": ["Status", "STATUS"], "dtype": "string"}
    """
    resolved_dtype = dtype or (infer_dtype(series) if series is not None else "unknown")
    # resolved_aliases = aliases or _auto_aliases(name)
    return {
        "name":    name,
        # "aliases": resolved_aliases,
        "dtype":   resolved_dtype,
    }


def _auto_aliases(name: str) -> List[str]:
    """Generate common casing aliases for a column name."""
    aliases = set()
    aliases.add(name.lower())
    aliases.add(name.upper())
    aliases.add(name.title())
    # snake_case → Title Case
    aliases.add(name.replace("_", " ").title())
    # remove duplicates and the name itself
    aliases.discard(name)
    return sorted(aliases)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  FORMULA OBJECT
# ─────────────────────────────────────────────────────────────────────────────

def make_formula_object(
    expression: str,
    inputs: Optional[List[str]] = None,
    output_dtype: str = "float",
) -> Dict[str, Any]:
    """
    Replace a bare formula string with a validated, safe formula object.

    Args:
        expression:   Raw formula string, e.g. ``"price * quantity"``.
        inputs:       Column names referenced in the expression.
                      Auto-extracted from *expression* when None.
        output_dtype: Expected dtype of the computed column.

    Returns:
        {
          "expression":   str,
          "inputs":       [str, ...],
          "output_dtype": str
        }

    Example
    -------
    >>> make_formula_object("price * quantity")
    {"expression": "price * quantity", "inputs": ["price", "quantity"], "output_dtype": "float"}
    """
    resolved_inputs = inputs or _extract_column_refs(expression)
    return {
        "expression":   expression,
        "inputs":       resolved_inputs,
        "output_dtype": output_dtype,
    }


def _extract_column_refs(expression: str) -> List[str]:
    """
    Heuristically extract column name tokens from a formula string.
    Filters out Python keywords, numeric literals and operator symbols.
    """
    _PYTHON_KEYWORDS = {
        "and", "or", "not", "in", "is", "if", "else", "True", "False",
        "None", "lambda", "for", "return",
    }
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)
    seen: List[str] = []
    for tok in tokens:
        if tok not in _PYTHON_KEYWORDS and tok not in seen:
            seen.append(tok)
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# 3.  GLOBAL PREPROCESSING BLOCK
# ─────────────────────────────────────────────────────────────────────────────

PREPROCESSING_BLOCK: Dict[str, Any] = {
    "normalize_column_names": True,
    "case_insensitive":        True,
    "trim_whitespace":         True,
    "auto_type_conversion":    True,
    "handle_nulls":            True,
}

# ─────────────────────────────────────────────────────────────────────────────
# 4.  CANONICAL EXECUTION ORDER
# ─────────────────────────────────────────────────────────────────────────────

EXECUTION_ORDER: List[str] = [
    "phase_1_structure",
    "phase_2_rows",
    "phase_3_columns",
    "phase_4_enrichment",
    "phase_5_aggregation",
    "phase_6_cleaning",
    "phase_7_advanced",
    "phase_9_final",
]

# ─────────────────────────────────────────────────────────────────────────────
# 5.  ERROR HANDLING BLOCK
# ─────────────────────────────────────────────────────────────────────────────

ERROR_HANDLING_BLOCK: Dict[str, str] = {
    "on_missing_column": "skip",
    "on_type_error":     "coerce",
    "on_formula_error":  "set_null",
}

# ─────────────────────────────────────────────────────────────────────────────
# 6.  RULE PRIORITY  (within a phase operation)
# ─────────────────────────────────────────────────────────────────────────────

def add_rule_priority(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Stamp a 1-based ``execution_order`` index on every rule dict.

    Args:
        rules: List of rule dicts (may already have other keys).

    Returns:
        Same list mutated in-place (also returned for chaining).

    Example
    -------
    >>> add_rule_priority([{"column": "a"}, {"column": "b"}])
    [{"column": "a", "execution_order": 1}, {"column": "b", "execution_order": 2}]
    """
    print(f"Adding execution_order priority to {len(rules)} rules...")
    for i, rule in enumerate(rules, start=1):
        rule["execution_order"] = i
    print(f"Finished adding execution_order priority.")
    return rules


# ─────────────────────────────────────────────────────────────────────────────
# 7.  WRAP PHASE  (attach all metadata to a phase dict)
# ─────────────────────────────────────────────────────────────────────────────

def wrap_phase(
    phase_name: str,
    phase_body: Dict[str, Any],
    intermediate_columns: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Wrap a raw phase dict with production-safety metadata.

    Injects:
      • preprocessing block
      • execution_order list
      • error_handling block
      • metadata.intermediate_columns  (auto-collected when not supplied)

    Args:
        phase_name:           e.g. ``"phase_1_structure"``
        phase_body:           The dict returned by the phase's detect().
        intermediate_columns: Derived / helper column names to track.
                              When None, auto-collected from phase_body.

    Returns:
        Full production-safe config dict.
    """
    if intermediate_columns is None:
        intermediate_columns = collect_intermediate_columns({phase_name: phase_body})
    return {
        "preprocessing":   PREPROCESSING_BLOCK,
        "execution_order": EXECUTION_ORDER,
        "error_handling":  ERROR_HANDLING_BLOCK,
        phase_name:        phase_body,
        "metadata": {
            "phase":                 phase_name,
            "intermediate_columns":  intermediate_columns,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.  COLLECT INTERMEDIATE COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def collect_intermediate_columns(phase_configs: Dict[str, Any]) -> List[str]:
    """
    Scan all phase config dicts and collect column names that look like
    intermediate / helper columns (used in calculations but not in the
    final output).

    Heuristic: columns whose names start/end with common staging prefixes.

    Args:
        phase_configs: Merged dict of all phase outputs.

    Returns:
        Deduplicated list of intermediate column name strings.
    """
    _INTERMEDIATE_PATTERNS = re.compile(
        r"(^temp_|_tmp$|^calc_|^helper_|_stg$|^_|__$|^aux_|_aux$)",
        re.IGNORECASE,
    )
    found: List[str] = []
    _scan_for_columns(phase_configs, found)
    return sorted(
        {c for c in found if _INTERMEDIATE_PATTERNS.search(c)}
    )


def _scan_for_columns(obj: Any, acc: List[str]) -> None:
    """Recursive scanner: collect all values of keys named 'column' or 'columns'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "column" and isinstance(v, str):
                acc.append(v)
            elif k == "column" and isinstance(v, dict):
                acc.append(v.get("name", ""))
            elif k == "columns" and isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        acc.append(item)
                    elif isinstance(item, dict):
                        # Handle column objects or plain dicts
                        col_val = item.get("column")
                        if isinstance(col_val, dict):
                            acc.append(col_val.get("name", ""))
                        elif isinstance(col_val, str):
                            acc.append(col_val)
                        else:
                            acc.append(item.get("name", ""))
            else:
                _scan_for_columns(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _scan_for_columns(item, acc)


# ─────────────────────────────────────────────────────────────────────────────
# 9.  INFER DTYPE
# ─────────────────────────────────────────────────────────────────────────────

def infer_dtype(series: pd.Series) -> str:
    """
    Map a pandas Series dtype to a canonical config dtype string.

    Returns one of: "string" | "float" | "int" | "boolean" | "datetime" |
                    "category" | "unknown"
    """
    if series is None or series.empty:
        return "unknown"
    try:
        if pd.api.types.is_bool_dtype(series):
            return "boolean"
        # Check for 'datetime64[us]' specifically
        if str(series.dtype) == "datetime64[us]":
            return "datetime64[us]"
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        if pd.api.types.is_integer_dtype(series):
            return "int"
        if pd.api.types.is_float_dtype(series):
            return "float"
        if pd.api.types.is_categorical_dtype(series):
            return "category"
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            # Try to detect date-strings
            sample = series.dropna().astype(str).head(10)
            try:
                pd.to_datetime(sample, infer_datetime_format=True)
                return "datetime"
            except Exception:
                pass
            return "string"
    except Exception:
        pass
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 10. FULLY-DEFINED LOOKUP RULE
# ─────────────────────────────────────────────────────────────────────────────

def make_lookup_rule(
    left_key: str,
    right_key: str,
    columns_added: List[str],
    source: str = "same_sheet",
    join_type: str = "left",
    lookup_type: str = "VLOOKUP",
    confidence: float = 0.85,
) -> Dict[str, Any]:
    """
    Build a fully-defined, machine-executable lookup rule.

    Args:
        left_key:      Join key column in the left (input) DataFrame.
        right_key:     Join key column in the right (reference) DataFrame.
        columns_added: New columns brought in by the lookup.
        source:        Where the reference data lives ("same_sheet" | file path).
        join_type:     SQL-style join: "left" | "inner" | "right" | "outer".
        lookup_type:   Excel function family: "VLOOKUP" | "XLOOKUP" | "INDEX_MATCH".
        confidence:    Detection confidence score (0.0 – 1.0).

    Returns:
        Fully-defined lookup rule dict.
    """
    return {
        "left_key":      left_key,
        "right_key":     right_key,
        "source":        source,
        "join_type":     join_type,
        "lookup_type":   lookup_type,
        "columns_added": columns_added,
        "confidence":    confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 11. GROUPBY SAFETY FLAG
# ─────────────────────────────────────────────────────────────────────────────

def make_groupby_safe(groupby_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add the ``already_aggregated`` safety flag to a groupby config block.

    Prevents double-aggregation when the pipeline is re-applied on an
    already-aggregated sheet.

    Args:
        groupby_cfg: Raw groupby config dict (``{"applied": bool, "details": {...}}``).

    Returns:
        Same dict with ``already_aggregated: false`` injected into details.
    """
    if "details" in groupby_cfg:
        groupby_cfg["details"].setdefault("already_aggregated", False)
    return groupby_cfg


# ─────────────────────────────────────────────────────────────────────────────
# 12. RESOLVE ACTIVE STRUCTURAL OPERATION  (mutually exclusive)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_active_structural_op(
    transpose_cfg: Dict[str, Any],
    pivot_cfg: Dict[str, Any],
    unpivot_cfg: Dict[str, Any],
) -> str:
    """
    Determine which mutually-exclusive structural operation is active.

    Only ONE of transpose / pivot / unpivot should execute.  Priority:
      pivot > unpivot > transpose

    Args:
        transpose_cfg: Transpose config block.
        pivot_cfg:     Pivot config block.
        unpivot_cfg:   Unpivot config block.

    Returns:
        "pivot" | "unpivot" | "transpose" | "none"
    """
    if pivot_cfg.get("applied"):
        return "pivot"
    if unpivot_cfg.get("applied"):
        return "unpivot"
    if transpose_cfg.get("applied"):
        return "transpose"
    return "none"


# ─────────────────────────────────────────────────────────────────────────────
# 13. UPGRADE COLUMN REFS IN A RULE LIST
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_column_refs(
    rules: List[Dict[str, Any]],
    df: Optional[pd.DataFrame] = None,
    keys: Sequence[str] = ("column",),
    ctx: Optional[Any] = None,
    ctx_df_key: str = "in",
) -> List[Dict[str, Any]]:
    """
    Walk every rule dict and replace plain string column references under
    *keys* with full column objects produced by ``make_column_object()``.

    When a ``PhaseContext`` is supplied via *ctx*, pre-built column objects
    are reused from the context cache instead of re-running dtype inference,
    which avoids redundant computation across detection phases.

    Args:
        rules:       List of rule dicts.
        df:          Optional DataFrame to look up the Series for dtype inference
                     (used when *ctx* is not provided).
        keys:        Which dict keys to upgrade (default: ``("column",)``).
        ctx:         Optional ``PhaseContext`` carrying pre-built column objects.
        ctx_df_key:  Which side of the context to resolve from: ``"in"`` (input
                     DataFrame) or ``"out"`` (output DataFrame).

    Returns:
        Rules with upgraded column objects (mutated in-place + returned).
    """
    print(f"Upgrading column refs in rules based on keys: {keys}...")

    def _resolve(col_name: str) -> Any:
        if ctx is not None:
            return ctx.col_obj_out(col_name) if ctx_df_key == "out" else ctx.col_obj_in(col_name)
        series = None
        if df is not None and col_name in df.columns:
            series = df[col_name]
        return make_column_object(col_name, series=series)

    for rule in rules:
        for key in keys:
            val = rule.get(key)
            if isinstance(val, str) and val:
                rule[key] = _resolve(val)
            elif isinstance(val, list):
                rule[key] = [_resolve(v) if isinstance(v, str) else v for v in val]

    print(f"Finished upgrading column refs in rules.")
    return rules


# ─────────────────────────────────────────────────────────────────────────────
# 14. UPGRADE FORMULA REFS IN A RULE LIST
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_formula_refs(
    rules: List[Dict[str, Any]],
    key: str = "formula",
) -> List[Dict[str, Any]]:
    """
    Walk every rule dict and replace plain formula strings under *key* with
    safe formula objects produced by ``make_formula_object()``.

    Args:
        rules: List of rule dicts.
        key:   Which dict key holds the formula string (default: ``"formula"``).

    Returns:
        Rules with upgraded formula objects (mutated in-place + returned).
    """
    for rule in rules:
        val = rule.get(key)
        if isinstance(val, str) and val:
            # Infer output dtype from formula_type hint if present
            ftype = rule.get("formula_type", "")
            if "concat" in ftype.lower():
                out_dtype = "string"
            elif "bool" in ftype.lower() or "flag" in ftype.lower():
                out_dtype = "boolean"
            else:
                out_dtype = "float"
            rule[key] = make_formula_object(val, output_dtype=out_dtype)
    return rules


# ─────────────────────────────────────────────────────────────────────────────
# 15. UPGRADE OPERATION  (upgrade rules + set execution order in one call)
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_op(
    cfg: Dict[str, Any],
    op_order: int,
    df: Optional[pd.DataFrame] = None,
    keys: Sequence[str] = ("column",),
    list_key: str = "rules",
    ctx: Optional[Any] = None,
    ctx_df_key: str = "in",
) -> Dict[str, Any]:
    """
    Apply the standard upgrade-and-sequence pattern to a single operation config.

    Combines three repeated steps into one call:
      1. ``upgrade_column_refs``  – replace plain strings with column objects
      2. ``add_rule_priority``    – stamp execution_order on each rule
      3. ``cfg["execution_order"] = op_order``

    Args:
        cfg:         Operation config dict from a ``build_*_config()`` function.
        op_order:    This operation's position within its phase (1-based).
        df:          DataFrame for dtype inference (used when *ctx* is not provided).
        keys:        Rule-level keys that hold column references.
        list_key:    Key inside ``cfg["details"]`` that holds the rule list
                     (default ``"rules"``).
        ctx:         Optional ``PhaseContext`` carrying pre-built column objects.
        ctx_df_key:  ``"in"`` or ``"out"`` — which context side to resolve from.

    Returns:
        *cfg* mutated in-place and returned (for chaining).
    """
    rules = cfg.get("details", {}).get(list_key)
    if rules:
        upgrade_column_refs(rules, df=df, keys=keys, ctx=ctx, ctx_df_key=ctx_df_key)
        add_rule_priority(rules)
    cfg["execution_order"] = op_order
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# 16. UPGRADE COLUMN LIST  (upgrade a list of column names to column objects)
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_col_list(
    cfg: Dict[str, Any],
    df_out: pd.DataFrame,
    list_key: str = "columns",
    ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Replace plain string column names under ``cfg["details"][list_key]`` with
    full column objects using ``make_column_object()``.

    Used for ``select_columns.details.columns`` and
    ``reorder_columns.details.order`` in Phase 3, where the list holds column
    names rather than rule dicts.

    When a ``PhaseContext`` is provided, pre-built output column objects are
    reused (no repeated dtype inference).

    Args:
        cfg:      Operation config dict.
        df_out:   Output DataFrame used to infer dtype (fallback when no ctx).
        list_key: Key inside ``"details"`` holding the column list.
        ctx:      Optional ``PhaseContext`` carrying pre-built column objects.

    Returns:
        *cfg* mutated in-place and returned (for chaining).
    """
    items = cfg.get("details", {}).get(list_key, [])
    if not items:
        return cfg

    def _resolve(c: str) -> Any:
        if ctx is not None:
            return ctx.col_obj_out(c)
        return make_column_object(c, series=df_out[c] if c in df_out.columns else None)

    cfg["details"][list_key] = [
        _resolve(c) if isinstance(c, str) else c
        for c in items
    ]
    return cfg
