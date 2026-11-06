# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (data types, task 10). REUSABLE.
# ============================================================================
import pandas as pd

def detect_data_type(series: pd.Series) -> str:
    """
    Detect the data type of a column.
    Returns: 'Date' | 'Numeric' | 'Boolean' | 'String'

    Detection order:
      1. Empty / all-null  → 'String'
      2. Boolean           → 'Boolean'
      3. Numeric           → 'Numeric'  (strict: ALL non-null values must convert)
      4. Date              → 'Date'     (strict: ALL non-null values must convert,
      and values must look date-like, not plain integers)
      5. Fallback          → 'String'
    """
    non_null = series.dropna()

    # 1. Empty / all-null column
    if non_null.empty:
        return "String"

    # 2. Boolean check (before numeric, since bool is subclass of int in Python)
    if pd.api.types.is_bool_dtype(non_null):
        return "Boolean"
    str_lower = non_null.astype(str).str.strip().str.lower()
    if str_lower.isin({"true", "false"}).all():
        return "Boolean"

    # 3. Date / DateTime check — pandas already parsed this column from Excel
    if pd.api.types.is_datetime64_any_dtype(non_null):
        # If ANY value has a non-zero time component → it's a DateTime column
        has_time = (
            (non_null.dt.hour != 0) |
            (non_null.dt.minute != 0) |
            (non_null.dt.second != 0)
        ).any()
        return "DateTime" if has_time else "Date"

    # 4. Numeric check — all non-null values must be convertible
    numeric_converted = pd.to_numeric(non_null, errors="coerce")
    if numeric_converted.notna().all():
        return "Numeric"

    # 5. Date check (string path) — reject plain bare integers (e.g. 1, 99, 2020 alone)
    #    At least one value must contain a date separator or month name
    str_values = non_null.astype(str).str.strip()
    DATE_PATTERN = r"([-/.]|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)"
    has_date_pattern = str_values.str.contains(DATE_PATTERN, case=False, regex=True).any()
    if has_date_pattern:
        try:
            converted = pd.to_datetime(str_values, errors="coerce")
            if converted.notna().all():
                return "Date"
        except Exception:
            pass

    return "String"