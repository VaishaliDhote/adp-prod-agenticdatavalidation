"""
execution/execution_phase_5_enrichment.py
==========================================
PHASE 5 — Enrichment (Feature Engineering)
--------------------------------------------
Why AFTER row filter? Avoid computing on rows that were removed.

Operations (in order):
  22. Lookup (joins)          ← config["phase_4_enrichment"]["lookup"]
  23. Calculated columns      ← config["phase_4_enrichment"]["calculated_columns"]
  24. Conditional columns     ← config["phase_4_enrichment"]["conditional_columns"]
  25. Text operations         ← config["phase_4_enrichment"]["text_operations"]
  26. Date operations         ← config["phase_4_enrichment"]["date_operations"]
  27. Math operations         ← config["phase_4_enrichment"]["math_operations"]

Config key : "phase_4_enrichment"
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from utils.execution_helpers import col_name, col_exists


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Apply all enrichment operations defined in config["phase_4_enrichment"].

    Args:
        df     : DataFrame produced by Phase 4.
        config : Full merged config dict.

    Returns:
        Enriched DataFrame with new / derived columns.
    """
    phase          = config.get("phase_4_enrichment", {})
    on_missing_col = config.get("error_handling", {}).get("on_missing_column", "skip")
    on_formula_err = config.get("error_handling", {}).get("on_formula_error", "set_null")

    if not phase:
        print("[Phase 5] No enrichment config found — skipping.")
        return df

    df = df.copy()
    print(f"[Phase 5] Starting enrichment. DataFrame shape: {df.shape}, columns: {list(df.columns)}")
    print(f"[Phase 5] Config keys in phase_4_enrichment: {list(phase.keys())}")
    for key in ["lookup", "calculated_columns", "conditional_columns",
                "text_operations", "date_operations", "math_operations"]:
        sub = phase.get(key, {})
        print(f"[Phase 5]   {key}: applied={sub.get('applied', False)}, "
              f"rules={len(sub.get('details', {}).get('rules', []))}")

    # ── 22. Lookup (joins) ────────────────────────────────────────────────────
    lookup_cfg = phase.get("lookup", {})
    if lookup_cfg.get("applied"):
        df = _apply_lookup(df, lookup_cfg, on_missing_col)

    # Build input→output column remap from metadata.column_mapping
    # metadata.column_mapping = {output_col: input_col}  (lineage direction)
    # Invert it so we can translate formula expressions that use input col names.
    # Skip: identity mappings, empty values, and sentinel values for derived cols.
    _SENTINEL_KEYWORDS = ("derived", "multisource", "unknown")
    raw_col_mapping: Dict[str, str] = config.get("metadata", {}).get("column_mapping", {})
    input_to_output: Dict[str, str] = {
        inp: out for out, inp in raw_col_mapping.items()
        if inp and out and inp != out
        and not any(kw in str(inp).lower() for kw in _SENTINEL_KEYWORDS)
    }
    print(f"[Phase 5] Column remap (input→output) for formula translation: {input_to_output}")

    # ── 23. Calculated columns ────────────────────────────────────────────────
    calc_cfg = phase.get("calculated_columns", {})
    if calc_cfg.get("applied"):
        df = _apply_calculated(df, calc_cfg, on_missing_col, on_formula_err, input_to_output)

    # ── 24. Conditional columns ───────────────────────────────────────────────
    cond_cfg = phase.get("conditional_columns", {})
    if cond_cfg.get("applied"):
        df = _apply_conditional(df, cond_cfg, on_missing_col)

    # ── 25. Text operations ───────────────────────────────────────────────────
    text_cfg = phase.get("text_operations", {})
    if text_cfg.get("applied"):
        df = _apply_text_ops(df, text_cfg, on_missing_col)

    # ── 26. Date operations ───────────────────────────────────────────────────
    date_cfg = phase.get("date_operations", {})
    if date_cfg.get("applied"):
        df = _apply_date_ops(df, date_cfg, on_missing_col)

    # ── 27. Math operations ───────────────────────────────────────────────────
    math_cfg = phase.get("math_operations", {})
    if math_cfg.get("applied"):
        df = _apply_math_ops(df, math_cfg, on_missing_col, on_formula_err)

    print(f"[Phase 5] Enrichment complete. Shape: {df.shape}")

    # ── 28. POST-ENRICHMENT DEFERRED FILTERS ──────────────────────────────────
    # Re-run filters now that derived columns exist (filters on derived columns
    # were skipped during Phase 4 because those columns didn't exist yet).
    # Import locally to avoid circular import at module load time.
    try:
        from execution_phases.execution_phase_4_rows import run_deferred_filters
        rows_before = len(df)
        print(f"\n[Phase 5] ═══════════════════════════════════════════════════════════")
        print(f"[Phase 5] Running POST-ENRICHMENT FILTERS (for derived column filters)")
        print(f"[Phase 5] ═══════════════════════════════════════════════════════════")
        df = run_deferred_filters(df, config)
        rows_after = len(df)
        if rows_before != rows_after:
            print(f"[Phase 5] Deferred filters applied: {rows_before} → {rows_after} rows")
        else:
            print(f"[Phase 5] Deferred filters: no additional filtering ({rows_after} rows)")
    except Exception as exc:
        print(f"[Phase 5] Deferred filters failed: {exc}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _formula_expr(formula: Any) -> str:
    """Extract expression string from a plain string or formula-object dict."""
    if isinstance(formula, dict):
        return formula.get("expression", "")
    return str(formula)


# ── 22. Lookup ────────────────────────────────────────────────────────────────

def _apply_lookup(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Perform lookup operations, supporting two modes:
    
    1. VALUE MAPPING (like VLOOKUP to a dictionary):
       Rule shape:
         {"source_column": str, "target_column": str, "mapping": dict,
          "default_value": any}
    
    2. JOIN-STYLE (left-join from external source):
       Rule shape:
         {"left_key": str, "right_key": str, "source": str,
          "columns_added": [str,...], "join_type": "left"|"inner"|...}
    
    When source == "same_sheet" the join is a self-join (no-op in most cases).
    External file sources are loaded with pandas.read_excel / read_csv.
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        # ── VALUE MAPPING MODE ────────────────────────────────────────────────
        # Detect value mapping: has 'source_column', 'target_column', 'mapping'
        if "mapping" in rule and "source_column" in rule:
            df = _apply_value_mapping_lookup(df, rule, on_missing)
            continue
        
        # ── JOIN-STYLE MODE ───────────────────────────────────────────────────
        left_key  = col_name(rule.get("left_key",  ""))
        right_key = col_name(rule.get("right_key", left_key))
        source    = rule.get("source", "same_sheet")
        join_type = rule.get("join_type", "left")
        cols_added = rule.get("columns_added", [])

        if not left_key or not col_exists(df, left_key, on_missing, "Phase 5"):
            continue

        if source == "same_sheet":
            # Self-join: columns_added must already exist in df; nothing to do
            missing = [c for c in cols_added if col_name(c) not in df.columns]
            if missing:
                print(f"  [Phase 5] Lookup (same_sheet): columns {missing} not found — skipping.")
            else:
                print(f"  [Phase 5] Lookup (same_sheet): columns {cols_added} already present.")
            continue

        # External source
        try:
            ref_df = _load_reference(source)
            if right_key not in ref_df.columns:
                print(f"  [Phase 5] Lookup: right_key '{right_key}' not in source '{source}' — skipping.")
                continue

            select_cols = [right_key] + [col_name(c) for c in cols_added if col_name(c) in ref_df.columns]
            ref_subset  = ref_df[list(dict.fromkeys(select_cols))]   # dedupe preserving order

            df = df.merge(
                ref_subset,
                left_on=left_key,
                right_on=right_key,
                how=join_type,
                suffixes=("", "_lookup"),
            )
            print(f"  [Phase 5] Lookup join on '{left_key}' ← '{source}': added {cols_added}")
        except Exception as exc:
            print(f"  [Phase 5] Lookup failed for source '{source}': {exc}")

    return df


def _apply_value_mapping_lookup(df: pd.DataFrame, rule: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Apply a value mapping lookup (like VLOOKUP to a dictionary).
    
    Rule shape:
      {
        "source_column": "Supplier",
        "target_column": "GST_rate",
        "mapping": {"Samsung": 0.07, "Apple": 0.0793, "Dell": 0.1187},
        "default_value": 0.18
      }
    
    This maps values in source_column to create target_column using the mapping dict.
    """
    source_col = col_name(rule.get("source_column", ""))
    target_col = col_name(rule.get("target_column", ""))
    mapping    = rule.get("mapping", {})
    default    = rule.get("default_value")
    
    if not source_col:
        print(f"  [Phase 5] Value mapping lookup skipped: no source_column specified")
        return df
    
    if not target_col:
        print(f"  [Phase 5] Value mapping lookup skipped: no target_column specified")
        return df
    
    if source_col not in df.columns:
        if on_missing == "skip":
            print(f"  [Phase 5] Value mapping lookup skipped: source_column '{source_col}' not found")
            return df
        print(f"  [Phase 5] Value mapping lookup error: source_column '{source_col}' not found")
        return df
    
    if not mapping:
        print(f"  [Phase 5] Value mapping lookup skipped: empty mapping dict")
        return df
    
    # Apply the mapping using pandas map() with fallback to default
    df[target_col] = df[source_col].map(mapping)
    
    # Fill unmapped values with default
    if default is not None:
        df[target_col] = df[target_col].fillna(default)
    
    print(f"  [Phase 5] Value mapping lookup: '{source_col}' → '{target_col}' "
          f"(mapping={list(mapping.keys())}, default={default})")
    
    return df


def _load_reference(source: str) -> pd.DataFrame:
    """Load a reference DataFrame from an external file path."""
    if source.endswith(".csv"):
        return pd.read_csv(source)
    return pd.read_excel(source)


# ── 23. Calculated columns ────────────────────────────────────────────────────

# ── Math functions usable inside formula eval (work on Series & scalars) ────
# numpy ufuncs work on both pandas Series and Python scalars, so we expose
# them under their common Excel / Python names. This lets formulas like
#   floor((pd.Timestamp('2026-08-10') - HireDate).dt.days / 365)
# evaluate vectorised without needing a row-by-row fallback.
_MATH_FUNCTIONS = {
    "floor":  np.floor,
    "ceil":   np.ceil,
    "ceiling": np.ceil,   # Excel alias
    "trunc":  np.trunc,
    "sqrt":   lambda x: np.sqrt(x) if not isinstance(x, (int, float)) else np.sqrt(float(x)),
    "log":    np.log,
    "ln":     np.log,     # Excel alias
    "log10":  np.log10,
    "log2":   np.log2,
    "exp":    np.exp,
    "power":  np.power,   # Excel POWER(x, y)
    "pow":    np.power,
    "sign":   np.sign,
    "mod":    np.mod,
    "sin":    np.sin,
    "cos":    np.cos,
    "tan":    np.tan,
}

# ── Datetime constructors usable inside formula eval ────────────────────────
# LLM-generated formulas often write Python-flavoured date literals such as
#   datetime(2026, 6, 25) - HireDate
# or
#   date(2026, 6, 25)
# Expose the standard-library constructors plus pandas' Timestamp / Timedelta
# so those formulas evaluate without a NameError.
_DATETIME_FUNCTIONS = {
    "datetime":  datetime,
    "date":      date,
    "timedelta": timedelta,
    "Timestamp": pd.Timestamp,
    "Timedelta": pd.Timedelta,
}

# Safe built-ins available inside eval()
_SAFE_BUILTINS = {
    "abs": abs, "round": round, "min": min, "max": max, "len": len,
    "np": np, "pd": pd,
    "int": int, "str": str, "float": float,  # Allow type conversions
    **_MATH_FUNCTIONS,
    **_DATETIME_FUNCTIONS,
}


# ── Timedelta accessor fix ───────────────────────────────────────────────────
# Pandas Series of Timedelta dtype don't expose `.days`, `.seconds`, etc.
# directly — they require the `.dt` accessor. Many LLM-generated formulas
# write `(pd.Timestamp('...') - col).days` which fails when col is a Series.
# We rewrite such expressions to use `.dt.days` before evaluation.
_TIMEDELTA_ATTRS = ("days", "seconds", "microseconds", "nanoseconds")


def _fix_timedelta_accessors(expression: str) -> str:
    """
    Rewrite `(<timedelta expr>).days` → `(<timedelta expr>).dt.days` etc.

    Only adds `.dt` when the attribute is preceded by `)` (i.e. an expression
    result) and not already preceded by `.dt`. This avoids touching scalar
    Timedelta literals like `pd.Timedelta(days=5)`.
    """
    fixed = expression
    for attr in _TIMEDELTA_ATTRS:
        # Match `).days` but NOT `).dt.days`
        pattern = rf'\)\s*\.{attr}\b(?!\s*\()'
        replacement = f').dt.{attr}'
        new = re.sub(pattern, replacement, fixed)
        if new != fixed:
            print(f"    [Phase 5] Rewrote timedelta accessor: '.{attr}' → '.dt.{attr}'")
            fixed = new
    # Also handle `).total_seconds()` — this one IS valid on Series via .dt,
    # but if user writes `(a - b).total_seconds()` we need `.dt.total_seconds()`
    pattern_ts = r'\)\s*\.total_seconds\s*\('
    if re.search(pattern_ts, fixed) and '.dt.total_seconds' not in fixed:
        fixed = re.sub(pattern_ts, ').dt.total_seconds(', fixed)
        print(f"    [Phase 5] Rewrote timedelta accessor: '.total_seconds()' → '.dt.total_seconds()'")
    return fixed


# ── Excel-style function helpers ─────────────────────────────────────────────
def _excel_concat(*args) -> Any:
    """Excel CONCAT / CONCATENATE — joins all args as strings.
    Works on both scalars and pandas Series (broadcasts element-wise)."""
    series_args = [a for a in args if isinstance(a, pd.Series)]
    if series_args:
        idx = series_args[0].index
        result = pd.Series([""] * len(idx), index=idx)
        for a in args:
            if isinstance(a, pd.Series):
                result = result + a.fillna("").astype(str)
            else:
                result = result + str(a)
        return result
    return "".join(str(a) for a in args)


def _excel_if(cond, t, f) -> Any:
    """Excel IF function. Works on scalars and Series."""
    if isinstance(cond, pd.Series):
        return pd.Series(np.where(cond, t, f), index=cond.index)
    return t if cond else f


# Excel-style functions exposed to eval()
_EXCEL_FUNCTIONS = {
    "CONCAT":       _excel_concat,
    "CONCATENATE":  _excel_concat,
    "IF":           _excel_if,
    "UPPER":        lambda s: s.str.upper() if isinstance(s, pd.Series) else str(s).upper(),
    "LOWER":        lambda s: s.str.lower() if isinstance(s, pd.Series) else str(s).lower(),
    "TRIM":         lambda s: s.str.strip() if isinstance(s, pd.Series) else str(s).strip(),
    "LEN":          lambda s: s.str.len() if isinstance(s, pd.Series) else len(str(s)),
    "AND":          lambda *a: np.logical_and.reduce(a),
    "OR":           lambda *a: np.logical_or.reduce(a),
    "NOT":          lambda x: ~x if isinstance(x, pd.Series) else not x,
}


_IF_PATTERN = re.compile(
    r"^IF\(\s*(.+?)\s*,\s*(.+?)\s*,\s*(.+?)\s*\)$",
    re.IGNORECASE | re.DOTALL,
)


def _convert_excel_if(expression: str) -> Optional[str]:
    """
    Convert Excel-style IF(cond, true_val, false_val) to a pandas-eval
    compatible expression using numpy where syntax.

    Examples:
      IF(TotalAmount > 14250, TotalAmount * 0.10, 0)
      → (TotalAmount * 0.10) if (TotalAmount > 14250) else 0   [row-by-row]

    Returns the converted expression or None if not an IF pattern.
    """
    m = _IF_PATTERN.match(expression.strip())
    if not m:
        return None
    condition, true_val, false_val = m.group(1), m.group(2), m.group(3)
    print(f"    [Phase 5] Converted Excel IF → condition='{condition}', "
          f"true='{true_val}', false='{false_val}'")
    # Return as a tuple so caller knows to use np.where / row-by-row
    return (condition, true_val, false_val)


_RANGE_PATTERN = re.compile(r'^range\s*\(\s*(.+?)\s*,\s*(.+?)\s*\)$')


def _eval_sequence(df: pd.DataFrame, expression: str) -> Optional[pd.Series]:
    """
    Handle sequence expressions like range(1, len(EID) + 1) by evaluating
    start/stop with len(df) substituted for len(<any_col>).
    """
    expr = expression.strip()
    m = _RANGE_PATTERN.match(expr)
    if not m:
        return None
    # len() always returns len(df) regardless of argument so that
    # range(1, len(EID) + 1) works even when EID is not in the DataFrame.
    _n = len(df)
    ns = {col: df[col] for col in df.columns}
    ns["len"] = lambda _=None: _n
    ns["range"] = range
    ns["np"] = np
    # Proxy for any identifier in the formula that isn't a real column
    # (e.g. EID renamed to EmployeeID). Only needs to be passable to len().
    class _ColProxy:
        pass
    ns = {**ns, **{tok: _ColProxy() for tok in re.findall(r'\b([A-Za-z_]\w*)\b', expression)
                   if tok not in ns and tok not in {"range", "len", "np"}}}
    try:
        start = eval(m.group(1), {"__builtins__": {}}, ns)  # noqa: S307
        stop  = eval(m.group(2), {"__builtins__": {}}, ns)  # noqa: S307
        return pd.Series(range(int(start), int(stop)), index=df.index)
    except Exception as exc:
        print(f"    [Phase 5] _eval_sequence failed: {exc}")
        return None


def _eval_formula(df: pd.DataFrame, expression: str, on_error: str) -> pd.Series:
    """
    Evaluate a pandas-style expression against the DataFrame.

    Handles:
      1. Excel-style IF(cond, true_val, false_val) via numpy where
      2. np.where() expressions with Series methods like .isin()
      3. pandas df.eval() for standard arithmetic expressions
      4. Row-by-row Python eval() fallback with IF as a built-in
    """
    print(f"    [Phase 5] _eval_formula: expression='{expression}'")
    print(f"    [Phase 5] DataFrame columns available: {list(df.columns)}")

    # ── Preprocess: rewrite `.days` → `.dt.days` for Series timedelta exprs ──
    fixed_expression = _fix_timedelta_accessors(expression)
    if fixed_expression != expression:
        expression = fixed_expression
        print(f"    [Phase 5] Expression after timedelta fix: '{expression}'")

    # ── 0. Fast path: Excel-style functions (CONCAT, CONCATENATE, UPPER, …) ──
    # If the expression uses any of our Excel function names, evaluate directly
    # in a namespace that has those functions and the DataFrame columns.
    if re.search(r'\b(' + '|'.join(_EXCEL_FUNCTIONS.keys()) + r')\s*\(', expression):
        try:
            local_ns = {col: df[col] for col in df.columns}
            local_ns.update(_EXCEL_FUNCTIONS)
            local_ns.update(_MATH_FUNCTIONS)
            local_ns.update(_DATETIME_FUNCTIONS)
            local_ns["np"] = np
            local_ns["pd"] = pd
            result = eval(expression, {"__builtins__": {}}, local_ns)  # noqa: S307
            if isinstance(result, (pd.Series, np.ndarray)):
                result = pd.Series(result, index=df.index)
            elif not isinstance(result, pd.Series):
                result = pd.Series([result] * len(df), index=df.index)
            print(f"    [Phase 5] Excel-function eval succeeded: {result.values}")
            return result
        except Exception as exc:
            print(f"    [Phase 5] Excel-function eval failed: {exc} — falling back")

    # ── 1. Handle Excel IF() syntax ──────────────────────────────────────────
    if_parts = _convert_excel_if(expression)
    if if_parts is not None:
        condition, true_val, false_val = if_parts
        try:
            cond_series  = df.eval(condition)
            true_series  = df.eval(true_val)  if any(c in true_val  for c in df.columns) else float(true_val)
            false_series = df.eval(false_val) if any(c in false_val for c in df.columns) else float(false_val)
            result = pd.Series(
                np.where(cond_series, true_series, false_series),
                index=df.index,
            )
            print(f"    [Phase 5] IF via np.where succeeded: {result.values}")
            return result
        except Exception as exc:
            print(f"    [Phase 5] IF via np.where failed ({exc}), trying row-by-row")

        # Row-by-row fallback for IF
        def _safe_if_eval(row: pd.Series) -> Any:
            try:
                local_vars = dict(row)
                local_vars.update(_SAFE_BUILTINS)
                local_vars["IF"] = lambda cond, t, f: t if cond else f
                cond_val = eval(condition,  {"__builtins__": {}}, local_vars)  # noqa: S307
                true_v   = eval(true_val,   {"__builtins__": {}}, local_vars)  # noqa: S307
                false_v  = eval(false_val,  {"__builtins__": {}}, local_vars)  # noqa: S307
                return true_v if cond_val else false_v
            except Exception as e:
                print(f"    [Phase 5] IF row-by-row eval failed: {e}")
                return np.nan if on_error == "set_null" else None

        return df.apply(_safe_if_eval, axis=1)

    # ── 2. Handle pd.* vectorized expressions (e.g. pd.cut, pd.to_datetime) ──
    # Also triggers when the expression uses math helpers like floor/ceil even
    # without an explicit `pd.` prefix, because those need a namespace richer
    # than what df.eval() provides.
    _math_func_pattern = r'\b(' + '|'.join(_MATH_FUNCTIONS.keys()) + r')\s*\('
    has_math_func = bool(re.search(_math_func_pattern, expression))
    if (re.search(r'\bpd\.', expression) or has_math_func) \
            and "np.where" not in expression and "np.select" not in expression:
        try:
            local_ns = {col: df[col] for col in df.columns}
            local_ns["np"] = np
            local_ns["pd"] = pd
            local_ns["float"] = float
            local_ns["int"] = int
            local_ns["str"] = str
            local_ns.update(_MATH_FUNCTIONS)
            local_ns.update(_DATETIME_FUNCTIONS)
            # Auto-convert columns to datetime in these cases:
            # 1. Expression uses .dt accessor directly on column: col.dt.year
            # 2. Expression involves date arithmetic with pd.Timestamp: pd.Timestamp(...) - col
            # 3. Expression has .dt. accessor on result of arithmetic involving the column
            for col in df.columns:
                should_convert = False
                
                # Case 1: Direct .dt accessor on column
                if re.search(rf'\b{re.escape(col)}\.dt\.', expression):
                    should_convert = True
                
                # Case 2: Date arithmetic pattern - pd.Timestamp(...) +/- column
                # Matches: pd.Timestamp('...') - HireDate, pd.Timestamp("...") + col, etc.
                if re.search(rf'pd\.Timestamp\s*\([^)]+\)\s*[-+]\s*{re.escape(col)}\b', expression):
                    should_convert = True
                # Also match: column - pd.Timestamp(...)
                if re.search(rf'\b{re.escape(col)}\s*[-+]\s*pd\.Timestamp\s*\(', expression):
                    should_convert = True
                
                # Case 3: Expression has .dt. somewhere and column is mentioned (likely date arithmetic result)
                if '.dt.' in expression and re.search(rf'\b{re.escape(col)}\b', expression):
                    # Check if the dtype looks like it could be a date
                    col_dtype = str(df[col].dtype)
                    col_sample = df[col].dropna().astype(str).head(1).values
                    if 'date' in col_dtype.lower() or 'time' in col_dtype.lower():
                        should_convert = True
                    # Heuristic: if column name suggests date and expression has .dt.
                    elif any(kw in col.lower() for kw in ['date', 'time', 'hire', 'start', 'end', 'created', 'updated', 'dob', 'birth']):
                        should_convert = True
                
                if should_convert:
                    converted = pd.to_datetime(df[col], errors="coerce")
                    if converted.notna().any():
                        local_ns[col] = converted
                        print(f"    [Phase 5] Auto-converted '{col}' to datetime for date arithmetic")
            
            result = eval(expression, {"__builtins__": {}}, local_ns)  # noqa: S307
            if isinstance(result, (pd.Categorical, np.ndarray)):
                result = pd.Series(result, index=df.index)
            elif not isinstance(result, pd.Series):
                result = pd.Series([result] * len(df), index=df.index)
            print(f"    [Phase 5] pd.* vectorized eval succeeded: {result.values}")
            return result
        except Exception as exc:
            print(f"    [Phase 5] pd.* vectorized eval failed: {exc} — falling back")

    # ── 3. Handle np.where() expressions with Series methods (e.g., .isin()) ──
    if "np.where" in expression or "np.select" in expression:
        try:
            # Build local namespace with DataFrame columns as Series and numpy/pandas
            local_ns = {col: df[col] for col in df.columns}
            local_ns["np"] = np
            local_ns["pd"] = pd
            local_ns.update(_MATH_FUNCTIONS)
            local_ns.update(_DATETIME_FUNCTIONS)
            result = eval(expression, {"__builtins__": {}}, local_ns)  # noqa: S307
            if isinstance(result, (pd.Series, np.ndarray)):
                result = pd.Series(result, index=df.index)
                print(f"    [Phase 5] np.where/np.select eval succeeded: {result.values}")
                return result
            else:
                # Scalar result
                print(f"    [Phase 5] np.where/np.select eval returned scalar: {result}")
                return pd.Series([result] * len(df), index=df.index)
        except Exception as exc:
            print(f"    [Phase 5] np.where/np.select eval failed: {exc}")
            if on_error == "set_null":
                return pd.Series([np.nan] * len(df), index=df.index)
            raise

    # ── 3. Handle Series accessor expressions (.dt, .str) ─────────────────────
    # These need special handling because df.eval() doesn't support them well
    dt_accessor_match = re.match(r'^([A-Za-z_]\w*)\.dt\.(year|month|day|hour|minute|second|dayofweek|dayofyear|quarter|weekday)$', expression.strip())
    if dt_accessor_match:
        col_name, attr = dt_accessor_match.groups()
        if col_name in df.columns:
            try:
                # Auto-convert to datetime if needed
                col_series = pd.to_datetime(df[col_name], errors='coerce')
                result = getattr(col_series.dt, attr)
                print(f"    [Phase 5] .dt accessor succeeded: {result.values}")
                return result
            except Exception as exc:
                print(f"    [Phase 5] .dt accessor failed: {exc}")

    # ── Handle .map({...}) expressions (e.g. PerformanceRating.map({4: 'A', 5: 'B'})) ──
    if '.map(' in expression:
        try:
            local_ns = {col: df[col] for col in df.columns}
            local_ns.update(_SAFE_BUILTINS)
            result = eval(expression, {"__builtins__": {}}, local_ns)  # noqa: S307
            if not isinstance(result, pd.Series):
                result = pd.Series(result, index=df.index)
            print(f"    [Phase 5] .map() eval succeeded: {result.values}")
            return result
        except Exception as exc:
            print(f"    [Phase 5] .map() eval failed: {exc}")

    # Handle more complex Series expressions (e.g., col.str.split(...), col.astype(...))
    # by evaluating with columns in namespace
    if '.str.' in expression or '.astype(' in expression or '.dt.' in expression:
        try:
            local_ns = {col: df[col] for col in df.columns}
            local_ns.update(_SAFE_BUILTINS)
            local_ns["pd"] = pd  # Ensure pd is available
            
            # Auto-convert datetime columns for .dt accessor or date arithmetic
            for col in df.columns:
                should_convert = False
                # Direct .dt accessor
                if f"{col}.dt." in expression:
                    should_convert = True
                # Date arithmetic with pd.Timestamp
                if re.search(rf'pd\.Timestamp\s*\([^)]+\)\s*[-+]\s*{re.escape(col)}\b', expression):
                    should_convert = True
                if re.search(rf'\b{re.escape(col)}\s*[-+]\s*pd\.Timestamp\s*\(', expression):
                    should_convert = True
                # .dt. in expression and column mentioned (likely date arithmetic result)
                if '.dt.' in expression and re.search(rf'\b{re.escape(col)}\b', expression):
                    if any(kw in col.lower() for kw in ['date', 'time', 'hire', 'start', 'end', 'created', 'updated', 'dob', 'birth']):
                        should_convert = True
                
                if should_convert:
                    converted = pd.to_datetime(df[col], errors='coerce')
                    if converted.notna().any():
                        local_ns[col] = converted
                        print(f"    [Phase 5] Auto-converted '{col}' to datetime")
            
            result = eval(expression, {"__builtins__": {}}, local_ns)  # noqa: S307
            if isinstance(result, (pd.Series, np.ndarray)):
                result = pd.Series(result, index=df.index)
            print(f"    [Phase 5] Series accessor eval succeeded: {result.values if hasattr(result, 'values') else result}")
            return result
        except Exception as exc:
            print(f"    [Phase 5] Series accessor eval failed: {exc}")

    # ── 4. Standard pandas eval ───────────────────────────────────────────────
    try:
        result = df.eval(expression)
        print(f"    [Phase 5] df.eval() succeeded: {result.values}")
        return result
    except Exception as exc:
        print(f"    [Phase 5] df.eval() failed: {exc} — falling back to row-by-row eval")

    # ── 5. Row-by-row Python eval fallback ────────────────────────────────────
    # Identify columns that need datetime conversion for date arithmetic
    date_cols_to_convert = set()
    for col in df.columns:
        # Date arithmetic with pd.Timestamp
        if re.search(rf'pd\.Timestamp\s*\([^)]+\)\s*[-+]\s*{re.escape(col)}\b', expression):
            date_cols_to_convert.add(col)
        if re.search(rf'\b{re.escape(col)}\s*[-+]\s*pd\.Timestamp\s*\(', expression):
            date_cols_to_convert.add(col)
        # .dt. in expression and column mentioned
        if '.dt.' in expression and re.search(rf'\b{re.escape(col)}\b', expression):
            if any(kw in col.lower() for kw in ['date', 'time', 'hire', 'start', 'end', 'created', 'updated', 'dob', 'birth']):
                date_cols_to_convert.add(col)
    
    def _safe_eval(row: pd.Series) -> Any:
        try:
            local_vars = dict(row)
            # Convert date columns to datetime/Timestamp
            for col in date_cols_to_convert:
                if col in local_vars:
                    try:
                        local_vars[col] = pd.to_datetime(local_vars[col])
                    except:
                        pass
            local_vars.update(_SAFE_BUILTINS)
            local_vars["IF"] = lambda cond, t, f: t if cond else f
            return eval(expression, {"__builtins__": {}}, local_vars)   # noqa: S307
        except Exception as e:
            print(f"    [Phase 5] Row-by-row eval failed on row: {e}")
            return np.nan if on_error == "set_null" else None

    return df.apply(_safe_eval, axis=1)


def _remap_formula_columns(
    formula: str,
    input_to_output: Dict[str, str],
    df_columns: Optional[set] = None,
) -> str:
    """
    Replace input column names in a formula expression with their output column
    names, using whole-word matching to avoid partial replacements.

    IMPORTANT: Only remaps a column reference if the original column does NOT exist
    in the DataFrame but the mapped (output) column DOES exist. This prevents
    breaking formulas that reference columns that still have data.

    E.g. with {"UnitPrice": "Price"} and df_columns={"Price", "Quantity"}:
      "Quantity * UnitPrice" → "Quantity * Price"  (UnitPrice not in df, Price is)

    But with df_columns={"UnitPrice", "Price", "Quantity"}:
      "Quantity * UnitPrice" → "Quantity * UnitPrice"  (UnitPrice still exists, no remap)
    """
    if not input_to_output:
        return formula
    # Sort by length descending to avoid partial replacements (e.g. "Price" inside "UnitPrice")
    for inp_col in sorted(input_to_output, key=len, reverse=True):
        out_col = input_to_output[inp_col]
        # Only remap if: original column NOT in df, AND mapped column IS in df
        if df_columns is not None:
            if inp_col in df_columns:
                # Original column still exists with data - don't remap
                continue
            if out_col not in df_columns:
                # Mapped column doesn't exist either - don't remap
                continue
        formula = re.sub(rf'\b{re.escape(inp_col)}\b', out_col, formula)
    return formula


def _apply_calculated(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
    on_formula_err: str,
    input_to_output: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))
    col_remap = input_to_output or {}

    print(f"  [Phase 5] _apply_calculated: {len(rules)} rules to process")
    print(f"  [Phase 5] DataFrame shape before: {df.shape}, columns: {list(df.columns)}")

    for idx, rule in enumerate(rules):
        raw_col  = rule.get("column", "")
        out_col  = col_name(raw_col)
        formula  = _formula_expr(rule.get("formula", ""))
        exec_ord = rule.get("execution_order", "?")
        f_type   = rule.get("formula_type", "")
        depends  = rule.get("depends_on_derived", False)

        # Translate formula from input col names → output col names
        # Only remap if the original column no longer exists but the mapped column does
        remapped_formula = _remap_formula_columns(formula, col_remap, set(df.columns))
        if remapped_formula != formula:
            print(f"  [Phase 5]   ↔ Formula remapped: '{formula}' → '{remapped_formula}' (via column_mapping)")
        formula = remapped_formula

        print(f"\n  [Phase 5] Rule [{idx+1}] exec_order={exec_ord} col_raw={raw_col!r} "
              f"→ out_col='{out_col}' formula='{formula}' type='{f_type}' depends_on_derived={depends}")

        if not out_col:
            print(f"  [Phase 5]   ✗ Skipping: empty column name extracted from {raw_col!r}")
            continue
        if not formula:
            print(f"  [Phase 5]   ✗ Skipping: empty formula for column '{out_col}'")
            continue

        # Check that all referenced columns exist in df
        # (important when formula references a derived column from a prior rule)
        missing_deps = []
        for token in re.findall(r'\b([A-Za-z_]\w*)\b', formula):
            if token in df.columns:
                continue
            # skip numeric literals, operators, keywords, built-in names
            if token in {"IF", "AND", "OR", "NOT", "TRUE", "FALSE",
                         "pd", "np", "float", "int", "str", "len", "abs", "round",
                         "cut", "bins", "labels", "right", "inf", "True", "False",
                         "where", "select", "nan", "isin", "astype",
                         "Timestamp", "Timedelta", "to_datetime", "to_numeric",
                         "dt", "days", "seconds", "microseconds", "total_seconds",
                         "year", "month", "day", "hour", "minute", "second",
                         "dayofweek", "dayofyear", "quarter", "weekday",
                         "map", "fillna", "replace"} or token.lstrip('-').replace('.','',1).isnumeric():
                continue
            # Also skip Excel-style function names we support
            if token in _EXCEL_FUNCTIONS:
                continue
            # Skip math helpers (floor, ceil, sqrt, log, …)
            if token in _MATH_FUNCTIONS:
                continue
            # Only warn if it looks like it could be a column name
            if not token[0].isdigit():
                missing_deps.append(token)

        if missing_deps:
            print(f"  [Phase 5]   ⚠ Possibly missing columns in formula: {missing_deps} "
                  f"(available: {list(df.columns)})")

        try:
            # ── Sequence formula (e.g. range(1, len(col) + 1)) ──────────────
            is_sequence = f_type == "sequence" or re.match(r'^range\s*\(', formula.strip())
            if is_sequence:
                result = _eval_sequence(df, formula)
                if result is not None:
                    df[out_col] = result
                    print(f"  [Phase 5]   ✓ '{out_col}' computed (sequence): {df[out_col].values}")
                else:
                    print(f"  [Phase 5]   ✗ '{out_col}' sequence eval returned None — skipping.")
                continue

            result = _eval_formula(df, formula, on_formula_err)
            # Don't overwrite an already-populated column with all-null results
            # (e.g. Phase 3 split already filled Email_Domain, City, etc.)
            result_empty = pd.isna(result).all() if hasattr(result, '__iter__') else pd.isna(result)
            col_has_data = out_col in df.columns and df[out_col].notna().any()
            if result_empty and col_has_data:
                print(f"  [Phase 5]   ⚠ '{out_col}' formula returned all-null but column already has data — skipping overwrite.")
            else:
                df[out_col] = result
                print(f"  [Phase 5]   ✓ '{out_col}' computed: {df[out_col].values}")
        except Exception as exc:
            print(f"  [Phase 5]   ✗ '{out_col}' failed: {exc}")
            if on_formula_err == "set_null" and out_col not in df.columns:
                df[out_col] = np.nan

    print(f"\n  [Phase 5] _apply_calculated done. Shape: {df.shape}, columns: {list(df.columns)}")
    return df


# ── 24. Conditional columns ───────────────────────────────────────────────────

def _apply_conditional(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Create a column based on if/elif/else conditions.

    Rule shape:
      {"column": str,
       "conditions": [
         {"when": "expr", "then": value_or_expr},
         ...
       ],
       "else": default_value}
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        out_col    = col_name(rule.get("column", ""))
        conditions = rule.get("conditions", [])
        default    = rule.get("else", np.nan)

        if not out_col:
            continue

        result = pd.Series([default] * len(df), index=df.index)

        # Apply in reverse so first matching condition wins
        for cond in reversed(conditions):
            when_expr = cond.get("when", "")
            then_val  = cond.get("then", np.nan)

            if not when_expr:
                continue
            try:
                mask = df.eval(when_expr)
                if isinstance(then_val, str) and then_val in df.columns:
                    result[mask] = df.loc[mask, then_val]
                else:
                    result[mask] = then_val
            except Exception as exc:
                print(f"  [Phase 5] Conditional '{out_col}' condition '{when_expr}' failed: {exc}")

        # Don't overwrite an already-populated column with all-null results
        result_empty = pd.isna(result).all() if hasattr(result, '__iter__') else pd.isna(result)
        col_has_data = out_col in df.columns and df[out_col].notna().any()
        if result_empty and col_has_data:
            print(f"  [Phase 5]   ⚠ Conditional '{out_col}' returned all-null but column already has data — skipping overwrite.")
        else:
            df[out_col] = result
            print(f"  [Phase 5] Conditional column '{out_col}' created ({len(conditions)} conditions)")

    return df


# ── 25. Text operations ───────────────────────────────────────────────────────

_TEXT_OP_MAP = {
    "upper":      lambda s: s.str.upper(),
    "lower":      lambda s: s.str.lower(),
    "title":      lambda s: s.str.title(),
    "trim":       lambda s: s.str.strip(),
    "ltrim":      lambda s: s.str.lstrip(),
    "rtrim":      lambda s: s.str.rstrip(),
    "length":     lambda s: s.str.len(),
    "reverse":    lambda s: s.str[::-1],
    "capitalize": lambda s: s.str.capitalize(),
}


def _apply_text_ops(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Apply text transformations.

    Rule shapes supported:
    
    1. Standard (transform existing column):
      {"column": str, "operation": str,
       "output_column": str (optional — defaults to same column),
       "args": {...}}
    
    2. Derived column (create new column from source columns):
      {"column": str (OUTPUT column name),
       "operation": str,
       "columns": [str, ...] (source columns),
       "separator": str (for concatenate)}
       
    When 'columns' list is provided, 'column' is treated as the OUTPUT name.
    For operations like upper/lower on multiple sources, they are concatenated first.
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        col        = col_name(rule.get("column", ""))
        operation  = rule.get("operation", "").lower()
        out_col    = rule.get("output_column", col) or col
        args       = rule.get("args", {})
        source_cols = rule.get("columns", [])
        separator  = rule.get("separator", args.get("separator", ""))

        # ── Handle derived column pattern (columns list provided) ─────────────
        # When 'columns' list is present, 'column' is the OUTPUT, sources are in 'columns'
        if source_cols:
            # Resolve column names from dicts if needed
            resolved_cols = []
            for c in source_cols:
                resolved = col_name(c) if isinstance(c, dict) else str(c)
                resolved_cols.append(resolved)
            
            # Check all source columns exist
            missing = [c for c in resolved_cols if c not in df.columns]
            if missing:
                print(f"  [Phase 5] Text op '{operation}' missing source columns {missing} — skipping.")
                continue
            
            try:
                # Build the source series (concatenate if multiple, or use single)
                if len(resolved_cols) == 1:
                    series = df[resolved_cols[0]].astype(str)
                else:
                    # Concatenate multiple source columns
                    series = df[resolved_cols[0]].astype(str)
                    for c in resolved_cols[1:]:
                        series = series + separator + df[c].astype(str)
                
                # Apply the operation to the combined series
                if operation == "concatenate" or operation == "concat":
                    # Already concatenated above
                    df[out_col] = series
                elif operation in _TEXT_OP_MAP:
                    df[out_col] = _TEXT_OP_MAP[operation](series)
                elif operation == "replace":
                    df[out_col] = series.str.replace(
                        str(args.get("pattern", "")),
                        str(args.get("replacement", "")),
                        regex=args.get("regex", False),
                    )
                elif operation == "extract":
                    df[out_col] = series.str.extract(str(args.get("pattern", "(.*)")), expand=False)
                elif operation == "pad":
                    df[out_col] = series.str.pad(
                        width=int(args.get("width", 10)),
                        side=args.get("side", "right"),
                        fillchar=str(args.get("fillchar", " ")),
                    )
                elif operation == "slice":
                    start = args.get("start", 0)
                    stop  = args.get("stop")
                    df[out_col] = series.str[start:stop]
                else:
                    # Default: just use the concatenated/single source
                    df[out_col] = series
                
                print(f"  [Phase 5] Text op '{operation}' {resolved_cols} → '{out_col}'")
            except Exception as exc:
                print(f"  [Phase 5] Text op '{operation}' on derived column failed: {exc}")
            continue

        # ── Standard text operations (source column must exist) ───────────────
        if not col or not col_exists(df, col, on_missing, "Phase 5"):
            continue

        series = df[col].astype(str)

        try:
            if operation in _TEXT_OP_MAP:
                df[out_col] = _TEXT_OP_MAP[operation](series)

            elif operation == "replace":
                df[out_col] = series.str.replace(
                    str(args.get("pattern", "")),
                    str(args.get("replacement", "")),
                    regex=args.get("regex", False),
                )
            elif operation == "extract":
                df[out_col] = series.str.extract(str(args.get("pattern", "(.*)")), expand=False)

            elif operation == "pad":
                df[out_col] = series.str.pad(
                    width=int(args.get("width", 10)),
                    side=args.get("side", "right"),
                    fillchar=str(args.get("fillchar", " ")),
                )
            elif operation == "slice":
                start = args.get("start", 0)
                stop  = args.get("stop")
                df[out_col] = series.str[start:stop]

            elif operation in ("concat", "concatenate"):
                other_col = args.get("with", "")
                sep       = args.get("separator", separator)
                if other_col and other_col in df.columns:
                    df[out_col] = series + sep + df[other_col].astype(str)
                else:
                    df[out_col] = series + sep + str(args.get("value", ""))

            else:
                print(f"  [Phase 5] Unknown text operation '{operation}' — skipping.")
                continue

            print(f"  [Phase 5] Text op '{operation}' on '{col}' → '{out_col}'")

        except Exception as exc:
            print(f"  [Phase 5] Text op '{operation}' on '{col}' failed: {exc}")

    return df


# ── 26. Date operations ───────────────────────────────────────────────────────

def _apply_date_ops(df: pd.DataFrame, cfg: Dict[str, Any], on_missing: str) -> pd.DataFrame:
    """
    Apply date/time derivations.

    Rule shape:
      {"column": str, "operation": str, "output_column": str, "args": {...}}

    Supported operations:
      extract_year, extract_month, extract_day, extract_weekday,
      extract_quarter, extract_hour, extract_minute,
      date_diff (requires args.other_column or args.reference_date),
      add_days, format
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        col       = col_name(rule.get("column", ""))
        operation = rule.get("operation", "").lower()
        out_col   = rule.get("output_column", f"{col}_{operation}") or f"{col}_{operation}"
        args      = rule.get("args", {})

        if not col or not col_exists(df, col, on_missing, "Phase 5"):
            continue

        try:
            dt_series = pd.to_datetime(df[col], errors="coerce")

            if operation == "extract_year":
                df[out_col] = dt_series.dt.year
            elif operation == "extract_month":
                df[out_col] = dt_series.dt.month
            elif operation == "extract_day":
                df[out_col] = dt_series.dt.day
            elif operation == "extract_weekday":
                df[out_col] = dt_series.dt.day_name()
            elif operation == "extract_quarter":
                df[out_col] = dt_series.dt.quarter
            elif operation == "extract_hour":
                df[out_col] = dt_series.dt.hour
            elif operation == "extract_minute":
                df[out_col] = dt_series.dt.minute
            elif operation == "date_diff":
                other_col = args.get("other_column", "")
                ref_date  = args.get("reference_date")
                unit      = args.get("unit", "days")
                if other_col and other_col in df.columns:
                    other_dt = pd.to_datetime(df[other_col], errors="coerce")
                elif ref_date:
                    other_dt = pd.to_datetime(ref_date)
                else:
                    print(f"  [Phase 5] date_diff: no other_column or reference_date — skipping.")
                    continue
                diff = (dt_series - other_dt)
                if unit == "days":
                    df[out_col] = diff.dt.days
                elif unit == "hours":
                    df[out_col] = diff.dt.total_seconds() / 3600
                elif unit == "months":
                    df[out_col] = (dt_series.dt.year - other_dt.dt.year) * 12 + \
                                  (dt_series.dt.month - other_dt.dt.month) \
                                  if hasattr(other_dt, "dt") else \
                                  (dt_series.dt.year - other_dt.year) * 12 + \
                                  (dt_series.dt.month - other_dt.month)
            elif operation == "add_days":
                days = int(args.get("days", 0))
                df[out_col] = dt_series + pd.Timedelta(days=days)
            elif operation == "format":
                fmt = args.get("format", "%Y-%m-%d")
                df[out_col] = dt_series.dt.strftime(fmt)
            else:
                print(f"  [Phase 5] Unknown date operation '{operation}' — skipping.")
                continue

            print(f"  [Phase 5] Date op '{operation}' on '{col}' → '{out_col}'")

        except Exception as exc:
            print(f"  [Phase 5] Date op '{operation}' on '{col}' failed: {exc}")

    return df


# ── 27. Math operations ───────────────────────────────────────────────────────

def _apply_math_ops(
    df: pd.DataFrame,
    cfg: Dict[str, Any],
    on_missing: str,
    on_formula_err: str,
) -> pd.DataFrame:
    """
    Apply math/statistical operations.

    Rule shape:
      {"column": str, "operation": str,
       "output_column": str,
       "formula": str | {"expression": str, ...},
       "args": {...}}

    Supported: round, abs, sqrt, log, log10, exp, cumsum, cumprod,
               pct_change, zscore, normalize, custom (via formula)
    """
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    rules = sorted(rules, key=lambda r: r.get("execution_order", 999))

    for rule in rules:
        col       = col_name(rule.get("column", ""))
        operation = rule.get("operation", "").lower()
        out_col   = rule.get("output_column", col) or col
        args      = rule.get("args", {})
        formula   = _formula_expr(rule.get("formula", ""))

        if not col or not col_exists(df, col, on_missing, "Phase 5"):
            continue

        try:
            numeric = pd.to_numeric(df[col], errors="coerce")

            if operation == "round":
                df[out_col] = numeric.round(int(args.get("decimals", 2)))
            elif operation == "abs":
                df[out_col] = numeric.abs()
            elif operation == "sqrt":
                df[out_col] = np.sqrt(numeric.clip(lower=0))
            elif operation == "log":
                df[out_col] = np.log(numeric.clip(lower=1e-9))
            elif operation == "log10":
                df[out_col] = np.log10(numeric.clip(lower=1e-9))
            elif operation == "exp":
                df[out_col] = np.exp(numeric)
            elif operation == "cumsum":
                df[out_col] = numeric.cumsum()
            elif operation == "cumprod":
                df[out_col] = numeric.cumprod()
            elif operation == "pct_change":
                df[out_col] = numeric.pct_change()
            elif operation == "zscore":
                mean, std = numeric.mean(), numeric.std()
                df[out_col] = (numeric - mean) / std if std != 0 else 0.0
            elif operation == "normalize":
                mn, mx = numeric.min(), numeric.max()
                df[out_col] = (numeric - mn) / (mx - mn) if mx != mn else 0.0
            elif operation == "custom" and formula:
                df[out_col] = _eval_formula(df, formula, on_formula_err)
            else:
                print(f"  [Phase 5] Unknown math operation '{operation}' — skipping.")
                continue

            print(f"  [Phase 5] Math op '{operation}' on '{col}' → '{out_col}'")

        except Exception as exc:
            print(f"  [Phase 5] Math op '{operation}' on '{col}' failed: {exc}")
            if on_formula_err == "set_null":
                df[out_col] = np.nan

    return df
