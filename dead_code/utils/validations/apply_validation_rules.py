"""
apply_validation_rules.py
─────────────────────────
Row-level enforcement of derived validation rules against a real DataFrame.

For every (row, column) cell the checker runs each applicable rule in order:

  1. Mandatory / empty check        – always first; if the cell IS empty and
                                      isMandatory == "Y", record an error and
                                      SKIP all further checks for that cell
                                      (no point checking data-type on a blank).
  2. Data-type check                – value must be parseable as the declared type.
  3. Date / number format           – value must match the detected format.
  4. Min / max range                – numeric or date value must be in range.
  5. String length                  – string length must be within [min, max].
  6. LOV (List-of-Values)           – value must be in the allowed set.
  7. Special-character block list   – value must not contain blocked chars.
  8. Regex / string rule            – value must match the pattern.
  9. Uniqueness                     – every non-null value must be unique
                                      across the whole column (column-level check).

Returns
-------
{
  "hasErrors"  : bool,
  "errorCount" : int,
  "errors"     : [
    {
      "row"       : int,   # 1-based row number (excluding header)
      "column"    : str,
      "value"     : any,   # raw cell value (None / NaN shown as None)
      "errorType" : str,   # e.g. "MISSING_MANDATORY", "INVALID_DATATYPE", …
      "message"   : str,   # human-readable description
    },
    …
  ],
  "columnSummary": {
    "<column>": {"errorCount": int, "errorTypes": [str, …]},
    …
  }
}
"""

from __future__ import annotations

