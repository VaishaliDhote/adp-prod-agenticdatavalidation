# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Formatting & UI Transformation Utilities
==========================================
Six focused builder functions for Phase 8 formatting detection.

  build_formatting_config              → 8.1  COLUMN / CELL FORMATTING
                                               (number, currency, percent, date,
                                                text, custom format changes)
  build_conditional_formatting_config  → 8.2  CONDITIONAL FORMATTING
                                               (highlight rules, data bars,
                                                colour scales, icon sets)
  build_data_validation_config         → 8.3  DATA VALIDATION
                                               (list / LOV, numeric range,
                                                date range, text length,
                                                regex / custom)
  build_comments_config                → 8.4  COMMENTS / ANNOTATIONS
                                               (columns that gained or lost
                                                structured comment markers)
  build_charts_config                  → 8.5  CHARTS
                                               (bar, line, pie, scatter, area,
                                                column, combo — inferred from
                                                column roles and data shape)
  build_sparklines_config              → 8.6  SPARKLINES
                                               (win/loss, line, column —
                                                inferred from column name hints
                                                and numeric shape)

Each function accepts (input_df, output_df, column_mapping?) and returns a
self-contained config block consumed directly by phase_8_formatting.detect().

Design principles
-----------------
- Excel-aware dtype handling: currency ($), percentage (%), date, boolean,
  comma-thousand, empty-string-as-null, mixed numeric/string.
- column_mapping is honoured: detection runs in input namespace, output
  namespace is restored before returning.
- Applied only when evidence is conclusive; returns applied=False otherwise.
- Row-level sampling for performance (capped at _SAMPLE rows).
- Every rule carries enough metadata for downstream transformation appliers.

