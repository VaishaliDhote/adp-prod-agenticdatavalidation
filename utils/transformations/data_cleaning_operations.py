# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Data Cleaning Transformation Utilities
========================================
Four focused builder functions for Phase 6 cleaning detection.

  build_trim_clean_config      → 6.1  TRIM / CASE NORMALISATION
  build_find_replace_config    → 6.2  FIND & REPLACE  (value substitution)
  build_type_conversion_config → 6.3  TYPE CONVERSION  (text→number, etc.)
  build_missing_values_config  → 6.4  MISSING VALUES   (fill / drop / flag)

Each function accepts (input_df, output_df, column_mapping?) and returns a
self-contained config block consumed directly by phase_6_cleaning.detect().

Design principles
-----------------
- Row-level verification: every rule is proven against ≥ MATCH_THRESHOLD of rows.
- Excel-aware dtype handling: currency ($), percentage (%), comma-thousand,
  date strings, boolean text ("TRUE"/"FALSE"), empty-string-as-null, etc.
- column_mapping is honoured: detection runs in input namespace, output
  namespace is restored before returning.
- Applied only when evidence is conclusive; returns applied=False otherwise.

Output schema
-------------
{
  "trim_clean": {
    "applied": bool,
    "details": {
      "columns": [str, ...]          # columns where trim/case was applied
    }
  },
  "find_replace": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "from": str, "to": str}
      ]
    }
  },
  "type_conversion": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "from": str, "to": str}
      ]
    }
  },
  "missing_values": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "method": "fill"|"drop"|"flag", "value": Any}
      ]
    }
  }
}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils.validations.detect_data_type import detect_data_type
from utils.validations.detect_number_format import detect_number_format, normalize_number

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants & helpers
# ─────────────────────────────────────────────────────────────────────────────

# Minimum fraction of non-null rows that must confirm the transformation
_MATCH_THRESHOLD = 0.70

# Max rows sampled for row-by-row verification (performance guard)
_SAMPLE = 200

# Excel boolean representations
_BOOL_TRUE  = {"true", "yes", "1", "y", "x", "on"}
_BOOL_FALSE = {"false", "no", "0", "n", "off"}

# Canonical type labels (used in type_conversion rules)
_TYPE_LABELS = {
    "int64":          "int",
    "int32":          "int",
    "float64":        "float",
    "float32":        "float",
    "object":         "string",
    "string":         "string",
    "bool":           "boolean",
    "boolean":        "boolean",
    "datetime64[ns]": "datetime",
    "datetime64":     "datetime",
    "category":       "category",
}

# Currency / percentage prefixes / suffixes that Excel embeds in cells
_CURRENCY_RE   = re.compile(r"^\s*[$€£¥₹₩]\s*|[$€£¥₹₩]\s*$", re.UNICODE)
_PERCENT_RE    = re.compile(r"\s*%\s*$")
_THOUSAND_RE   = re.compile(r"(?<=\d),(?=\d{3})")


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

# Sentinel values for derived/new columns
_DERIVED_SENTINELS = frozenset({"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"})


def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to input-namespace.
    
    column_mapping is {output_col: input_col}.
    """
    if not column_mapping:
        return output_df
    rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    out_df = output_df.rename(columns=rename_map)
    # Drop duplicates: multiple output cols can map to the same input col name
    # (e.g. Full_Name + Reconstructed_Full_Name → Full_Name). Keep first.
    return out_df.loc[:, ~out_df.columns.duplicated()]


def _to_out(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Translate input-namespace column to output-namespace."""
    if not column_mapping:
        return col
    inv = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
    return inv.get(col, col)


def _common_cols(
    input_df: pd.DataFrame,
    out_df: pd.DataFrame,   # already remapped to input namespace
) -> List[str]:
    return [c for c in input_df.columns if c in out_df.columns]


def _str_col(series: pd.Series) -> pd.Series:
    """Return string representation; NaN → empty string."""
    return series.fillna("").astype(str)


def _sample(df: pd.DataFrame) -> pd.DataFrame:
    return df.iloc[:_SAMPLE] if len(df) > _SAMPLE else df


def _match_ratio(a: pd.Series, b: pd.Series) -> float:
    """Fraction of rows where a == b (ignoring rows where both are empty)."""
    valid = (a != "") & (b != "")
    if valid.sum() == 0:
        return 0.0
    return float((a[valid] == b[valid]).mean())


