# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Finalization Transformation Utilities
========================================
Three focused builder functions for Phase 9 finalization detection.

  build_formula_to_value_config           → 9.1  FORMULA → VALUE
                                                   (columns whose live formula
                                                    cells were frozen as static
                                                    values — Excel "Paste Special
                                                    → Values")
  build_remove_intermediate_columns_config → 9.2  REMOVE INTERMEDIATE COLUMNS
                                                   (helper / staging / temp columns
                                                    present in input that were
                                                    stripped before the final save)
  build_final_save_config                 → 9.3  FINAL SAVE / STRUCTURE FREEZE
                                                   (output format, sheet freeze,
                                                    header lock, print-area flags)

Each function accepts (input_df, output_df, column_mapping?) and returns a
self-contained config block consumed directly by phase_9_final.detect().

Design principles
-----------------
- Row-level verification: formula→value detection compares value stability
  across columns that changed dtype or lost formula-like patterns.
- Excel-aware dtype handling: currency ($), percentage (%), date strings,
  boolean text ("TRUE"/"FALSE"), #REF!, #N/A, =SUM(…) cell content.
- column_mapping is honoured: detection runs in input namespace, output
  namespace is restored before returning.
- Applied only when evidence is conclusive; returns applied=False otherwise.

Output schema
-------------
{
  "formula_to_value": {
    "applied": bool,
    "details": {
      "columns": [str, ...]      # columns where formula→value was applied
    }
  },
  "remove_intermediate_columns": {
    "applied": bool,
    "details": {
      "columns": [str, ...]      # intermediate columns removed from output
    }
  },
  "final_save": {
    "applied": bool,
    "details": {
      "format":       str,        # "xlsx" | "csv" | "xls" | "xlsm" | "ods" …
      "freeze_panes": bool,
      "locked_header": bool,
      "print_area":   bool
    }
  }
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants & helpers
# ─────────────────────────────────────────────────────────────────────────────

# Minimum fraction of non-null rows that must confirm the transformation
_MATCH_THRESHOLD = 0.70

# Max rows sampled for row-level verification (performance guard)
_SAMPLE = 300

# ── Excel error / formula sentinel strings ────────────────────────────────────
_EXCEL_ERRORS: Set[str] = {
    "#ref!", "#n/a", "#value!", "#div/0!", "#name?",
    "#null!", "#num!", "#getting_data", "#spill!",
}

# Patterns that suggest a cell held a formula (string starting with "=")
_FORMULA_RE = re.compile(r"^\s*=", re.IGNORECASE)

# Patterns that represent computed / derived / temporary column names
_INTERMEDIATE_HINTS: List[str] = [
    "temp", "tmp", "helper", "calc", "intermediate", "staging",
    "stg", "work", "scratch", "raw_", "_raw", "draft", "wip",
    "interim", "aux", "flag_", "_flag", "lookup_", "_lookup",
    "step_", "_step", "partial", "_partial", "debug", "_debug",
    "prev_", "_prev", "old_", "_old", "orig_", "_orig",
    "copy_", "_copy", "bak_", "_bak", "__",
]

# Excel-aware null representations
_EXCEL_NULLS: Set[str] = {
    "", "nan", "none", "null", "n/a", "na", "#n/a", "nil", "-",
}

# Supported final-save formats and their typical file-extension aliases
_KNOWN_FORMATS: Dict[str, List[str]] = {
    "xlsx":  ["xlsx", "excel", "workbook", "xls"],
    "xlsm":  ["xlsm", "macro"],
    "csv":   ["csv", "comma", "text"],
    "tsv":   ["tsv", "tab"],
    "ods":   ["ods", "opendoc"],
    "parquet": ["parquet", "pq"],
    "json":  ["json"],
    "html":  ["html", "htm"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

# Sentinel values for derived/new columns that have no input counterpart
_DERIVED_SENTINELS = frozenset({
    "DERIVED COLUMN",
    "MULTISOURCE COLUMN",
    "UNKNOWN MAPPING TYPE",
})


# def _remap_output(
#     output_df: pd.DataFrame,
#     column_mapping: Optional[Dict[str, str]],
# ) -> pd.DataFrame:
#     """Rename output columns back to input-namespace.
    
#     column_mapping is {output_col: input_col}. We use it directly to rename
#     output columns to their input equivalents. Sentinel values like
#     'DERIVED COLUMN' are excluded.
#     """
#     if not column_mapping:
#         print(f"  [_remap_output] No column_mapping — output cols unchanged: {list(output_df.columns)}")
#         return output_df
#     # Use mapping directly (output→input), excluding sentinel values
#     rename_map = {
#         out_col: in_col
#         for out_col, in_col in column_mapping.items()
#         if in_col not in _DERIVED_SENTINELS
#     }
#     print(f"  [_remap_output] rename map (out→in): {rename_map}")
#     renamed = output_df.rename(columns=rename_map)
#     print(f"  [_remap_output] output cols after rename: {list(renamed.columns)}")
#     return renamed
def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to input-namespace when a mapping is given.
    
    column_mapping is {output_col: input_col}.
    """
    if not column_mapping:
        return output_df
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return output_df.rename(columns=rename_map)

def _to_out(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Translate an input-namespace column to the output namespace.
    
    column_mapping is {output_col: input_col}. We invert it to find which
    output column corresponds to the given input column name.
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


def _common_cols(
    input_df: pd.DataFrame,
    out_df: pd.DataFrame,
) -> List[str]:
    """Columns present in both DataFrames (input namespace)."""
    return [c for c in input_df.columns if c in out_df.columns]


def _str_series(series: pd.Series) -> pd.Series:
    """Stringify a series; NaN → empty string."""
    return series.fillna("").astype(str)


def _sample_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return first _SAMPLE rows for performance."""
    return df.iloc[:_SAMPLE] if len(df) > _SAMPLE else df


def _is_excel_error(val: str) -> bool:
    return val.strip().lower() in _EXCEL_ERRORS


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and np.isnan(val):
        return True
    if isinstance(val, str) and val.strip().lower() in _EXCEL_NULLS:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _is_numeric_col(series: pd.Series) -> bool:
    return (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


def _is_string_col(series: pd.Series) -> bool:
    return (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
    )


def _is_bool_col(series: pd.Series) -> bool:
    return pd.api.types.is_bool_dtype(series)


def _is_datetime_col(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def _is_formula_string(val: str) -> bool:
    """Return True if a cell value looks like an Excel formula string."""
    return bool(_FORMULA_RE.match(val))


def _has_excel_error(series: pd.Series) -> bool:
    """True when the series contains at least one Excel error sentinel."""
    str_s = _str_series(series).str.strip().str.lower()
    return str_s.isin(_EXCEL_ERRORS).any()


def _col_has_formula_strings(series: pd.Series) -> bool:
    """
    True when the series is string-typed and ≥ _MATCH_THRESHOLD of its
    non-null values start with '=' (Excel formula strings exported as text).
    """
    if not _is_string_col(series):
        return False
    non_null = series.dropna().astype(str)
    if len(non_null) == 0:
        return False
    formula_frac = non_null.map(_is_formula_string).mean()
    return formula_frac >= _MATCH_THRESHOLD


def _dtype_changed_to_static(in_s: pd.Series, out_s: pd.Series) -> bool:
    """
    Heuristic: a column "froze" its formulas if
      (a) input was string / object (formula strings or mixed) and
          output became purely numeric / datetime / bool, OR
      (b) input contained Excel error sentinels and output does not, OR
      (c) input had formula-like strings (=SUM…) and output is all numeric.
    """
    # (a) dtype upgrade: object → numeric/datetime/bool
    if _is_string_col(in_s) and (
        _is_numeric_col(out_s) or _is_datetime_col(out_s) or _is_bool_col(out_s)
    ):
        return True

    # (b) Excel errors cleared
    if _has_excel_error(in_s) and not _has_excel_error(out_s):
        return True

    # (c) formula strings → numeric values
    if _col_has_formula_strings(in_s) and _is_numeric_col(out_s):
        return True

    return False


def _values_are_stable(in_s: pd.Series, out_s: pd.Series) -> bool:
    """
    After freezing, the *numeric* values of the output should be derivable
    from (or at least consistent with) the input.  This check ensures we
    don't flag a plain data-type conversion that has nothing to do with
    formula freezing.

    Returns True when the output numeric values are non-trivially different
    from what a simple cast of the input would produce — i.e. the values
    were computed (formula) and then frozen.

    Uses a loose check: if coercing both to float gives the same values for
    ≥ _MATCH_THRESHOLD rows, the "freeze" is confirmed (values are stable).
    """
    try:
        coerced_in  = pd.to_numeric(_str_series(in_s).str.replace(
            r"[$€£¥₹₩%,]", "", regex=True), errors="coerce")
        coerced_out = pd.to_numeric(out_s, errors="coerce")
        n = min(len(coerced_in), len(coerced_out))
        if n == 0:
            return False
        valid = coerced_out.notna().reset_index(drop=True)[:n]
        if valid.sum() == 0:
            return False
        matches = (
            abs(
                coerced_in.reset_index(drop=True)[:n][valid]
                - coerced_out.reset_index(drop=True)[:n][valid]
            ) <= 1e-4
        ).sum()
        return (matches / valid.sum()) >= _MATCH_THRESHOLD
    except Exception:
        return False


def _norm_col(name: str) -> str:
    """Lowercase, collapse non-alphanumeric to underscore."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _looks_intermediate(col_name: str) -> bool:
    """
    Return True when the column name matches known intermediate / helper
    column naming conventions.
    """
    n = _norm_col(col_name)
    return any(hint.strip("_") in n for hint in _INTERMEDIATE_HINTS)


def _infer_save_format(
    column_mapping: Optional[Dict[str, str]],
    output_df: pd.DataFrame,
) -> str:
    """
    Try to infer the save format from:
      1. A '__format__' or '__save_format__' pseudo-column in the output DF.
      2. column_mapping metadata key '__format__'.
      3. Default to 'xlsx'.
    """
    # Check column_mapping metadata key
    if column_mapping:
        fmt_key = column_mapping.get("__format__", "").lower()
        for canonical, aliases in _KNOWN_FORMATS.items():
            if any(alias in fmt_key for alias in aliases):
                return canonical

    # Check pseudo-column in output
    for col in output_df.columns:
        n = _norm_col(col)
        if "format" in n or "save" in n or "export" in n:
            val = str(output_df[col].dropna().astype(str).iloc[0]).lower() \
                if not output_df[col].dropna().empty else ""
            for canonical, aliases in _KNOWN_FORMATS.items():
                if any(alias in val for alias in aliases):
                    return canonical

    return "xlsx"  # safe default


# ─────────────────────────────────────────────────────────────────────────────
# 9.1  FORMULA → VALUE
# ─────────────────────────────────────────────────────────────────────────────

def build_formula_to_value_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect FORMULA → VALUE freezing (Excel "Paste Special → Values").

    Detection logic
    ---------------
    A column C is flagged when ANY of the following conditions hold:

    A. Dtype upgrade
       Input column is string / object (Excel exported formula text or
       mixed cells) and output column is purely numeric, datetime, or bool.
       The numeric values must be stable (match within 1e-4) confirming the
       output values were produced by evaluating the formula.

    B. Excel error clearing
       Input column contains Excel error sentinels (#REF!, #VALUE!, #N/A …)
       and the output column does not — the errors were resolved and frozen.

    C. Formula strings frozen
       Input column's string values start with "=" (Excel formula text
       e.g. "=SUM(B2:B10)") in ≥ 70 % of non-null rows, and the output
       column is numeric / clean — formula was evaluated and pasted as value.

    D. Numeric stability unchanged
       Both input and output are numeric, but the output has fewer unique
       values (rounded / frozen), indicating a "Paste Values" over a
       previously dynamic formula result.

    Edge cases
    ----------
    - Empty / None DataFrames                 → applied=False ✓
    - Row count mismatch                       → still checked (no row dependency) ✓
    - Columns only in output (new columns)     → ignored ✓
    - column_mapping renaming resolved         → output namespace reported ✓
    - Mixed currency/percentage input strings  → cleaned before value comparison ✓
    - Boolean text ("TRUE"/"FALSE") columns    → not flagged as formula freeze ✓
    - Date string → datetime conversion        → flagged (date formulas frozen) ✓
    - All-null input columns                   → skipped ✓
    - Excel error #N/A → empty string          → flagged ✓

    Args:
        input_df:       DataFrame before finalization.
        output_df:      DataFrame after finalization.
        column_mapping: Optional {output_col: input_col} rename map.

    Returns:
        {
          "applied": bool,
          "details": {"columns": [output_col_name, ...]}
        }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"columns": []}}

    if input_df is None or output_df is None:
        return empty
    if input_df.empty or output_df.empty:
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    print(f"  [build_formula_to_value_config] output columns after remap: {list(out_df.columns)}")
    print
    cols    = _common_cols(input_df, out_df)
    frozen: List[str] = []

    for col in cols:
        in_s  = _sample_df(input_df)[[col]].iloc[:, 0].reset_index(drop=True)
        out_s = _sample_df(out_df)[[col]].iloc[:, 0].reset_index(drop=True)

        # Skip entirely null input columns
        if in_s.dropna().empty:
            continue

        # Skip columns that are identical (nothing changed)
        try:
            if in_s.equals(out_s):
                continue
        except Exception:
            pass

        # ── Condition A+C: dtype upgrade / formula strings ────────────────────
        if _dtype_changed_to_static(in_s, out_s):
            # Extra guard: if it is a simple boolean-text → bool conversion,
            # that belongs to type_conversion (phase 6), not formula freeze.
            in_lower = in_s.dropna().astype(str).str.strip().str.lower()
            if in_lower.isin({"true", "false", "yes", "no", "1", "0", "y", "n"}).mean() \
                    >= _MATCH_THRESHOLD:
                continue
            # Verify numeric stability for dtype-upgrade case
            if _is_numeric_col(out_s) and not _values_are_stable(in_s, out_s):
                continue
            frozen.append(_to_out(col, column_mapping))
            continue

        # ── Condition B: Excel error clearing ────────────────────────────────
        if _has_excel_error(in_s) and not _has_excel_error(out_s):
            frozen.append(_to_out(col, column_mapping))
            continue

        # ── Condition D: both numeric, but output has reduced unique count ────
        if _is_numeric_col(in_s) and _is_numeric_col(out_s):
            in_unique  = in_s.dropna().round(6).nunique()
            out_unique = out_s.dropna().round(6).nunique()
            if in_unique > 1 and out_unique < in_unique * 0.5:
                # Output has significantly fewer distinct values → "frozen"
                # Only flag if values actually differ (not merely rounded)
                if not in_s.round(4).equals(out_s.round(4)):
                    frozen.append(_to_out(col, column_mapping))

    return {
        "applied": len(frozen) > 0,
        "details": {"columns": frozen},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9.2  REMOVE INTERMEDIATE COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def build_remove_intermediate_columns_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect REMOVE INTERMEDIATE COLUMNS — helper/staging/temp columns that
    were present in the input but stripped before the final save.

    Detection logic
    ---------------
    A column C (present in input, absent in output) is flagged as
    "intermediate" when ANY of the following hold:

    A. Name-based heuristic
       The column name contains a well-known intermediate/helper naming
       token: temp, tmp, helper, calc, intermediate, staging, stg, work,
       scratch, raw_, _raw, draft, wip, interim, aux, flag_, _flag,
       lookup_, _lookup, step_, _step, partial, debug, prev_, old_,
       orig_, copy_, bak_, __ (double-underscore prefix convention).

    B. All-constant column
       Every non-null value in the input column is the same — a staging
       constant column added purely for lookup/join purposes.

    C. Sequential-index-like column
       Values are a strict -based integer sequence (1, 2, 3 …), indicating
       a helper row-number column added during intermediate processing.

    D. Fully-derived numeric column
       The column's values can be exactly reproduced from one or two other
       input numeric columns via simple arithmetic (+, -, *, /), implying it
       was a calculated staging column not needed in the final output.

    E. Exclusively #N/A / error columns
       Input column contains ≥ 70 % Excel error sentinels — clearly a broken
       lookup column that was removed before final delivery.

    Columns that are EXCLUDED from intermediate detection
    -----------------------------------------------------
    - Columns still present in the output (even under a renamed name via
      column_mapping) → kept columns are NOT intermediate.
    - Columns that look like primary keys / IDs (short integer or UUID-like
      string, named with "id", "key", "code", "pk") → not intermediate.
    - Columns with < 2 non-null values → insufficient evidence, skipped.

    Edge cases
    ----------
    - Empty / None DataFrames               → applied=False ✓
    - column_mapping renames resolved        → mapped cols not flagged ✓
    - Multi-criteria: name hint + derived    → flagged once ✓
    - Single-column DataFrames              → handled ✓
    - 100 % null columns in input           → flagged as intermediate ✓
    - column_mapping keys with __format__   → metadata keys ignored ✓

    Args:
        input_df:       DataFrame before finalization.
        output_df:      DataFrame after finalization.
        column_mapping: Optional {output_col: input_col} rename map.

    Returns:
        {
          "applied": bool,
          "details": {"columns": [input_col_name_or_output_alias, ...]}
        }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"columns": []}}

    if input_df is None or output_df is None:
        return empty
    if input_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)

    # Columns present in input but absent in the (remapped) output
    out_col_set = set(out_df.columns)
    removed_cols = [c for c in input_df.columns if c not in out_col_set]

    if not removed_cols:
        return empty

    # Build the set of columns that are accounted for by column_mapping renames
    # (i.e. input col renamed but still present — not truly removed)
    mapped_input_cols: Set[str] = set()
    if column_mapping:
        for ic in column_mapping:
            if ic.startswith("__"):
                continue  # metadata sentinel
            mapped_input_cols.add(ic)

    # Primary key / ID name hints → exclude from intermediate detection
    _ID_HINTS = {"id", "key", "code", "pk", "uuid", "guid", "oid",
                 "identifier", "ref", "no", "num", "number"}

    def _looks_like_pk(col_name: str) -> bool:
        n = _norm_col(col_name)
        tokens = set(n.split("_"))
        return bool(tokens & _ID_HINTS)

    # Derive a simple arithmetic value from two source columns
    def _is_derived(series: pd.Series, df: pd.DataFrame) -> bool:
        num_cols = [
            c for c in df.columns
            if _is_numeric_col(df[c]) and c != series.name
        ]
        if len(num_cols) < 1:
            return False
        target = pd.to_numeric(series, errors="coerce").dropna()
        if len(target) < 2:
            return False
        for a in num_cols[:8]:  # limit search for performance
            col_a = pd.to_numeric(df[a], errors="coerce")
            # Single-column: identity, negation, scaling
            for op_fn in (
                lambda x: x,
                lambda x: -x,
                lambda x: x * 100,
                lambda x: x / 100,
            ):
                try:
                    result = op_fn(col_a)
                    n_check = min(len(result), len(target))
                    valid = target.index[:n_check]
                    if (abs(result.loc[valid] - target.loc[valid]) <= 1e-4).mean() \
                            >= _MATCH_THRESHOLD:
                        return True
                except Exception:
                    continue
            for b in num_cols[:8]:
                if a == b:
                    continue
                col_b = pd.to_numeric(df[b], errors="coerce")
                for op_fn in (
                    lambda x, y: x + y,
                    lambda x, y: x - y,
                    lambda x, y: x * y,
                    lambda x, y: x / y.replace(0, np.nan),
                    lambda x, y: y - x,
                ):
                    try:
                        result = op_fn(col_a, col_b)
                        n_check = min(len(result), len(target))
                        valid = target.index[:n_check]
                        if (abs(result.loc[valid] - target.loc[valid]) <= 1e-4).mean() \
                                >= _MATCH_THRESHOLD:
                            return True
                    except Exception:
                        continue
        return False

    intermediate: List[str] = []

    for col in removed_cols:
        # Skip columns that exist under a rename in the output
        if col in mapped_input_cols and column_mapping and col in column_mapping:
            continue

        in_s = input_df[col]
        non_null = in_s.dropna()

        # ── Condition A: name-based heuristic ─────────────────────────────────
        if _looks_intermediate(col):
            intermediate.append(_to_out(col, column_mapping))
            continue

        # ── Skip PK / ID columns ──────────────────────────────────────────────
        if _looks_like_pk(col):
            continue

        # ── Condition: 100% null column ───────────────────────────────────────
        if len(non_null) == 0:
            intermediate.append(_to_out(col, column_mapping))
            continue

        # Need at least 2 non-null values for reliable detection below
        if len(non_null) < 2:
            continue

        # ── Condition B: all-constant column ──────────────────────────────────
        if non_null.astype(str).nunique() == 1:
            intermediate.append(_to_out(col, column_mapping))
            continue

        # ── Condition C: sequential integer index ─────────────────────────────
        if _is_numeric_col(in_s):
            vals = pd.to_numeric(in_s, errors="coerce").dropna()
            if len(vals) >= 2:
                expected_seq = pd.Series(range(1, len(vals) + 1), dtype=float)
                actual_seq   = vals.reset_index(drop=True).astype(float)
                n = min(len(expected_seq), len(actual_seq))
                if (abs(expected_seq[:n] - actual_seq[:n]) <= 1e-6).all():
                    intermediate.append(_to_out(col, column_mapping))
                    continue

        # ── Condition D: fully-derived numeric ────────────────────────────────
        if _is_numeric_col(in_s) and _is_derived(in_s, input_df):
            intermediate.append(_to_out(col, column_mapping))
            continue

        # ── Condition E: predominantly Excel error column ─────────────────────
        str_s = _str_series(in_s).str.strip().str.lower()
        error_frac = str_s.isin(_EXCEL_ERRORS).mean()
        if error_frac >= _MATCH_THRESHOLD:
            intermediate.append(_to_out(col, column_mapping))
            continue

    return {
        "applied": len(intermediate) > 0,
        "details": {"columns": intermediate},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9.3  FINAL SAVE / STRUCTURE FREEZE
# ─────────────────────────────────────────────────────────────────────────────

def build_final_save_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect FINAL SAVE characteristics — output format, freeze panes,
    header locking, and print-area indicators.

    Detection logic
    ---------------
    format
      Priority 1: column_mapping["__format__"] metadata key.
      Priority 2: a pseudo-column in output_df whose name contains "format"
                  or "save" or "export" and whose value is a known format alias.
      Priority 3: column_mapping["__file_ext__"] metadata key.
      Default:    "xlsx"  (safe Excel default for tabular data).

    freeze_panes
      True when ANY of:
      • column_mapping["__freeze_panes__"] is truthy.
      • The header row in output_df appears duplicated as row 0 (Excel
        freeze row 1 sentinel pattern).
      • output_df column count ≥ 6 and row count ≥ 15 (heuristic: large
        sheets are almost always saved with freeze panes in practice).

    locked_header
      True when:
      • column_mapping["__locked_header__"] is truthy, OR
      • The output column names are all-uppercase or all title-case (a common
        convention for "locked" / presentation-ready headers), OR
      • column_mapping["__header_lock__"] is truthy.

    print_area
      True when:
      • column_mapping["__print_area__"] is truthy, OR
      • output_df has a named column that matches a print-area sentinel
        (e.g. "__print_area__", "print_range", "page_setup").

    Edge cases
    ----------
    - Empty / None DataFrames           → applied=True, format="xlsx" ✓
      (finalization is always considered "applied" — even if no structural
       changes happened, the sheet was saved)
    - No column_mapping provided        → format defaults to "xlsx" ✓
    - Metadata keys (__format__ etc.)   → stripped from detection scope ✓
    - Mixed-case column names           → locked_header=False ✓
    - Very small DataFrames (< 6 cols)  → freeze_panes heuristic skipped ✓
    - CSVs have no freeze / lock / print area — flags forced False for csv ✓

    Args:
        input_df:       DataFrame before finalization.
        output_df:      DataFrame after finalization (or None for format-only).
        column_mapping: Optional {output_col: input_col} + optional metadata
                        keys (__format__, __freeze_panes__, __locked_header__,
                        __print_area__, __file_ext__).

    Returns:
        {
          "applied": True,          # always True — save always happens
          "details": {
            "format":        str,
            "freeze_panes":  bool,
            "locked_header": bool,
            "print_area":    bool
          }
        }
    """
    cm = column_mapping or {}

    # ── Resolve save format ───────────────────────────────────────────────────
    save_format = "xlsx"

    # Check metadata keys first
    for meta_key in ("__format__", "__file_ext__", "__save_format__"):
        raw = str(cm.get(meta_key, "")).strip().lower()
        if raw:
            for canonical, aliases in _KNOWN_FORMATS.items():
                if any(alias in raw for alias in aliases):
                    save_format = canonical
                    break
            if save_format != "xlsx":
                break

    # Fallback: check output pseudo-columns
    if save_format == "xlsx" and output_df is not None and not output_df.empty:
        for col in output_df.columns:
            n = _norm_col(col)
            if any(kw in n for kw in ("format", "save", "export", "file_type")):
                col_vals = output_df[col].dropna()
                if not col_vals.empty:
                    val = str(col_vals.iloc[0]).strip().lower()
                    for canonical, aliases in _KNOWN_FORMATS.items():
                        if any(alias in val for alias in aliases):
                            save_format = canonical
                            break

    # ── Resolve freeze_panes ──────────────────────────────────────────────────
    freeze_panes = False

    meta_fp = cm.get("__freeze_panes__", None)
    if meta_fp is not None:
        freeze_panes = bool(meta_fp)
    elif output_df is not None and not output_df.empty:
        n_cols = len(output_df.columns)
        n_rows = len(output_df)
        # Header-as-row-0 sentinel: first data row equals the column headers
        if n_rows > 1:
            first_row_vals = [str(v) for v in output_df.iloc[0]]
            header_vals    = [str(c) for c in output_df.columns]
            match_frac = sum(
                a.strip().lower() == b.strip().lower()
                for a, b in zip(first_row_vals, header_vals)
            ) / max(len(header_vals), 1)
            if match_frac >= _MATCH_THRESHOLD:
                freeze_panes = True
        # Size heuristic (large sheets always benefit from freeze)
        if not freeze_panes and n_cols >= 6 and n_rows >= 15:
            freeze_panes = True

    # ── Resolve locked_header ─────────────────────────────────────────────────
    locked_header = False

    meta_lh = cm.get("__locked_header__", cm.get("__header_lock__", None))
    if meta_lh is not None:
        locked_header = bool(meta_lh)
    elif output_df is not None and not output_df.empty:
        col_names = [str(c) for c in output_df.columns if not str(c).startswith("__")]
        if col_names:
            all_upper  = all(c == c.upper() for c in col_names)
            all_title  = all(c == c.title() for c in col_names)
            locked_header = all_upper or all_title

    # ── Resolve print_area ────────────────────────────────────────────────────
    print_area = False

    meta_pa = cm.get("__print_area__", None)
    if meta_pa is not None:
        print_area = bool(meta_pa)
    elif output_df is not None:
        _PRINT_HINTS = {"print_area", "print_range", "page_setup",
                        "page_break", "print_title"}
        for col in output_df.columns:
            if _norm_col(str(col)) in _PRINT_HINTS:
                print_area = True
                break

    # ── For CSV / plain text formats, structural flags are not applicable ─────
    if save_format in ("csv", "tsv", "json", "html"):
        freeze_panes  = False
        locked_header = False
        print_area    = False

    return {
        "applied": True,   # finalization / save always applies
        "details": {
            "format":        save_format,
            "freeze_panes":  freeze_panes,
            "locked_header": locked_header,
            "print_area":    print_area,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9.4  RESEQUENCE SERIAL / INDEX COLUMNS
# ─────────────────────────────────────────────────────────────────────────────

def _is_serial_index_col(series: pd.Series, col_name: str) -> bool:
    """
    Return True when a column's VALUES look like a serial / row-number index.

    Detection is purely value-based — column names are ignored entirely.

    A column qualifies when ALL of the following hold:
      1. All non-null values are integers (no decimals).
      2. min value is 1 (or 0 for 0-based) — avoids flagging arbitrary
         ID columns that happen to be sequential (e.g. employee IDs starting
         at 10001).
      3. Values are UNIQUE (no duplicate row numbers in a serial index).
      4. max value ≤ 2 × non-null row count — bounds check to reject large
         sparse ID columns (e.g. phone numbers, zip codes).
      5. Values form a contiguous integer sequence (no gaps when sorted).
    """
    # Must be numeric (allow up to 5 % coercion failures for mixed columns)
    if not pd.api.types.is_numeric_dtype(series):
        coerced = pd.to_numeric(series, errors="coerce")
        if coerced.isna().mean() > 0.05:
            print(f"  [resequence._is_serial] '{col_name}': SKIP (not numeric)")
            return False
        series = coerced

    vals = series.dropna()
    if len(vals) < 2:
        print(f"  [resequence._is_serial] '{col_name}': SKIP (< 2 non-null values)")
        return False

    try:
        float_vals = vals.astype(float)
    except Exception as e:
        print(f"  [resequence._is_serial] '{col_name}': SKIP (float cast failed: {e})")
        return False

    # 1. All integer-like
    if not (float_vals % 1 == 0).all():
        print(f"  [resequence._is_serial] '{col_name}': SKIP (non-integer values)")
        return False

    int_vals = float_vals.astype(int)
    n        = len(int_vals)          # non-null count — used for ALL bounds checks
    mn       = int(int_vals.min())
    mx       = int(int_vals.max())

    # 2. Must start at 0 or 1
    if mn not in (0, 1):
        print(f"  [resequence._is_serial] '{col_name}': SKIP (min={mn}, not 0 or 1)")
        return False

    # 3. All values unique (no duplicate row numbers)
    if int_vals.nunique() != n:
        print(f"  [resequence._is_serial] '{col_name}': SKIP (has duplicate values)")
        return False

    # 4. Max value bounded: must be ≤ 2 × non-null row count
    if mx > n * 2:
        print(f"  [resequence._is_serial] '{col_name}': SKIP (max={mx} > 2×n={n*2}, likely an ID column)")
        return False

    # 5. Values must form a contiguous sequence (no gaps)
    expected_start = mn  # 0 or 1
    expected_seq   = list(range(expected_start, expected_start + n))
    actual_sorted  = sorted(int_vals.tolist())
    if actual_sorted != expected_seq:
        print(f"  [resequence._is_serial] '{col_name}': SKIP (gaps in sequence, not contiguous)")
        return False

    print(f"  [resequence._is_serial] '{col_name}': "
          f"✅ serial (n={n}, min={mn}, max={mx}, unique={int_vals.nunique()})")
    return True


def _output_needs_resequence(
    in_series: pd.Series,
    out_series: pd.Series,
) -> bool:
    """
    Return True when resequence WAS APPLIED or needs to be applied.

    Two scenarios both confirm resequence:

    A. Output already clean 1..M  (M < N → was renumbered after filtering)
       input  = [1, 2, 3, ..., 100]
       output = [1, 2, 3, ..., 52]   ← fresh renumber, out_max < in_max ✅

    B. Output still sparse  (old row numbers kept, gaps exist)
       input  = [1, 2, 3, ..., 100]
       output = [1,2,3,4,5,6,7,8,9,11,13,...]  ← gaps, not renumbered ✅
    """
    in_vals  = pd.to_numeric(in_series,  errors="coerce").dropna()
    out_vals = pd.to_numeric(out_series, errors="coerce").dropna()

    if len(in_vals) == 0 or len(out_vals) == 0:
        print(f"  [resequence._needs_reseq]: SKIP — empty series")
        return False

    if not (in_vals % 1 == 0).all():
        print(f"  [resequence._needs_reseq]: SKIP — input has non-integer values")
        return False
    if not (out_vals % 1 == 0).all():
        print(f"  [resequence._needs_reseq]: SKIP — output has non-integer values")
        return False

    in_sorted  = sorted(in_vals.astype(int).tolist())
    out_sorted = sorted(out_vals.astype(int).tolist())
    in_n       = len(in_sorted)
    out_n      = len(out_sorted)

    # Input must be a clean contiguous sequence starting at 0 or 1
    in_start  = in_sorted[0] if in_sorted else 1
    in_is_seq = (
        in_start in (0, 1)
        and in_sorted == list(range(in_start, in_start + in_n))
    )

    print(f"  [resequence._needs_reseq]: "
          f"in_n={in_n} in[:5]={in_sorted[:5]} in_is_seq={in_is_seq} | "
          f"out_n={out_n} out[:5]={out_sorted[:5]}")

    if not in_is_seq:
        print(f"  [resequence._needs_reseq]: SKIP — input is not a clean serial sequence")
        return False

    if out_n >= in_n:
        print(f"  [resequence._needs_reseq]: SKIP — no row reduction (out_n={out_n} >= in_n={in_n})")
        return False

    # Scenario A: output is a NEW clean 1..M — was renumbered after filtering
    out_is_clean_renumbered = (out_sorted == list(range(1, out_n + 1)))
    if out_is_clean_renumbered:
        # Confirm values actually changed: out_max must be < in_max
        result = int(out_sorted[-1]) < int(in_sorted[-1])
        print(f"  [resequence._needs_reseq]: Scenario A (renumbered) — "
              f"out_max={out_sorted[-1]} < in_max={in_sorted[-1]} → applied={result}")
        return result

    # Scenario B: output has gaps — old row numbers retained, resequence needed
    print(f"  [resequence._needs_reseq]: Scenario B (gaps) — output not renumbered → applied=True")
    return True


def build_resequence_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect serial / index columns that need to be re-numbered 1..N.

    IMPORTANT: input_df must be the FULL original unfiltered DataFrame.
    After row filtering, df_in_filtered has sparse index values that
    match df_out exactly — making resequence undetectable.
    df_in_full retains the clean 1..M that proves resequencing is needed.

    Detection strategy
    ------------------
    column_mapping (e.g. {"SL No": "Employee Count", ...}) is used by
    _remap_output() to rename output columns back to the input namespace
    before comparison.  This means renamed serial columns (e.g. "SL No"
    in input / "Employee Count" in output) are found in common_cols and
    evaluated normally — no position-based fallback needed.
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if input_df is None or output_df is None:
        return empty
    if input_df.empty or output_df.empty:
        return empty

    print(f"\n  [resequence] ── ENTRY ─────────────────────────────────────────")
    print(f"  [resequence] input_df  rows={len(input_df)}  cols={list(input_df.columns)}")
    print(f"  [resequence] output_df rows={len(output_df)} cols={list(output_df.columns)}")
    print(f"  [resequence] column_mapping received: {column_mapping}")
    print(f"  [resequence] column_mapping type: {type(column_mapping)}, empty={not column_mapping}")

    # Resequence only makes sense when output has fewer rows than input.
    if len(output_df) >= len(input_df):
        print(f"  [resequence] SKIP — no row reduction ({len(output_df)} >= {len(input_df)})")
        return empty

    print(f"  [resequence] row reduction confirmed: {len(input_df)} → {len(output_df)}")

    # _remap_output renames output cols to input namespace using column_mapping.
    out_df = _remap_output(output_df, column_mapping)
    common = _common_cols(input_df, out_df)
    print(f"  [resequence] common cols after remap: {common}")

    if not common:
        print(f"  [resequence] ⚠️  NO common cols found!")
        print(f"  [resequence]    input  cols : {list(input_df.columns)}")
        print(f"  [resequence]    remapped out: {list(out_df.columns)}")

    rules: List[Dict[str, Any]] = []

    for col in common:
        in_s  = input_df[col]
        out_s = out_df[col]

        # print(f"\n  [resequence] ── checking col='{col}' ──────────────────────")
        # print(f"  [resequence]   in  dtype={in_s.dtype}  n={len(in_s)}  "
        #       f"non_null={in_s.notna().sum()}  "
        #       f"values[:5]={in_s.dropna().tolist()[:5]}")
        # print(f"  [resequence]   out dtype={out_s.dtype}  n={len(out_s)}  "
        #       f"non_null={out_s.notna().sum()}  "
        #       f"values[:5]={out_s.dropna().tolist()[:5]}")

        serial_ok = _is_serial_index_col(in_s, col)
        print(f"  [resequence]   _is_serial_index_col → {serial_ok}")
        if not serial_ok:
            continue

        reseq_ok = _output_needs_resequence(in_s, out_s)
        print(f"  [resequence]   _output_needs_resequence → {reseq_ok}")
        if not reseq_ok:
            continue

        out_col_name = _to_out(col, column_mapping)
        print(f"  [resequence] ✅ '{col}' → output col='{out_col_name}' NEEDS RESEQUENCE")
        rules.append({
            "column":   out_col_name,
            "start_at": 1,
            "step":     1,
        })

    print(f"\n  [resequence] ── RESULT: rules={rules} ─────────────────────────")
    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