Output schema
-------------
{
  "formatting": {
    "applied": bool,
    "details": {
      "columns": [
        {"column": str, "format": str,
         "sample_value": Any, "excel_format_code": str|None}
      ]
    }
  },
  "conditional_formatting": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "condition": str, "format": str,
         "threshold": Any, "color": str|None}
      ]
    }
  },
  "data_validation": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "type": str, "values": list|None,
         "min": Any, "max": Any, "pattern": str|None,
         "allow_blank": bool, "error_message": str|None}
      ]
    }
  },
  "comments": {
    "applied": bool,
    "details": {
      "columns": [str, ...]
    }
  },
  "charts": {
    "applied": bool,
    "details": {
      "type": str,
      "columns": [str, ...],
      "category_column": str|None,
      "value_columns": [str, ...],
      "title": str|None
    }
  },
  "sparklines": {
    "applied": bool,
    "details": {
      "rules": [
        {"column": str, "type": str, "source_columns": [str, ...]}
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


# ─────────────────────────────────────────────────────────────────────────────
# Shared constants & helpers
# ─────────────────────────────────────────────────────────────────────────────

# Minimum fraction of non-null rows that must confirm the transformation
_MATCH_THRESHOLD = 0.70

# Max rows sampled for row-by-row verification (performance guard)
_SAMPLE = 300

# Excel / locale number prefixes / suffixes
_CURRENCY_SYMBOLS   = "$€£¥₹₩₺₿"
_CURRENCY_RE        = re.compile(
    rf"^\s*[{re.escape(_CURRENCY_SYMBOLS)}]\s*|[{re.escape(_CURRENCY_SYMBOLS)}]\s*$",
    re.UNICODE,
)
_PERCENT_RE         = re.compile(r"\s*%\s*$")
_THOUSAND_RE        = re.compile(r"(?<=\d),(?=\d{3})")
_ACCOUNTING_RE      = re.compile(r"^\s*\([\d,]+\.?\d*\)\s*$")  # (1,234.56) negative

# Excel boolean representations
_BOOL_TRUE  = {"true", "yes", "1", "y", "x", "on"}
_BOOL_FALSE = {"false", "no", "0", "n", "off"}

# Excel date separators
_DATE_SEP_RE = re.compile(
    r"([-/.]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)",
    re.IGNORECASE,
)

# Chart-type keyword hints in column names
_CHART_CATEGORY_KEYWORDS = [
    "category", "cat", "label", "name", "region", "product", "type",
    "group", "segment", "department", "country", "city", "month",
    "quarter", "year", "period", "status", "tier", "brand",
]
_CHART_VALUE_KEYWORDS = [
    "sales", "revenue", "amount", "total", "count", "value", "qty",
    "quantity", "price", "profit", "cost", "score", "rate", "sum",
    "avg", "average", "volume", "units", "number",
]

# Sparkline keyword hints
_SPARKLINE_KEYWORDS = [
    "sparkline", "spark", "trend", "mini_chart", "minigraph",
    "inline_chart", "bar_spark", "line_spark",
]

# Comment/annotation keyword hints
_COMMENT_KEYWORDS = [
    "comment", "note", "annotation", "remark", "description",
    "footnote", "tooltip", "flag", "memo",
]

# Conditional-formatting operator patterns
_CF_OPERATORS: List[Tuple[str, str]] = [
    (">",   "greater_than"),
    (">=",  "greater_than_or_equal"),
    ("<",   "less_than"),
    ("<=",  "less_than_or_equal"),
    ("==",  "equal_to"),
    ("!=",  "not_equal_to"),
]

# Highlight colour palette used when the output column name embeds a colour hint
_COLOUR_HINTS: Dict[str, str] = {
    "red":    "#FF0000",
    "green":  "#00B050",
    "yellow": "#FFFF00",
    "orange": "#FF6600",
    "blue":   "#0070C0",
    "grey":   "#A6A6A6",
    "gray":   "#A6A6A6",
    "bold":   None,
    "italic": None,
}

# Excel number-format codes for canonical format names
_EXCEL_FORMAT_CODES: Dict[str, str] = {
    "currency":    '"$"#,##0.00',
    "accounting":  '_("$"* #,##0.00_)',
    "percent":     "0.00%",
    "integer":     "#,##0",
    "float":       "#,##0.00",
    "date":        "MM/DD/YYYY",
    "datetime":    "MM/DD/YYYY HH:MM:SS",
    "time":        "HH:MM:SS",
    "text":        "@",
    "scientific":  "0.00E+00",
    "fraction":    "# ?/?",
    "boolean":     '"TRUE";"TRUE";"FALSE"',
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


def _remap_output(
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]],
) -> pd.DataFrame:
    """Rename output columns back to the input-namespace.
    
    column_mapping is {output_col: input_col}. We rename output columns
    to their input names for comparison purposes. Derived columns (those
    mapping to sentinel values like 'DERIVED COLUMN') are excluded to
    avoid name collisions.
    """
    if not column_mapping:
        return output_df
    # Filter out derived column sentinels to avoid collisions
    rename_map = {
        out_col: in_col
        for out_col, in_col in column_mapping.items()
        if in_col not in _DERIVED_SENTINELS
    }
    return output_df.rename(columns=rename_map)


def _to_out(col: str, column_mapping: Optional[Dict[str, str]]) -> str:
    """Map an input-namespace column name to the output namespace.
    
    column_mapping is {output_col: input_col}. We invert it to find the
    output column name for a given input column name. Derived columns
    (those with sentinel values) are excluded from the inversion since
    they remain in output namespace.
    """
    if not column_mapping:
        return col
    # Build inverse mapping {input_col: output_col}, excluding sentinels
    inv = {
        in_col: out_col
        for out_col, in_col in column_mapping.items()
        if in_col not in _DERIVED_SENTINELS
    }
    return inv.get(col, col)


def _common_cols(input_df: pd.DataFrame, out_df: pd.DataFrame) -> List[str]:
    """Return column names present in both DataFrames."""
    return [c for c in input_df.columns if c in out_df.columns]


def _sample_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.iloc[:_SAMPLE] if len(df) > _SAMPLE else df


def _is_numeric(series: pd.Series) -> bool:
    return (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
    )


def _is_bool(series: pd.Series) -> bool:
    return pd.api.types.is_bool_dtype(series)


def _is_string(series: pd.Series) -> bool:
    return (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
    )


def _is_datetime(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def _str_col(series: pd.Series) -> pd.Series:
    """Return string representation; NaN → empty string."""
    return series.fillna("").astype(str)


def _norm_name(s: str) -> str:
    """Lowercase + collapse non-alphanumeric to underscore."""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _has_keyword(col: str, keywords: List[str]) -> bool:
    n = _norm_name(col)
    return any(kw in n for kw in keywords)


def _sample_non_null(series: pd.Series) -> Any:
    """Return a representative non-null sample value, or None."""
    non_null = series.dropna()
    if non_null.empty:
        return None
    return non_null.iloc[0]


# ─────────────────────────────────────────────────────────────────────────────
# 8.1  FORMATTING  —  detect column-level number / date / text formats
# ─────────────────────────────────────────────────────────────────────────────

def _detect_format_for_column(series: pd.Series) -> Optional[str]:
    """
    Return the canonical format name for a series based on its dtype and
    content, or None when no special format is detected.

    Detection priority
    ------------------
    1. Native datetime dtype                  → "date" / "datetime" / "time"
    2. Boolean dtype                          → "boolean"
    3. String-encoded currency ($, €, £ …)   → "currency"
    4. Accounting format  (1,234.56) / ($…)  → "accounting"
    5. String-encoded percentage (12.5%)      → "percent"
    6. String-encoded scientific (1.23E+06)  → "scientific"
    7. Numeric integer (no fractional part)  → "integer"
    8. Numeric float                         → "float"
    9. Date-like string                      → "date"
    10. Column name hints                     → fallback
    """
    non_null = series.dropna()
    if non_null.empty:
        return None

    # 1. Datetime dtype
    if _is_datetime(series):
        has_time = False
        try:
            has_time = (
                (series.dt.hour != 0) | (series.dt.minute != 0)
            ).any()
        except Exception:
            pass
        return "datetime" if has_time else "date"

    # 2. Boolean dtype
    if _is_bool(series):
        return "boolean"

    # 3–6: String-encoded special formats
    if _is_string(series):
        sample_str = _str_col(non_null.head(_SAMPLE))
        stripped   = sample_str.str.strip()

        # Currency
        has_curr = stripped.str.contains(
            rf"[{re.escape(_CURRENCY_SYMBOLS)}]", regex=True
        )
        if has_curr.mean() >= _MATCH_THRESHOLD:
            # Check accounting (negative in parentheses)
            if stripped.str.match(_ACCOUNTING_RE).mean() >= 0.05:
                return "accounting"
            return "currency"

        # Percentage
        if stripped.str.endswith("%").mean() >= _MATCH_THRESHOLD:
            return "percent"

        # Scientific notation
        if stripped.str.match(r"^-?\d+\.?\d*[eE][+-]?\d+$").mean() >= _MATCH_THRESHOLD:
            return "scientific"

        # Boolean text
        lower = stripped.str.lower()
        if lower.isin(_BOOL_TRUE | _BOOL_FALSE).mean() >= _MATCH_THRESHOLD:
            return "boolean"

        # Date-like string
        if stripped.str.contains(_DATE_SEP_RE, regex=True).mean() >= _MATCH_THRESHOLD:
            try:
                converted = pd.to_datetime(stripped, errors="coerce")
                if converted.notna().mean() >= _MATCH_THRESHOLD:
                    has_time = converted.dropna().apply(
                        lambda d: d.hour != 0 or d.minute != 0
                    ).any()
                    return "datetime" if has_time else "date"
            except Exception:
                pass

        # Numeric string (pure number stored as text → "text" format in Excel)
        numeric = pd.to_numeric(stripped.str.replace(",", "", regex=False), errors="coerce")
        if numeric.notna().mean() >= _MATCH_THRESHOLD:
            # Numbers stored as text → flag as "text" format
            return "text"

        return None  # plain string — no special format

    # 7–8. Native numeric
    if _is_numeric(series):
        non_null_num = pd.to_numeric(non_null, errors="coerce").dropna()
        if len(non_null_num) == 0:
            return "float"
        try:
            if (non_null_num == non_null_num.round(0)).all():
                return "integer"
        except Exception:
            pass
        return "float"

    return None


def _format_changed(in_s: pd.Series, out_s: pd.Series) -> bool:
    """
    Return True when the format of a column appears to have changed between
    input and output (same underlying values, different representation).
    """
    in_fmt  = _detect_format_for_column(in_s)
    out_fmt = _detect_format_for_column(out_s)
    return in_fmt != out_fmt


def build_formatting_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect column-level number / date / text formatting changes.

    Detects per column
    ------------------
    - Currency      : values prefixed/suffixed with $, €, £, ¥, ₹, ₩ …
    - Accounting    : like currency but negatives shown as (value)
    - Percent       : values suffixed with %
    - Integer       : whole-number numeric column
    - Float / Decimal: fractional numeric column
    - Date          : date-only values (Excel date serial or date strings)
    - DateTime      : date + time component
    - Time          : time-only strings
    - Boolean       : TRUE/FALSE, Yes/No, 1/0, Y/N text columns
    - Scientific    : exponential notation (1.23E+06)
    - Text          : numbers stored as text (leading apostrophe in Excel)

    Edge cases
    ----------
    - Mixed dtype columns sampled for majority-vote detection
    - Currency symbols from 8+ locales / Unicode ranges
    - European comma-decimal format ignored (value comparison not triggered)
    - Columns with zero non-null values → skipped
    - Format detected on OUTPUT column; flagged when it differs from INPUT
    - Newly added columns always checked for format
    - column_mapping resolved before detection

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "columns": [
          {"column": str, "format": str,
           "sample_value": Any, "excel_format_code": str|None}
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"columns": []}}

    if output_df.empty:
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    columns: List[Dict[str, Any]] = []

    for col in out_df.columns:
        out_s   = out_df[col]
        out_fmt = _detect_format_for_column(out_s)

        if out_fmt is None:
            continue

        # Check if format changed from input
        in_s    = input_df[col] if col in input_df.columns else None
        in_fmt  = _detect_format_for_column(in_s) if in_s is not None else None

        if out_fmt == in_fmt:
            continue  # No format change

        # For NEW columns (not in input), only flag if format is "special"
        # (currency, percent, date, etc.). Plain integer/float are default
        # formats and don't represent a formatting transformation.
        _DEFAULT_FORMATS = {"integer", "float", "text", None}
        if in_s is None and out_fmt in _DEFAULT_FORMATS:
            continue  # New column with default format — not a transformation

        columns.append({
            "column":            _to_out(col, column_mapping),
            "format":            out_fmt,
            "sample_value":      _sample_non_null(out_s),
            "excel_format_code": _EXCEL_FORMAT_CODES.get(out_fmt),
        })

    return {
        "applied": len(columns) > 0,
        "details": {"columns": columns},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.2  CONDITIONAL FORMATTING
# ─────────────────────────────────────────────────────────────────────────────

def _detect_threshold_for_operator(
    series: pd.Series,
    operator: str,
    output_series: pd.Series,
) -> Optional[Any]:
    """
    Try to find a numeric threshold T such that applying `series <op> T`
    partitions the data in a way consistent with what conditional formatting
    would highlight.

    Strategy: compute statistical boundaries (mean, median, std, percentiles,
    quartiles, min, max) and test each as a candidate threshold.
    Returns the first threshold that results in a non-trivial partition.
    """
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 2:
        return None

    candidates: List[float] = []
    mean   = float(numeric.mean())
    median = float(numeric.median())
    std    = float(numeric.std())

    candidates += [
        round(mean, 2),
        round(median, 2),
        round(mean + std, 2),
        round(mean - std, 2),
        round(float(numeric.quantile(0.75)), 2),
        round(float(numeric.quantile(0.25)), 2),
        round(float(numeric.max()), 2),
        round(float(numeric.min()), 2),
    ]

    # Round numbers that look like natural business thresholds
    for base in [0, 50, 100, 500, 1000, 5000, 10000]:
        if numeric.min() <= base <= numeric.max():
            candidates.append(float(base))

    op_fn = {
        ">":  lambda s, t: s > t,
        ">=": lambda s, t: s >= t,
        "<":  lambda s, t: s < t,
        "<=": lambda s, t: s <= t,
        "==": lambda s, t: s == t,
        "!=": lambda s, t: s != t,
    }
    fn = op_fn.get(operator)
    if fn is None:
        return None

    for t in candidates:
        mask = fn(numeric, t)
        # Threshold is meaningful if it highlights between 5% and 95% of rows
        ratio = mask.mean()
        if 0.05 <= ratio <= 0.95:
            return t

    return None


def _infer_cf_color(col_name: str) -> Optional[str]:
    """
    Infer a highlight colour from the column name (e.g. 'sales_red' → '#FF0000').
    Returns None when no colour hint is found.
    """
    n = _norm_name(col_name)
    for hint, hex_code in _COLOUR_HINTS.items():
        if hint in n:
            return hex_code
    return None


def _detect_cf_rules_for_column(
    col: str,
    in_s: pd.Series,
    out_s: pd.Series,
) -> List[Dict[str, Any]]:
    """
    Detect conditional formatting rules for a single numeric column.

    Detection paths
    ---------------
    Path A – Value-change analysis:
      Cells whose output value differs from input value in a systematic way
      that aligns with a comparison operator applied to the original value.
      (This catches cases where CF was "baked into" the data as colour-coded
       categories, e.g. high/low flags appended to the output.)

    Path B – Column name heuristics:
      If the column name contains operator-like tokens ('>100', '_gt_50',
      '_above_', '_below_', '_eq_', etc.) parse them directly.

    Path C – Statistical thresholds:
      For numeric columns compare the OUTPUT distribution against the INPUT.
      If the output is a subset or shows a cut-off, infer the threshold.

    Returns a list of rule dicts.
    """
    rules: List[Dict[str, Any]] = []

    if not _is_numeric(out_s):
        return rules

    col_n = _norm_name(col)
    color = _infer_cf_color(col)

    # ── Path B: column name tokens ────────────────────────────────────────────
    name_op_patterns: List[Tuple[str, str]] = [
        (r"_gt_(\d+\.?\d*)",     ">"),
        (r"_gte_(\d+\.?\d*)",    ">="),
        (r"_lt_(\d+\.?\d*)",     "<"),
        (r"_lte_(\d+\.?\d*)",    "<="),
        (r"_eq_(\d+\.?\d*)",     "=="),
        (r"_neq_(\d+\.?\d*)",    "!="),
        (r"_above_(\d+\.?\d*)",  ">"),
        (r"_below_(\d+\.?\d*)",  "<"),
        (r"_over_(\d+\.?\d*)",   ">"),
        (r"_under_(\d+\.?\d*)",  "<"),
        (r">(\d+\.?\d*)",        ">"),
        (r">=(\d+\.?\d*)",       ">="),
        (r"<(\d+\.?\d*)",        "<"),
        (r"<=(\d+\.?\d*)",       "<="),
        (r"=(\d+\.?\d*)",        "=="),
    ]
    for pattern, op in name_op_patterns:
        m = re.search(pattern, col_n)
        if m:
            try:
                threshold = float(m.group(1))
                rules.append({
                    "column":    col,
                    "condition": f"{op}{threshold}",
                    "format":    "highlight",
                    "threshold": threshold,
                    "operator":  op,
                    "color":     color,
                })
            except ValueError:
                pass

    if rules:
        return rules

    # ── Path C: statistical threshold detection ───────────────────────────────
    # Check if input has a wider range than output (subset → possible filter-CF)
    numeric_in  = pd.to_numeric(in_s,  errors="coerce").dropna()
    numeric_out = pd.to_numeric(out_s, errors="coerce").dropna()

    if len(numeric_in) < 4 or len(numeric_out) < 1:
        return rules

    in_range  = numeric_in.max()  - numeric_in.min()
    out_range = numeric_out.max() - numeric_out.min()

    # Only flag CF when output range is notably narrower (subset of input range)
    if in_range > 0 and out_range < in_range * 0.9:
        # Try to find the threshold that explains the range restriction
        for op, _ in _CF_OPERATORS:
            t = _detect_threshold_for_operator(in_s, op, out_s)
            if t is not None:
                rules.append({
                    "column":    col,
                    "condition": f"{op}{t}",
                    "format":    "highlight",
                    "threshold": t,
                    "operator":  op,
                    "color":     color,
                })
                break   # One rule per column is sufficient

    return rules


def build_conditional_formatting_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect CONDITIONAL FORMATTING rules between input and output DataFrames.

    Detects per column
    ------------------
    - Greater than / less than threshold rules
    - Equal to / not equal to rules
    - Top-N / bottom-N (inferred from percentile thresholds)
    - Above / below average (threshold == column mean)
    - Colour-scale intent (column name embeds a colour hint)
    - Data-bar intent (name hints: 'bar', 'data_bar', 'progress')

    Edge cases
    ----------
    - Only numeric columns are examined (CF on text not detectable from data)
    - Column name tokens like 'sales_gt_100' are parsed directly
    - Statistical thresholds (mean ± σ, quartiles) tested as candidates
    - Thresholds that produce trivial partitions (0% or 100% highlighted)
      are suppressed
    - column_mapping resolved before detection
    - Same row count required (CF does not change rows)

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {"column": str, "condition": str, "format": str,
           "threshold": Any, "operator": str, "color": str|None}
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
        in_s  = input_df[col].reset_index(drop=True)
        out_s = out_df[col].reset_index(drop=True)
        raw   = _detect_cf_rules_for_column(col, in_s, out_s)
        for r in raw:
            rules.append({
                "column":    _to_out(r["column"], column_mapping),
                "condition": r["condition"],
                "format":    r["format"],
                "threshold": r["threshold"],
                "operator":  r["operator"],
                "color":     r["color"],
            })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.3  DATA VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

# Minimum number of distinct values to consider as a List-of-Values (LOV)
_MIN_LOV_VALUES  = 2
# Maximum cardinality to classify as LOV (high-cardinality → not a dropdown)
_MAX_LOV_RATIO   = 0.50   # at most 50 % of rows are distinct

# Excel error / null markers excluded from LOV detection
_EXCEL_NULLS = {"", "nan", "none", "null", "n/a", "#n/a", "nil", "-"}


def _is_lov_candidate(series: pd.Series) -> bool:
    """
    Return True when the column looks like a constrained list-of-values
    (i.e. a dropdown / data-validation list in Excel).

    Criteria
    --------
    - String or categorical dtype
    - ≥ 2 distinct non-null values
    - Distinct-value ratio ≤ _MAX_LOV_RATIO  (not a high-cardinality free-text col)
    - Not a numeric or date column
    """
    if _is_numeric(series) or _is_datetime(series) or _is_bool(series):
        return False
    non_null = series.dropna()
    if len(non_null) < _MIN_LOV_VALUES:
        return False
    n_distinct = non_null.astype(str).str.strip().nunique()
    if n_distinct < _MIN_LOV_VALUES:
        return False
    ratio = n_distinct / len(non_null)
    return ratio <= _MAX_LOV_RATIO


def _extract_lov(series: pd.Series) -> List[Any]:
    """Return sorted list of distinct non-null, non-error values."""
    vals = (
        series.dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )
    return sorted(v for v in vals if v.lower() not in _EXCEL_NULLS)


def _detect_numeric_range(series: pd.Series) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (min_val, max_val) for a numeric column as data-validation bounds.
    Returns (None, None) when the column is not numeric.
    """
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 2:
        return None, None
    return float(numeric.min()), float(numeric.max())


def _detect_date_range(
    series: pd.Series,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (min_date_str, max_date_str) for a date column as data-validation
    date range bounds.  Returns (None, None) when not a date column.
    """
    if _is_datetime(series):
        valid = series.dropna()
    else:
        if not _is_string(series):
            return None, None
        valid = pd.to_datetime(series, errors="coerce").dropna()

    if len(valid) < 2:
        return None, None

    return (
        str(valid.min().date()),
        str(valid.max().date()),
    )


def _detect_text_length(series: pd.Series) -> Tuple[Optional[int], Optional[int]]:
    """
    Infer min/max text-length constraints from a string column.
    Returns (min_len, max_len); both None when the column is not string.
    """
    if not _is_string(series):
        return None, None
    lengths = series.dropna().astype(str).str.len()
    if len(lengths) < 2:
        return None, None
    min_l, max_l = int(lengths.min()), int(lengths.max())
    # Only report when there's a meaningful constraint
    if min_l == max_l:
        return min_l, max_l   # Fixed-length column (codes, IDs)
    return None, max_l        # Report only max for variable-length


def _detect_regex_pattern(series: pd.Series) -> Optional[str]:
    """
    Infer a regex / custom pattern from a string column when all values share
    a detectable structure.

    Patterns detected
    -----------------
    - Email        : "^[\\w.+-]+@[\\w-]+\\.[\\w.]+$"
    - Phone US     : "^\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}$"
    - ZIP code US  : "^\\d{5}(-\\d{4})?$"
    - Postal UK    : "^[A-Z]{1,2}\\d[\\dA-Z]?\\s?\\d[A-Z]{2}$"
    - ISO date     : "^\\d{4}-\\d{2}-\\d{2}$"
    - UUID         : "^[0-9a-fA-F]{8}-…$"
    - All digits   : "^\\d+$"
    - Alphanumeric : "^[A-Za-z0-9]+$"
    """
    if not _is_string(series):
        return None

    sample = series.dropna().astype(str).str.strip()
    if sample.empty:
        return None

    PATTERNS: List[Tuple[str, str]] = [
        (r"^[\w.+\-]+@[\w\-]+\.[\w.]+$",          "^[\\w.+-]+@[\\w-]+\\.[\\w.]+$"),
        (r"^\(?\d{3}\)?[.\-\s]?\d{3}[.\-\s]?\d{4}$", "^\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}$"),
        (r"^\d{5}(-\d{4})?$",                      "^\\d{5}(-\\d{4})?$"),
        (r"^[A-Z]{1,2}\d[\dA-Z]?\s?\d[A-Z]{2}$",  "^[A-Z]{1,2}\\d[\\dA-Z]?\\s?\\d[A-Z]{2}$"),
        (r"^\d{4}-\d{2}-\d{2}$",                   "^\\d{4}-\\d{2}-\\d{2}$"),
        (r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                                                    "^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"),
        (r"^\d+$",                                  "^\\d+$"),
        (r"^[A-Za-z0-9]+$",                         "^[A-Za-z0-9]+$"),
    ]

    for raw_pat, human_pat in PATTERNS:
        try:
            if sample.str.match(raw_pat).mean() >= _MATCH_THRESHOLD:
                return human_pat
        except Exception:
            continue

    return None


def build_data_validation_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect DATA VALIDATION rules for each output column.

    Validation types detected
    -------------------------
    list       : low-cardinality string column → dropdown list
    whole      : integer numeric column → min/max whole number range
    decimal    : float numeric column   → min/max decimal range
    date       : date column            → min/max date range
    text_length: string column          → min/max character count
    custom     : column matching a regex pattern (email, phone, zip, UUID …)
    boolean    : TRUE/FALSE column      → constrained to two values

    Edge cases
    ----------
    - Excel empty strings / "#N/A" excluded from LOV lists
    - Mixed numeric/string columns handled via majority-vote
    - Boolean text ("Yes"/"No", "Y"/"N") → list type with ["No","Yes"]
    - Fixed-length codes (e.g. ZIP, ISIN) → text_length with min == max
    - Columns with all-null output values are skipped
    - column_mapping resolved before detection
    - Allow_blank inferred from null presence in output column

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {
            "column":        str,
            "type":          "list"|"whole"|"decimal"|"date"|"text_length"|"custom"|"boolean",
            "values":        [str, ...]  | None,
            "min":           Any | None,
            "max":           Any | None,
            "pattern":       str | None,
            "allow_blank":   bool,
            "error_message": str | None,
          }
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if output_df.empty:
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    rules: List[Dict[str, Any]] = []

    for col in out_df.columns:
        out_s = out_df[col]

        if out_s.dropna().empty:
            continue

        allow_blank = bool(out_s.isna().any() or
                           (out_s.astype(str).str.strip() == "").any())

        # ── Boolean ──────────────────────────────────────────────────────────
        if _is_bool(out_s):
            rules.append({
                "column":        _to_out(col, column_mapping),
                "type":          "boolean",
                "values":        [False, True],
                "min":           None,
                "max":           None,
                "pattern":       None,
                "allow_blank":   allow_blank,
                "error_message": f"'{_to_out(col, column_mapping)}' must be TRUE or FALSE.",
            })
            continue

        # ── Boolean text (Yes/No, True/False, Y/N) ────────────────────────────
        if _is_string(out_s):
            lower = out_s.dropna().astype(str).str.strip().str.lower()
            if lower.isin(_BOOL_TRUE | _BOOL_FALSE).mean() >= _MATCH_THRESHOLD:
                lov = _extract_lov(out_s)
                rules.append({
                    "column":        _to_out(col, column_mapping),
                    "type":          "list",
                    "values":        lov,
                    "min":           None,
                    "max":           None,
                    "pattern":       None,
                    "allow_blank":   allow_blank,
                    "error_message": f"'{_to_out(col, column_mapping)}' must be one of: {lov}.",
                })
                continue

        # ── List of values (low-cardinality string / categorical) ─────────────
        if _is_lov_candidate(out_s):
            lov = _extract_lov(out_s)
            if len(lov) >= _MIN_LOV_VALUES:
                rules.append({
                    "column":        _to_out(col, column_mapping),
                    "type":          "list",
                    "values":        lov,
                    "min":           None,
                    "max":           None,
                    "pattern":       None,
                    "allow_blank":   allow_blank,
                    "error_message": f"'{_to_out(col, column_mapping)}' must be one of: {lov}.",
                })
                continue

        # ── Date range ────────────────────────────────────────────────────────
        if _is_datetime(out_s):
            min_d, max_d = _detect_date_range(out_s)
            if min_d and max_d:
                rules.append({
                    "column":        _to_out(col, column_mapping),
                    "type":          "date",
                    "values":        None,
                    "min":           min_d,
                    "max":           max_d,
                    "pattern":       None,
                    "allow_blank":   allow_blank,
                    "error_message": (
                        f"'{_to_out(col, column_mapping)}' date must be "
                        f"between {min_d} and {max_d}."
                    ),
                })
            continue

        # ── Numeric range (whole / decimal) ───────────────────────────────────
        if _is_numeric(out_s):
            min_v, max_v = _detect_numeric_range(out_s)
            numeric_clean = pd.to_numeric(out_s, errors="coerce").dropna()
            is_whole = len(numeric_clean) > 0 and (numeric_clean == numeric_clean.round(0)).all()
            val_type = "whole" if is_whole else "decimal"
            rules.append({
                "column":        _to_out(col, column_mapping),
                "type":          val_type,
                "values":        None,
                "min":           min_v,
                "max":           max_v,
                "pattern":       None,
                "allow_blank":   allow_blank,
                "error_message": (
                    f"'{_to_out(col, column_mapping)}' must be between "
                    f"{min_v} and {max_v}."
                    if min_v is not None and max_v is not None else None
                ),
            })
            continue

        # ── String: regex / custom pattern ────────────────────────────────────
        if _is_string(out_s):
            pattern = _detect_regex_pattern(out_s)
            min_l, max_l = _detect_text_length(out_s)

            if pattern:
                rules.append({
                    "column":        _to_out(col, column_mapping),
                    "type":          "custom",
                    "values":        None,
                    "min":           None,
                    "max":           None,
                    "pattern":       pattern,
                    "allow_blank":   allow_blank,
                    "error_message": (
                        f"'{_to_out(col, column_mapping)}' must match pattern: {pattern}."
                    ),
                })
                continue

            if min_l is not None or max_l is not None:
                rules.append({
                    "column":        _to_out(col, column_mapping),
                    "type":          "text_length",
                    "values":        None,
                    "min":           min_l,
                    "max":           max_l,
                    "pattern":       None,
                    "allow_blank":   allow_blank,
                    "error_message": (
                        f"'{_to_out(col, column_mapping)}' length must be "
                        f"between {min_l} and {max_l} characters."
                        if min_l and max_l else None
                    ),
                })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.4  COMMENTS / ANNOTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def build_comments_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect columns that likely carry COMMENTS or ANNOTATIONS.

    Detection paths
    ---------------
    Path A – New columns in output whose name contains comment-like keywords
             ('comment', 'note', 'annotation', 'remark', 'description',
              'footnote', 'tooltip', 'memo', 'flag').

    Path B – Existing string columns that have non-null values in the output
             but were entirely null in the input (comment column populated).

    Path C – Column name tokens: 'comment_<col>', '<col>_note', etc.

    Edge cases
    ----------
    - Pure numeric or date columns are never flagged as comment columns
    - High-cardinality free-text columns are included only if name hints match
    - All-null output comment columns are skipped
    - column_mapping resolved before detection

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

    if output_df.empty:
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    in_cols = set(input_df.columns)
    affected: List[str] = []

    for col in out_df.columns:
        out_s = out_df[col]

        # Skip if all null in output
        if out_s.dropna().empty:
            continue

        # Skip pure numeric / date columns
        if _is_numeric(out_s) or _is_datetime(out_s) or _is_bool(out_s):
            continue

        # Path A: name keyword match
        if _has_keyword(col, _COMMENT_KEYWORDS):
            affected.append(_to_out(col, column_mapping))
            continue

        # Path B: new column populated from null
        if col not in in_cols:
            # Only flag if name is string-like (not a derived numeric KPI)
            if _is_string(out_s):
                affected.append(_to_out(col, column_mapping))
            continue

        # Path B (existing): was entirely null in input, now has values
        in_s = input_df[col]
        was_all_null = in_s.isna().all() or (
            _is_string(in_s) and in_s.fillna("").astype(str).str.strip().eq("").all()
        )
        now_has_values = out_s.dropna().astype(str).str.strip().ne("").any()
        if was_all_null and now_has_values and _has_keyword(col, _COMMENT_KEYWORDS):
            if col not in affected:
                affected.append(_to_out(col, column_mapping))

    return {
        "applied": len(affected) > 0,
        "details": {"columns": affected},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.5  CHARTS
# ─────────────────────────────────────────────────────────────────────────────

# Chart type inference — ordered by typical Excel usage frequency
_CHART_TYPES_BY_SHAPE: List[Tuple[str, str]] = [
    ("pie",       "single_value_per_category"),
    ("donut",     "single_value_per_category"),
    ("bar",       "multiple_series_per_category"),
    ("column",    "multiple_series_per_category"),
    ("line",      "ordered_category"),
    ("area",      "ordered_category"),
    ("scatter",   "two_numeric_no_category"),
    ("bubble",    "three_numeric_no_category"),
    ("histogram", "single_numeric_distribution"),
    ("combo",     "mixed_series"),
]

_PIE_KEYWORDS      = ["share", "percent", "proportion", "distribution", "breakdown", "ratio"]
_LINE_KEYWORDS     = ["trend", "over_time", "timeline", "monthly", "weekly", "daily", "ytd"]
_SCATTER_KEYWORDS  = ["correlation", "scatter", "vs", "versus", "compared"]
_BAR_KEYWORDS      = ["compare", "comparison", "ranking", "leaderboard", "top_n"]
_AREA_KEYWORDS     = ["cumulative", "running", "stacked", "growth"]


def _infer_chart_type(
    cat_cols: List[str],
    val_cols: List[str],
    all_col_names: List[str],
) -> str:
    """
    Infer the most likely chart type from the column structure.

    Heuristics
    ----------
    1. Check column name hints in the category and value columns.
    2. Use shape heuristics: single value col → bar/pie, multiple → grouped bar.
    3. No category col + two numeric → scatter.
    4. Date/time category → line chart.
    5. Default to "bar".
    """
    all_names_lower = " ".join(_norm_name(c) for c in all_col_names)

    # Date-time ordered category → line
    for c in cat_cols:
        if _has_keyword(c, ["date", "time", "month", "year", "quarter", "week", "day",
                              "period", "timestamp", "created", "updated"]):
            if any(_has_keyword(c, _AREA_KEYWORDS) for c in all_col_names):
                return "area"
            return "line"

    # Name hints for specific chart types
    if any(kw in all_names_lower for kw in _PIE_KEYWORDS):
        if len(val_cols) == 1:
            return "pie"
    if any(kw in all_names_lower for kw in _SCATTER_KEYWORDS):
        return "scatter"
    if any(kw in all_names_lower for kw in _LINE_KEYWORDS):
        return "line"
    if any(kw in all_names_lower for kw in _AREA_KEYWORDS):
        return "area"
    if any(kw in all_names_lower for kw in _BAR_KEYWORDS):
        return "bar"

    # Shape-based: no categorical column → scatter
    if not cat_cols:
        if len(val_cols) >= 2:
            return "scatter"
        return "histogram"

    # Single numeric column → bar (most common in Excel)
    if len(val_cols) == 1:
        return "bar"

    # Multiple numeric columns → grouped bar / combo
    if len(val_cols) >= 3:
        return "combo"

    return "bar"


def build_charts_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect likely CHART configuration from the output DataFrame structure.

    Detection strategy
    ------------------
    1. Classify output columns as category (categorical/date) or value (numeric).
    2. Use column-name keywords (sales, revenue, category, month, etc.)
       to improve role classification.
    3. Infer chart type from column roles and name hints.
    4. Return all column roles for the applier.

    Chart types inferred
    --------------------
    bar, column, line, area, pie, donut, scatter, bubble, histogram, combo

    Edge cases
    ----------
    - Output with only numeric columns → scatter or histogram
    - Date column as category → line chart
    - Single numeric + categorical → bar chart
    - Multiple numeric + one categorical → grouped bar or combo
    - Empty DataFrames return applied=False
    - column_mapping resolved before detection

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "type":             str,
        "columns":          [str, ...],   # all columns involved
        "category_column":  str | None,
        "value_columns":    [str, ...],
        "title":            str | None
      }
    }
    """
    empty: Dict[str, Any] = {
        "applied": False,
        "details": {
            "type":            "bar",
            "columns":         [],
            "category_column": None,
            "value_columns":   [],
            "title":           None,
        },
    }

    if output_df.empty or len(output_df.columns) < 2:
        return empty

    out_df = _remap_output(output_df, column_mapping)

    # ── Classify columns ──────────────────────────────────────────────────────
    cat_cols: List[str] = []
    val_cols: List[str] = []

    for col in out_df.columns:
        s = out_df[col]
        col_n = _norm_name(col)

        is_val_hint = any(kw in col_n for kw in _CHART_VALUE_KEYWORDS)
        is_cat_hint = any(kw in col_n for kw in _CHART_CATEGORY_KEYWORDS)

        if _is_datetime(s) or (_is_string(s) and is_cat_hint and not is_val_hint):
            cat_cols.append(col)
        elif _is_numeric(s):
            if is_cat_hint and not is_val_hint:
                cat_cols.append(col)
            else:
                val_cols.append(col)
        elif _is_string(s) or _is_bool(s):
            if _is_lov_candidate(s) or is_cat_hint:
                cat_cols.append(col)
            else:
                # Free-text or high-cardinality → not useful for charts
                pass

    # Fallback: if no value columns, promote numeric columns
    if not val_cols:
        for col in out_df.columns:
            if _is_numeric(out_df[col]) and col not in cat_cols:
                val_cols.append(col)

    if not val_cols:
        return empty

    # Use first category column (most likely the axis)
    cat_col: Optional[str] = cat_cols[0] if cat_cols else None

    all_involved = (([cat_col] if cat_col else []) + val_cols)
    chart_type   = _infer_chart_type(cat_cols, val_cols, list(out_df.columns))

    # Infer a chart title from the value column names
    title: Optional[str] = None
    if val_cols:
        title = " vs ".join(
            col.replace("_", " ").title() for col in val_cols[:3]
        )

    return {
        "applied": True,
        "details": {
            "type":            chart_type,
            "columns":         [_to_out(c, column_mapping) for c in all_involved],
            "category_column": _to_out(cat_col, column_mapping) if cat_col else None,
            "value_columns":   [_to_out(c, column_mapping) for c in val_cols],
            "title":           title,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.6  SPARKLINES
# ─────────────────────────────────────────────────────────────────────────────

# Sparkline types supported in Excel
_SPARKLINE_TYPES = ("line", "column", "win_loss")

_WIN_LOSS_KEYWORDS = ["win", "loss", "win_loss", "binary", "result", "pass_fail",
                       "flag", "hit", "miss", "positive", "negative"]
_LINE_SPARK_KEYWORDS = ["trend", "sparkline", "spark", "mini_chart"]
_COL_SPARK_KEYWORDS  = ["bar_spark", "column_spark", "minichart", "inline_bar"]


def _detect_sparkline_type(col: str, series: pd.Series) -> Optional[str]:
    """
    Infer the sparkline type from the column name and data shape.

    Win/loss : binary-valued column (0/1, -1/1, True/False)
    Line     : name hints or multi-value numeric sequence
    Column   : default for numeric sparkline columns
    """
    col_n = _norm_name(col)

    # Win/loss: binary values
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) > 0:
        unique_vals = set(numeric.unique())
        if unique_vals.issubset({-1, 0, 1}) or unique_vals.issubset({0, 1}):
            return "win_loss"
        if any(kw in col_n for kw in _WIN_LOSS_KEYWORDS):
            return "win_loss"

    if any(kw in col_n for kw in _LINE_SPARK_KEYWORDS):
        return "line"
    if any(kw in col_n for kw in _COL_SPARK_KEYWORDS):
        return "column"

    # Default for sparkline-named columns
    return "line"


def _find_source_columns(
    sparkline_col: str,
    output_df: pd.DataFrame,
    input_df: pd.DataFrame,
) -> List[str]:
    """
    Identify the source data columns a sparkline is likely rendering.

    Heuristics
    ----------
    1. New numeric columns in output that were not in input are likely
       period/series columns feeding the sparkline.
    2. Columns with period/time-like names adjacent to the sparkline column.
    3. Fallback: all numeric input columns.
    """
    col_n   = _norm_name(sparkline_col)
    in_cols = set(input_df.columns)

    # New numeric columns in output
    new_numeric = [
        c for c in output_df.columns
        if c not in in_cols
        and c != sparkline_col
        and _is_numeric(output_df[c])
    ]

    if new_numeric:
        return new_numeric

    # Adjacent period-named columns
    period_hints = ["jan", "feb", "mar", "apr", "may", "jun",
                    "jul", "aug", "sep", "oct", "nov", "dec",
                    "q1", "q2", "q3", "q4",
                    "month", "week", "day", "period", "year"]
    period_cols = [
        c for c in output_df.columns
        if c != sparkline_col and any(h in _norm_name(c) for h in period_hints)
        and _is_numeric(output_df[c])
    ]
    if period_cols:
        return period_cols

    # Fallback: all numeric input columns
    fallback = [
        c for c in input_df.columns
        if _is_numeric(input_df[c]) and c != sparkline_col
    ]
    return fallback[:12]  # cap at 12 (monthly data)


def build_sparklines_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Detect SPARKLINE columns in the output DataFrame.

    Detection paths
    ---------------
    Path A – Column name keywords: 'sparkline', 'spark', 'trend',
             'mini_chart', 'inline_chart', 'bar_spark', 'line_spark'.

    Path B – New columns whose data is a compact numeric sequence
             that mirrors a set of adjacent period columns in the output.

    Path C – Binary (0/1 or -1/0/1) new columns next to period data
             → 'win_loss' sparkline.

    Edge cases
    ----------
    - Sparkline columns are always new output columns (not in input)
    - Source columns identified by period name hints or new numeric cols
    - Win/loss sparklines for binary outcome sequences
    - Line sparklines for continuous trend data
    - Column sparklines as default for bar-like sequences
    - column_mapping resolved before detection

    Returns
    -------
    {
      "applied": bool,
      "details": {
        "rules": [
          {
            "column":         str,   # sparkline display column (output namespace)
            "type":           str,   # "line" | "column" | "win_loss"
            "source_columns": [str]  # source data columns
          }
        ]
      }
    }
    """
    empty: Dict[str, Any] = {"applied": False, "details": {"rules": []}}

    if output_df.empty:
        return empty

    out_df  = _remap_output(output_df, column_mapping)
    in_cols = set(input_df.columns)
    rules: List[Dict[str, Any]] = []

    for col in out_df.columns:
        # Sparklines are new columns (not present in input)
        if col in in_cols:
            continue

        out_s = out_df[col]

        # Path A: keyword detection
        is_keyword_match = _has_keyword(col, _SPARKLINE_KEYWORDS)

        # Path B/C: binary or compact numeric sequence
        spark_type = _detect_sparkline_type(col, out_s)
        is_binary  = spark_type == "win_loss"

        if not is_keyword_match and not is_binary:
            # Only flag numeric columns that look like compact sequences
            if not _is_numeric(out_s):
                continue
            # Must have low cardinality relative to row count (compact sequence)
            non_null = out_s.dropna()
            if len(non_null) == 0:
                continue
            n_unique = non_null.nunique()
            # If it looks like a plain derived metric, skip
            if n_unique > min(20, len(non_null) * 0.8) and not is_keyword_match:
                continue

        if spark_type is None:
            spark_type = "line"

        source_cols = _find_source_columns(col, out_df, input_df)

        rules.append({
            "column":         _to_out(col, column_mapping),
            "type":           spark_type,
            "source_columns": [_to_out(c, column_mapping) for c in source_cols],
        })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