def _dtype_label(series: pd.Series) -> str:
    """Return a human-readable dtype label for a series."""
    dtype_str = str(series.dtype)
    return _TYPE_LABELS.get(dtype_str, dtype_str)


def _is_numeric_col(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _is_string_col(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _is_bool_col(series: pd.Series) -> bool: 
    return pd.api.types.is_bool_dtype(series)


def _is_datetime_col(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """
    Coerce a series to float, handling Excel quirks:
      - Currency prefix/suffix  ($1,234.56 → 1234.56)
      - Percentage suffix        (12.5% → 12.5)
      - Comma thousand separator (1,234 → 1234)
      - European decimal comma   (1.234,56 → 1234.56)
    """
    if _is_numeric_col(series):
        return series.astype(float)

    def _clean(v: str) -> Optional[float]:
        v = v.strip()
        if not v or v.lower() in ("nan", "none", "null", "n/a", "-", ""):
            return None
        v = _CURRENCY_RE.sub("", v)
        v = _PERCENT_RE.sub("", v)
        # European: 1.234,56 → detect dot-as-thousand, comma-as-decimal
        if re.match(r"^-?\d{1,3}(\.\d{3})*,\d+$", v):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = _THOUSAND_RE.sub("", v)   # remove comma thousand separator
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    return series.astype(str).map(_clean)


# ─────────────────────────────────────────────────────────────────────────────
# 6.1  TRIM / CASE NORMALISATION
# ─────────────────────────────────────────────────────────────────────────────

def _detect_trim_on_col(inp: pd.Series, out: pd.Series) -> bool:
    """
    Return True when out == inp.str.strip() for ≥ MATCH_THRESHOLD of rows
    AND at least one value actually had surrounding whitespace.
    """
    a = _str_col(inp)
    b = _str_col(out)
    has_spaces = (a != a.str.strip()).any()
    if not has_spaces:
        return False
    return _match_ratio(a.str.strip(), b) >= _MATCH_THRESHOLD


def _detect_case_on_col(inp: pd.Series, out: pd.Series) -> Optional[str]:
    """
    Return the normalisation operation ('upper' | 'lower' | 'proper' | 'clean')
    or None when none applies.

    'clean' = strip of non-printable / non-ASCII characters (Excel CLEAN()).
    """
    a = _str_col(inp)
    b = _str_col(out)

    if _match_ratio(a.str.upper(), b) >= _MATCH_THRESHOLD:
        return "upper"
    if _match_ratio(a.str.lower(), b) >= _MATCH_THRESHOLD:
        return "lower"
    if _match_ratio(a.str.title(), b) >= _MATCH_THRESHOLD:
        return "proper"

    # CLEAN: remove non-printable characters
    cleaned = a.map(lambda s: "".join(c for c in s if c.isprintable()))
    if _match_ratio(cleaned, b) >= _MATCH_THRESHOLD:
        return "clean"

    return None


def build_trim_clean_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect TRIM and case-normalisation on string columns.

    Detects per column
    ------------------
    - TRIM: leading/trailing whitespace removed (Excel =TRIM())
    - UPPER / LOWER / PROPER: case normalisation
    - CLEAN: non-printable / control character removal (Excel =CLEAN())
    - Combined TRIM + CASE (checked together on already-trimmed values)

    Edge cases
    ----------
    - Only string (object/string dtype) columns are examined
    - Mixed numeric/string columns coerced to str before comparison
    - Columns with zero whitespace changes → not flagged as trimmed
    - Renames via column_mapping are resolved before detection
    - Same row count enforced (trim/case never changes row count)

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "columns": [str, ...]   # output-namespace column names
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"columns": []}}

    if input_df.empty or output_df.empty:
        return empty
    if len(input_df) != len(output_df):
        return empty

    out_df   = _remap_output(output_df, column_mapping)
    cols     = _common_cols(input_df, out_df)
    affected: List[str] = []

    for col in cols:
        in_s  = input_df[col].reset_index(drop=True)
        out_s = out_df[col].reset_index(drop=True)

        # Skip numeric/bool/datetime columns
        if _is_numeric_col(in_s) or _is_bool_col(in_s) or _is_datetime_col(in_s):
            continue

        # Skip identical columns
        if _str_col(in_s).equals(_str_col(out_s)):
            continue

        # Check TRIM
        if _detect_trim_on_col(in_s, out_s):
            affected.append(_to_out(col, column_mapping))
            continue

        # Check TRIM + CASE together (common pattern: strip then uppercase)
        a_trimmed = _str_col(in_s).str.strip()
        b = _str_col(out_s)
        found_case = False
        for fn in (str.upper, str.lower, str.title):
            if _match_ratio(a_trimmed.map(fn), b) >= _MATCH_THRESHOLD:
                affected.append(_to_out(col, column_mapping))
                found_case = True
                break
        if found_case:
            continue

        # Check CASE alone
        if _detect_case_on_col(in_s, out_s) is not None:
            affected.append(_to_out(col, column_mapping))

    return {
        "applied": len(affected) > 0,
        "details": {"columns": affected},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.2  FIND & REPLACE
# ─────────────────────────────────────────────────────────────────────────────

def _infer_replacements(
    inp: pd.Series,
    out: pd.Series,
    max_rules: int = 20,
) -> List[Tuple[str, str]]:
    """
    Infer (from_val, to_val) pairs where inp value was replaced by out value.

    Rules
    -----
    - Only rows where in_val ≠ out_val are considered.
    - A pair is accepted when the same mapping is consistent across ALL rows
      that contain in_val (i.e. in_val always → to_val, never → something else).
    - Currency / percentage stripped from numeric strings so that
      "$1,234" → "1234" is detected as a replacement even though the
      underlying number is the same.
    """
    a = _str_col(inp)
    b = _str_col(out)

    mapping: Dict[str, str] = {}
    conflict: set = set()

    for av, bv in zip(a, b):
        if av == bv:
            continue
        if av in conflict:
            continue
        if av in mapping:
            if mapping[av] != bv:
                conflict.add(av)
                del mapping[av]
        else:
            mapping[av] = bv

    # Remove pairs where in_val == "" (NaN placeholder) — those belong to
    # missing-values detection, not find & replace
    pairs = [
        (k, v) for k, v in mapping.items()
        if k not in ("", "nan", "none", "null", "n/a")
    ]

    # Verify that applying the mapping reproduces the output at threshold
    if pairs:
        predicted = a.replace(dict(pairs))
        if _match_ratio(predicted, b) < _MATCH_THRESHOLD:
            return []

    return pairs[:max_rules]


def build_find_replace_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect FIND & REPLACE value substitutions.

    Detects per column
    ------------------
    - Exact string replacements  ("A" → "active", "N" → "inactive")
    - Code expansions             ("US" → "United States")
    - Status normalisation        ("y"/"yes"/"1" → "true")
    - Excel error replacement     ("#N/A" → "")
    - Numeric format stripping    ("$1,234.00" → "1234.0")

    Edge cases
    ----------
    - Both string and numeric columns examined
    - Consistent mapping enforced: if same in_val maps to two different out_vals
      in different rows, the rule is discarded
    - NaN-to-value transitions are ignored here (handled by missing_values)
    - column_mapping applied in both directions
    - Same row count enforced (find/replace never changes row count)

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "from": str, "to": str}
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty
    if len(input_df) != len(output_df):
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    cols    = _common_cols(input_df, out_df)
    rules: List[Dict[str, Any]] = []

    for col in cols:
        in_s  = _sample(input_df)[[col]].iloc[:, 0].reset_index(drop=True)
        out_s = _sample(out_df)[[col]].iloc[:, 0].reset_index(drop=True)

        # Skip identical columns
        if _str_col(in_s).equals(_str_col(out_s)):
            continue

        pairs = _infer_replacements(in_s, out_s)
        out_col = _to_out(col, column_mapping)
        for frm, to in pairs:
            rules.append({"column": out_col, "from": frm, "to": to})

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.3  TYPE CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

# Ordered list of target type checks; first match wins
_TYPE_CHECKS: List[Tuple[str, Any]] = [
    ("int",      lambda s: s.apply(lambda x: isinstance(x, (int, np.integer)))),
    ("float",    lambda s: pd.api.types.is_float_dtype(s)),
    ("boolean",  lambda s: pd.api.types.is_bool_dtype(s)),
    ("datetime", lambda s: pd.api.types.is_datetime64_any_dtype(s)),
    ("date",     lambda s: pd.api.types.is_datetime64_any_dtype(s)),
    ("string",   lambda s: _is_string_col(s)),
    ("category", lambda s: str(s.dtype) == "category"),
]

# Canonical Excel-origin dtypes labels (what we report as "from")
def _canonical_from(series: pd.Series) -> str:
    """Human-readable dtype label for the INPUT series."""
    if _is_datetime_col(series):
        return "datetime"
    if _is_bool_col(series):
        return "boolean"
    if _is_numeric_col(series):
        non_null = series.dropna()
        if len(non_null) > 0:
            if (non_null == non_null.astype(int)).all():
                return "int"
        return "float"
    # String column: detect Excel-embedded types
    sample = series.dropna().astype(str).str.strip()
    if sample.empty:
        return "string"

    # Currency / percent → numeric
    no_curr = sample.map(lambda v: _CURRENCY_RE.sub("", _PERCENT_RE.sub("", v)))
    if pd.to_numeric(no_curr, errors="coerce").notna().mean() >= _MATCH_THRESHOLD:
        return "string(numeric)"

    # Date-like strings
    DATE_RE = re.compile(
        r"([-/.]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)",
        re.I,
    )
    if sample.str.contains(DATE_RE, regex=True).mean() >= _MATCH_THRESHOLD:
        try:
            if pd.to_datetime(sample, errors="coerce").notna().mean() >= _MATCH_THRESHOLD:
                return "string(date)"
        except Exception:
            pass

    # Boolean text
    lower = sample.str.lower()
    if lower.isin(_BOOL_TRUE | _BOOL_FALSE).mean() >= _MATCH_THRESHOLD:
        return "string(boolean)"

    return "string"


def _canonical_to(series: pd.Series) -> str:
    """Human-readable dtype label for the OUTPUT series."""
    if _is_datetime_col(series):
        return "datetime"
    if _is_bool_col(series):
        return "boolean"
    if _is_numeric_col(series):
        non_null = series.dropna()
        if len(non_null) > 0:
            try:
                if (non_null == non_null.astype(int)).all():
                    return "int"
            except (ValueError, TypeError):
                pass
        return "float"
    if str(series.dtype) == "category":
        return "category"
    return "string"


def _conversion_is_meaningful(from_type: str, to_type: str) -> bool:
    """
    Suppress trivial / duplicate conversions.
    e.g. string → string (same type), int → float when not significant.
    """
    if from_type == to_type:
        return False
    # "string" → "string(…)" variants are not conversions
    if from_type.startswith("string") and to_type.startswith("string"):
        return False
    return True


def _verify_numeric_conversion(in_s: pd.Series, out_s: pd.Series) -> bool:
    """
    Verify that the output numeric values can be reproduced by coercing
    the input strings (accounting for currency, percent, thousand separators).
    """
    try:
        coerced = _coerce_numeric(in_s).dropna()
        target  = pd.to_numeric(out_s, errors="coerce").dropna()
        if len(coerced) == 0 or len(target) == 0:
            return False
        n = min(len(coerced), len(target))
        matches = sum(
            abs(float(coerced.iloc[i]) - float(target.iloc[i])) <= 1e-6
            for i in range(n)
        )
        return (matches / n) >= _MATCH_THRESHOLD
    except Exception:
        return False


def _verify_datetime_conversion(in_s: pd.Series, out_s: pd.Series) -> bool:
    """Verify string → datetime conversion."""
    try:
        str_in = in_s.dropna().astype(str)
        converted = pd.to_datetime(str_in, errors="coerce").dropna()
        target    = out_s.dropna()
        if len(converted) == 0 or len(target) == 0:
            return False
        n = min(len(converted), len(target))
        # Use .values to avoid "identically-labeled" error when indices differ
        matches = (
            converted.reset_index(drop=True).iloc[:n].values
            == target.reset_index(drop=True).iloc[:n].values
        ).sum()
        return (matches / n) >= _MATCH_THRESHOLD
    except Exception:
        return False


def _verify_bool_conversion(in_s: pd.Series, out_s: pd.Series) -> bool:
    """Verify string boolean text → bool/int conversion."""
    try:
        lower = in_s.dropna().astype(str).str.strip().str.lower()
        mapped = lower.map(
            lambda v: True if v in _BOOL_TRUE else (False if v in _BOOL_FALSE else None)
        )
        target = out_s.dropna()
        # dropna() on mapped may produce fewer rows than target; align on min
        mapped_clean = mapped.dropna().reset_index(drop=True)
        target_clean = target.reset_index(drop=True)
        n = min(len(mapped_clean), len(target_clean))
        if n == 0:
            return False
        # Use .values to avoid "identically-labeled" error
        matches = (
            mapped_clean.iloc[:n].astype(bool).values
            == target_clean.iloc[:n].astype(bool).values
        ).sum()
        return (matches / n) >= _MATCH_THRESHOLD
    except Exception:
        return False


def build_type_conversion_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect TYPE CONVERSION transformations.

    Conversions detected
    --------------------
    - string → int / float   (text numbers, currency "$1,234", percent "12.5%")
    - string → datetime       (date strings in any format)
    - string → boolean        ("yes/no", "true/false", "1/0", "Y/N")
    - int    → float          (integer column widened to float)
    - float  → int            (float rounded/truncated to integer)
    - any    → string         (explicit cast to text)
    - any    → category       (low-cardinality column cast to categorical)

    Edge cases
    ----------
    - Currency prefix/suffix stripped before numeric verification
    - Percent suffix stripped, value NOT divided (raw value compared)
    - European comma-decimal format detected
    - Datetime conversion verified against pandas.to_datetime()
    - Trivial same-type conversions suppressed
    - Empty / all-null columns skipped
    - column_mapping resolved before detection

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "from": str, "to": str}
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)
    cols   = _common_cols(input_df, out_df)
    rules: List[Dict[str, Any]] = []

    for col in cols:
        in_s  = input_df[col]
        out_s = out_df[col]

        from_type = _canonical_from(in_s)
        to_type   = _canonical_to(out_s)

        if not _conversion_is_meaningful(from_type, to_type):
            continue

        # Verify the conversion is real (not just a dtype change with same values)
        verified = False

        if to_type in ("int", "float") and "string" in from_type:
            verified = _verify_numeric_conversion(in_s, out_s)

        elif to_type == "datetime" and "string" in from_type:
            verified = _verify_datetime_conversion(in_s, out_s)

        elif to_type == "boolean" and "string" in from_type:
            verified = _verify_bool_conversion(in_s, out_s)

        elif to_type in ("int", "float") and from_type in ("int", "float"):
            # int→float or float→int: verify values are compatible
            try:
                coerced = pd.to_numeric(in_s, errors="coerce")
                target  = pd.to_numeric(out_s, errors="coerce")
                n = min(len(coerced.dropna()), len(target.dropna()))
                if n > 0:
                    matches = (
                        abs(coerced.dropna().reset_index(drop=True)[:n] -
                            target.dropna().reset_index(drop=True)[:n]) <= 1e-6
                    ).sum()
                    verified = (matches / n) >= _MATCH_THRESHOLD
            except Exception:
                pass

        elif to_type == "string":
            # Cast to string: any → string is verified by checking
            # the output really is a string column
            verified = _is_string_col(out_s) and not _is_string_col(in_s)

        elif to_type == "category":
            verified = str(out_s.dtype) == "category"

        else:
            # For any other pair, require actual dtype change as evidence
            verified = str(in_s.dtype) != str(out_s.dtype)

        if verified:
            rules.append({
                "column": _to_out(col, column_mapping),
                "from":   from_type,
                "to":     to_type,
            })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.4  MISSING VALUES