import re
import pandas as pd
from datetime import datetime
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_empty(value: Any) -> bool:
    """Return True for None, NaN, or blank/whitespace-only strings."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    # pandas NA
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return False


def _to_numeric(value: Any):
    """Try to coerce value to float. Returns None on failure."""
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_date(value: Any, fmt: str | None = None):
    """
    Try to coerce value to a datetime. Returns None on failure.
    
    If the value is already a datetime/Timestamp (common when pandas reads
    Excel date columns), accept it directly without re-parsing through strptime.
    """
    # Already a datetime object (pandas Timestamp, Python datetime, numpy datetime64)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value
    try:
        # numpy datetime64 check
        if hasattr(value, 'astype') and 'datetime64' in str(type(value)):
            return pd.Timestamp(value)
    except Exception:
        pass

    # String path: try to parse
    try:
        s = str(value).strip()
        if fmt:
            # Convert display format (dd-MMM-yyyy) to Python strptime format (%d-%b-%Y)
            py_fmt = _display_to_strptime(fmt)
            if py_fmt:
                return datetime.strptime(s, py_fmt)
        # Fallback: let pandas try to parse
        return pd.to_datetime(s, errors="raise")
    except Exception:
        return None


# Display format → Python strptime format mapping
_FORMAT_MAP = {
    "dd-MMM-yyyy":       "%d-%b-%Y",
    "dd MMM yyyy":       "%d %b %Y",
    "dd-MMM-yy":         "%d-%b-%y",
    "yyyy-MM-dd":        "%Y-%m-%d",
    "dd/MM/yyyy":        "%d/%m/%Y",
    "MM/dd/yyyy":        "%m/%d/%Y",
    "dd-MM-yyyy":        "%d-%m-%Y",
    "MM-dd-yyyy":        "%m-%d-%Y",
    "dd.MM.yyyy":        "%d.%m.%Y",
    "MM.dd.yyyy":        "%m.%d.%Y",
    "yyyy/MM/dd":        "%Y/%m/%d",
    "yyyy.MM.dd":        "%Y.%m.%d",
    "dd/MM/yy":          "%d/%m/%y",
    "MM/dd/yy":          "%m/%d/%y",
    "dd-MM-yy":          "%d-%m-%y",
    "MM-dd-yy":          "%m-%d-%y",
    "dd.MM.yy":          "%d.%m.%y",
    "MM.dd.yy":          "%m.%d.%y",
    "yy-MM-dd":          "%y-%m-%d",
    "yy/MM/dd":          "%y/%m/%d",
    "yy.MM.dd":          "%y.%m.%d",
    "YYYY":              "%Y",
    "yyyy-MM-dd HH:mm:ss": "%Y-%m-%d %H:%M:%S",
    "yyyy-MM-dd HH:mm":    "%Y-%m-%d %H:%M",
    "dd/MM/yyyy HH:mm:ss": "%d/%m/%Y %H:%M:%S",
    "dd/MM/yyyy HH:mm":    "%d/%m/%Y %H:%M",
    "MM/dd/yyyy HH:mm:ss": "%m/%d/%Y %H:%M:%S",
    "MM/dd/yyyy HH:mm":    "%m/%d/%Y %H:%M",
    "dd-MM-yyyy HH:mm:ss": "%d-%m-%Y %H:%M:%S",
    "dd-MM-yyyy HH:mm":    "%d-%m-%Y %H:%M",
}


def _display_to_strptime(display_fmt: str) -> str | None:
    """Convert display format like 'dd-MMM-yyyy' to Python strptime format '%d-%b-%Y'."""
    return _FORMAT_MAP.get(display_fmt)


# ─────────────────────────────────────────────────────────────────────────────
# Core cell-level checker
# ─────────────────────────────────────────────────────────────────────────────

def _check_cell(
    row_idx: int,         # 1-based
    column: str,
    raw_value: Any,
    rule: dict,
) -> list[dict]:
    """
    Run all applicable checks for one cell.

    Returns a list of error dicts (empty → no errors).
    Stops after a MISSING_MANDATORY error so no noise is produced for empty
    cells that fail every subsequent check.
    """
    errors: list[dict] = []

    def err(error_type: str, message: str):
        errors.append({
            "row":       row_idx,
            "column":    column,
            "value":     None if _is_empty(raw_value) else raw_value,
            "errorType": error_type,
            "message":   message,
        })

    # ── 1. Mandatory / empty ─────────────────────────────────────────────────
    cell_is_empty = _is_empty(raw_value)
    if cell_is_empty:
        if rule.get("isMandatory") == "Y":
            err(
                "MISSING_MANDATORY",
                f"Column '{column}' is mandatory but row {row_idx} has an empty value.",
            )
        # Either way: nothing more to check on an empty cell
        return errors

    value_str = str(raw_value).strip()

    # ── 2. Data-type check ───────────────────────────────────────────────────
    declared_type = rule.get("dataType", "String")

    if declared_type == "Numeric":
        if _to_numeric(raw_value) is None:
            err(
                "INVALID_DATATYPE",
                f"Column '{column}' expects Numeric but got '{value_str}' at row {row_idx}.",
            )
            return errors  # range / format checks would be meaningless

    elif declared_type in ("Date", "DateTime"):
        date_fmt = rule.get("dateFormat")
        parsed_date = _to_date(raw_value, date_fmt)
        if parsed_date is None:
            fmt_hint = f" (expected format: {date_fmt})" if date_fmt else ""
            err(
                "INVALID_DATE_FORMAT",
                f"Column '{column}' expects {declared_type}{fmt_hint} "
                f"but got '{value_str}' at row {row_idx}.",
            )
            return errors

    elif declared_type == "Boolean":
        if value_str.lower() not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
            err(
                "INVALID_BOOLEAN",
                f"Column '{column}' expects Boolean but got '{value_str}' at row {row_idx}.",
            )
            return errors

    # ── 3. Min / Max range ───────────────────────────────────────────────────
    min_val = rule.get("minValue")
    max_val = rule.get("maxValue")

    if declared_type == "Numeric" and (min_val is not None or max_val is not None):
        num = _to_numeric(raw_value)
        if num is not None:
            if min_val is not None:
                try:
                    if num < float(min_val):
                        err(
                            "BELOW_MIN_VALUE",
                            f"Column '{column}' value {num} is below the minimum "
                            f"{min_val} at row {row_idx}.",
                        )
                except (ValueError, TypeError):
                    pass
            if max_val is not None:
                try:
                    if num > float(max_val):
                        err(
                            "ABOVE_MAX_VALUE",
                            f"Column '{column}' value {num} is above the maximum "
                            f"{max_val} at row {row_idx}.",
                        )
                except (ValueError, TypeError):
                    pass

    elif declared_type in ("Date", "DateTime") and (min_val is not None or max_val is not None):
        date_fmt = rule.get("dateFormat")
        d = _to_date(raw_value, date_fmt)
        if d is not None:
            if min_val is not None:
                d_min = _to_date(min_val, date_fmt)
                if d_min and d < d_min:
                    err(
                        "BELOW_MIN_DATE",
                        f"Column '{column}' date {value_str} is before the minimum "
                        f"{min_val} at row {row_idx}.",
                    )
            if max_val is not None:
                d_max = _to_date(max_val, date_fmt)
                if d_max and d > d_max:
                    err(
                        "ABOVE_MAX_DATE",
                        f"Column '{column}' date {value_str} is after the maximum "
                        f"{max_val} at row {row_idx}.",
                    )

    # ── 4. String length ─────────────────────────────────────────────────────
    min_len = rule.get("minLengthAllowed")
    max_len = rule.get("maxLengthAllowed")

    if min_len is not None or max_len is not None:
        vlen = len(value_str)
        if min_len is not None and vlen < int(min_len):
            err(
                "BELOW_MIN_LENGTH",
                f"Column '{column}' value '{value_str}' length {vlen} is below "
                f"minimum {min_len} at row {row_idx}.",
            )
        if max_len is not None and vlen > int(max_len):
            err(
                "ABOVE_MAX_LENGTH",
                f"Column '{column}' value '{value_str}' length {vlen} exceeds "
                f"maximum {max_len} at row {row_idx}.",
            )

    # ── 5. LOV check ─────────────────────────────────────────────────────────
    if rule.get("lovValidationRequired") == "Y":
        allowed = rule.get("allowedValues") or []
        # normalise to strings for comparison
        allowed_str = {str(v).strip() for v in allowed}
        if value_str not in allowed_str:
            err(
                "INVALID_LOV_VALUE",
                f"Column '{column}' value '{value_str}' is not in the allowed list "
                f"{sorted(allowed_str)} at row {row_idx}.",
            )

    # ── 6. Special-character block list ─────────────────────────────────────
    blocked_chars = rule.get("specialCharNotAllowed")
    if blocked_chars:
        if isinstance(blocked_chars, (list, tuple)):
            found = [c for c in blocked_chars if c in value_str]
        elif isinstance(blocked_chars, str):
            found = [c for c in blocked_chars if c in value_str]
        else:
            found = []
        if found:
            err(
                "BLOCKED_SPECIAL_CHAR",
                f"Column '{column}' value '{value_str}' contains blocked character(s) "
                f"{found} at row {row_idx}.",
            )

    # ── 7. Regex / string-rule check ─────────────────────────────────────────
    regex = rule.get("regex")
    if regex and regex not in (".*", ""):
        try:
            if not re.fullmatch(regex, value_str):
                string_rule_label = rule.get("stringCheckRule") or "pattern"
                err(
                    "REGEX_MISMATCH",
                    f"Column '{column}' value '{value_str}' does not match the "
                    f"expected {string_rule_label} '{regex}' at row {row_idx}.",
                )
        except re.error:
            pass  # bad regex in rule — skip silently

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Column-level uniqueness check
# ─────────────────────────────────────────────────────────────────────────────

def _check_uniqueness(column: str, series: pd.Series) -> list[dict]:
    """Return errors for every duplicate non-null value in the column."""
    errors: list[dict] = []
    # map value → list of 1-based row numbers
    seen: dict[str, list[int]] = {}
    for zero_idx, raw in enumerate(series):
        if _is_empty(raw):
            continue
        key = str(raw).strip()
        seen.setdefault(key, []).append(zero_idx + 1)

    for value, rows in seen.items():
        if len(rows) > 1:
            for r in rows:
                errors.append({
                    "row":       r,
                    "column":    column,
                    "value":     value,
                    "errorType": "DUPLICATE_VALUE",
                    "message":   (
                        f"Column '{column}' requires unique values but '{value}' "
                        f"appears at rows {rows}."
                    ),
                })
    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def apply_validation_rules(
    df: pd.DataFrame,
    rules: dict,                       # column_name → rule dict (from derive_validation_rules)
    *,
    skip_columns: list[str] | None = None,
) -> dict:
    """
    Validate every cell in *df* against the derived *rules*.

    Parameters
    ----------
    df            : Input DataFrame to validate.
    rules         : Dict of {column_name: rule_dict} from derive_validation_rules().
    skip_columns  : Optional list of column names to exclude from validation.

    Returns
    -------
    A result dict — see module docstring for full schema.
    """
    skip = set(skip_columns or [])
    all_errors: list[dict] = []

    for column, rule in rules.items():
        if column not in df.columns or column in skip:
            continue

        series = df[column]

        # Normalise blank strings to NaN so _is_empty() works uniformly
        series = series.replace(r"^\s*$", pd.NA, regex=True)

        # ── Per-cell checks ──────────────────────────────────────────────────
        for zero_idx, raw_value in enumerate(series):
            row_num = zero_idx + 1   # 1-based for readability
            cell_errors = _check_cell(row_num, column, raw_value, rule)
            all_errors.extend(cell_errors)

        # ── Column-level uniqueness check ────────────────────────────────────
        if rule.get("uniqueValueInColumn") == "Y":
            all_errors.extend(_check_uniqueness(column, series))

    # ── Build column summary ─────────────────────────────────────────────────
    column_summary: dict[str, dict] = {}
    for e in all_errors:
        col = e["column"]
        if col not in column_summary:
            column_summary[col] = {"errorCount": 0, "errorTypes": []}
        column_summary[col]["errorCount"] += 1
        if e["errorType"] not in column_summary[col]["errorTypes"]:
            column_summary[col]["errorTypes"].append(e["errorType"])

    return {
        "hasErrors":     len(all_errors) > 0,
        "errorCount":    len(all_errors),
        "errors":        all_errors,
        "columnSummary": column_summary,
    }