# ─────────────────────────────────────────────────────────────────────────────

# What pandas / Excel treats as "missing"
_EXCEL_NULLS = {"", "nan", "none", "null", "n/a", "na", "#n/a", "nil", "-"}


def _is_missing(val: Any) -> bool:
    """Return True for any value that should be considered missing."""
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


def _detect_fill_value(in_s: pd.Series, out_s: pd.Series) -> Optional[Any]:
    """
    Detect a constant fill value used to replace NaN / blanks.

    Strategy: rows where in_s is missing and out_s is non-missing
    → if the fill value is consistent (same value for ≥ MATCH_THRESHOLD
    of those rows) return that value; else None.
    """
    was_null  = in_s.map(_is_missing)
    now_filled = was_null & out_s.notna() & out_s.map(lambda v: not _is_missing(v))

    if now_filled.sum() == 0:
        return None

    fill_values = out_s[now_filled].astype(str)
    # Check if all fill values are the same
    unique = fill_values.unique()
    if len(unique) == 1:
        raw = out_s[now_filled].iloc[0]
        return raw

    # Check if fill value is 0 / 0.0  (most common numeric fill)
    numeric_fills = pd.to_numeric(fill_values, errors="coerce").dropna()
    if len(numeric_fills) > 0 and (numeric_fills == 0).mean() >= _MATCH_THRESHOLD:
        return 0

    # Accept the most frequent fill value if it covers ≥ threshold
    counts = fill_values.value_counts()
    top_val, top_count = counts.index[0], counts.iloc[0]
    if top_count / now_filled.sum() >= _MATCH_THRESHOLD:
        return out_s[now_filled & (out_s.astype(str) == top_val)].iloc[0]

    return None


def _detect_ffill_bfill(in_s: pd.Series, out_s: pd.Series) -> Optional[str]:
    """
    Detect forward-fill (ffill) or backward-fill (bfill).

    Returns 'ffill', 'bfill', or None.
    Verifies by reproducing pandas ffill/bfill and comparing to output.
    """
    try:
        for method in ("ffill", "bfill"):
            filled = in_s.fillna(method=method)   # type: ignore[call-arg]
            n = min(len(filled), len(out_s))
            in_null = in_s.iloc[:n].map(_is_missing)
            if in_null.sum() == 0:
                continue
            # Only check rows that were originally null
            null_mask = in_null
            predicted = filled.reset_index(drop=True)[null_mask]
            actual    = out_s.reset_index(drop=True)[null_mask]

            matches = sum(
                str(p) == str(a)
                for p, a in zip(predicted, actual)
                if not _is_missing(a)
            )
            total = null_mask.sum()
            if total > 0 and (matches / total) >= _MATCH_THRESHOLD:
                return method
    except Exception:
        pass
    return None


def _detect_interpolate(in_s: pd.Series, out_s: pd.Series) -> bool:
    """
    Detect linear interpolation fill for numeric columns.
    Verifies pandas .interpolate('linear') reproduces the output.
    """
    if not _is_numeric_col(out_s):
        return False
    try:
        interpolated = in_s.interpolate(method="linear")
        n = min(len(interpolated), len(out_s))
        in_null = in_s.iloc[:n].isna()
        if in_null.sum() == 0:
            return False
        predicted = interpolated.reset_index(drop=True)[in_null]
        actual    = pd.to_numeric(out_s, errors="coerce").reset_index(drop=True)[in_null]
        valid = actual.notna()
        if valid.sum() == 0:
            return False
        matches = (abs(predicted[valid] - actual[valid]) <= 1e-6).sum()
        return (matches / valid.sum()) >= _MATCH_THRESHOLD
    except Exception:
        return False


def _detect_drop(in_s: pd.Series, out_df_col_present: bool) -> bool:
    """
    Detect that an entirely-null column was dropped.
    (Column was in input, missing from output entirely.)
    """
    return not out_df_col_present and in_s.isna().all()


def _detect_flag_column(
    in_s: pd.Series,
    out_df: pd.DataFrame,
    col: str,
) -> Optional[str]:
    """
    Detect a flag column: a new boolean/binary column added to indicate
    where the original column was null.  Returns the flag column name or None.

    Pattern: new output column name contains 'is_null', 'missing', 'flag',
    or '_nan' and its True/1 values correspond to null rows in in_s.
    """
    flag_hints = ["is_null", "is_missing", "missing", "flag", "_nan", "null_flag", "has_"]
    candidates = [
        c for c in out_df.columns
        if any(h in c.lower() for h in flag_hints) and c != col
    ]
    for fc in candidates:
        flag_s = out_df[fc]
        null_mask = in_s.map(_is_missing).reset_index(drop=True)
        flag_val  = flag_s.reset_index(drop=True)
        try:
            matches = (flag_val.astype(bool) == null_mask).mean()
            if matches >= _MATCH_THRESHOLD:
                return fc
        except Exception:
            continue
    return None


def build_missing_values_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect MISSING VALUE handling transformations.

    Methods detected
    ----------------
    fill_constant : NaN → fixed value (0, "Unknown", "N/A", mean, median, mode)
    fill_mean     : NaN → column mean  (verified by computing mean of input)
    fill_median   : NaN → column median
    fill_mode     : NaN → column mode  (most frequent value)
    ffill         : NaN → previous non-null value (forward fill)
    bfill         : NaN → next non-null value (backward fill)
    interpolate   : NaN → linear interpolation (numeric columns only)
    drop          : rows with null in this column were removed
    flag          : a new boolean column was added to mark nulls

    Edge cases
    ----------
    - Excel empty strings treated as nulls ("")
    - "#N/A", "N/A", "nil", "-" treated as nulls
    - Mixed numeric/string columns handled
    - Constant fill vs mean/median distinguished by value comparison
    - Columns with zero nulls in input are not flagged
    - column_mapping resolved before detection
    - Row count may differ (drop) or stay same (fill/flag)

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "method": str, "value": Any}
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if input_df.empty or output_df.empty:
        return empty

    out_df = _remap_output(output_df, column_mapping)
    rules: List[Dict[str, Any]] = []

    for col in input_df.columns:
        in_s = input_df[col]

        # Skip columns with no missing values in input
        null_count = in_s.map(_is_missing).sum()
        if null_count == 0:
            continue

        out_col_present = col in out_df.columns

        # ── DROP: column entirely null and absent in output ───────────────────
        if _detect_drop(in_s, out_col_present):
            rules.append({
                "column": _to_out(col, column_mapping),
                "method": "drop",
                "value":  None,
            })
            continue

        if not out_col_present:
            continue

        out_s = out_df[col]

        # ── DROP: rows with nulls were removed (row count changed) ────────────
        if len(out_df) < len(input_df):
            # Check if rows that are missing in in_s are absent in out_df
            in_null_count  = in_s.map(_is_missing).sum()
            expected_rows  = len(input_df) - in_null_count
            if abs(len(out_df) - expected_rows) <= max(1, len(input_df) * 0.05):
                rules.append({
                    "column": _to_out(col, column_mapping),
                    "method": "drop",
                    "value":  None,
                })
                continue

        # ── FLAG: a new indicator column was added ────────────────────────────
        flag_col = _detect_flag_column(in_s, out_df, col)
        if flag_col:
            rules.append({
                "column": _to_out(col, column_mapping),
                "method": "flag",
                "value":  _to_out(flag_col, column_mapping),
            })
            # Continue — the original column may also have been filled

        # ── FFILL / BFILL ─────────────────────────────────────────────────────
        fill_method = _detect_ffill_bfill(in_s, out_s)
        if fill_method:
            rules.append({
                "column": _to_out(col, column_mapping),
                "method": fill_method,
                "value":  None,
            })
            continue

        # ── INTERPOLATE ───────────────────────────────────────────────────────
        if _detect_interpolate(in_s, out_s):
            rules.append({
                "column": _to_out(col, column_mapping),
                "method": "interpolate",
                "value":  None,
            })
            continue

        # ── FILL with MEAN / MEDIAN / MODE ────────────────────────────────────
        fill_val = _detect_fill_value(in_s, out_s)
        if fill_val is not None and _is_numeric_col(out_s):
            in_numeric = pd.to_numeric(in_s, errors="coerce")
            col_mean   = in_numeric.mean()
            col_median = in_numeric.median()

            if col_mean is not None and not np.isnan(col_mean):
                try:
                    if abs(float(fill_val) - col_mean) <= 1e-4:
                        rules.append({
                            "column": _to_out(col, column_mapping),
                            "method": "fill",
                            "value":  "mean",
                        })
                        continue
                except (TypeError, ValueError):
                    pass

            if col_median is not None and not np.isnan(col_median):
                try:
                    if abs(float(fill_val) - col_median) <= 1e-4:
                        rules.append({
                            "column": _to_out(col, column_mapping),
                            "method": "fill",
                            "value":  "median",
                        })
                        continue
                except (TypeError, ValueError):
                    pass

        # ── FILL with MODE ────────────────────────────────────────────────────
        if fill_val is not None:
            try:
                mode_vals = in_s.dropna().mode()
                if len(mode_vals) > 0:
                    mode_val = mode_vals.iloc[0]
                    if str(fill_val).strip() == str(mode_val).strip():
                        rules.append({
                            "column": _to_out(col, column_mapping),
                            "method": "fill",
                            "value":  "mode",
                        })
                        continue
            except Exception:
                pass

        # ── FILL with CONSTANT ────────────────────────────────────────────────
        if fill_val is not None:
            # Normalise the fill value type
            if isinstance(fill_val, (np.integer,)):
                fill_val = int(fill_val)
            elif isinstance(fill_val, (np.floating,)):
                fill_val = float(fill_val)
            rules.append({
                "column": _to_out(col, column_mapping),
                "method": "fill",
                "value":  fill_val,
            })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
