# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (date formats, task 11). REUSABLE.
# ============================================================================
import pandas as pd
from datetime import datetime


DATE_FORMATS = [
    # Month abbreviation — unambiguous
    ("%d-%b-%Y", "dd-MMM-yyyy"),
    ("%d %b %Y", "dd MMM yyyy"),
    ("%d-%b-%y", "dd-MMM-yy"),

    # 4-digit year with separator
    ("%Y-%m-%d", "yyyy-MM-dd"),   # ISO — checked before ambiguous d/m vs m/d
    ("%d/%m/%Y", "dd/MM/yyyy"),
    ("%m/%d/%Y", "MM/dd/yyyy"),
    ("%d-%m-%Y", "dd-MM-yyyy"),
    ("%m-%d-%Y", "MM-dd-yyyy"),
    ("%d.%m.%Y", "dd.MM.yyyy"),
    ("%m.%d.%Y", "MM.dd.yyyy"),
    ("%Y/%m/%d", "yyyy/MM/dd"),
    ("%Y.%m.%d", "yyyy.MM.dd"),

    # 2-digit year with separator
    ("%d/%m/%y", "dd/MM/yy"),
    ("%m/%d/%y", "MM/dd/yy"),
    ("%d-%m-%y", "dd-MM-yy"),
    ("%m-%d-%y", "MM-dd-yy"),
    ("%d.%m.%y", "dd.MM.yy"),
    ("%m.%d.%y", "MM.dd.yy"),
    ("%y-%m-%d", "yy-MM-dd"),
    ("%y/%m/%d", "yy/MM/dd"),
    ("%y.%m.%d", "yy.MM.dd"),

    # Year only
    ("%Y",       "YYYY"),
]

# --- DateTime candidate formats (date + time) ---
DATETIME_FORMATS = [
    ("%Y-%m-%d %H:%M:%S",   "yyyy-MM-dd HH:mm:ss"),
    ("%Y-%m-%d %H:%M",      "yyyy-MM-dd HH:mm"),
    ("%d/%m/%Y %H:%M:%S",   "dd/MM/yyyy HH:mm:ss"),
    ("%d/%m/%Y %H:%M",      "dd/MM/yyyy HH:mm"),
    ("%m/%d/%Y %H:%M:%S",   "MM/dd/yyyy HH:mm:ss"),
    ("%m/%d/%Y %H:%M",      "MM/dd/yyyy HH:mm"),
    ("%d-%m-%Y %H:%M:%S",   "dd-MM-yyyy HH:mm:ss"),
    ("%d-%m-%Y %H:%M",      "dd-MM-yyyy HH:mm"),
]


def _has_day_over_12(series: pd.Series) -> bool:
    """True if any day value in the series exceeds 12 (rules out month-first formats)."""
    return bool((series.dt.day > 12).any())


def _match_format_string(sample: pd.Series) -> str | None:
    """Match raw string values against DATE_FORMATS. Returns display format or None."""
    for fmt, display_fmt in DATE_FORMATS:
        try:
            matched = 0
            for val in sample:
                datetime.strptime(val.strip(), fmt)
                matched += 1
            if matched == len(sample):
                return display_fmt
        except ValueError:
            continue
    return None


def detect_date_format(series: pd.Series) -> str | None:
    """
    Detect the date/datetime format of a column.
    Returns a display format string like 'dd/MM/yyyy' or 'yyyy-MM-dd HH:mm:ss', or None.

    Handles three cases:
      1. datetime64 with time (DateTime column) — checks DATETIME_FORMATS
      2. datetime64 date-only (Date column)     — disambiguates d/m vs m/d using day values
      3. Raw string column                      — tries each format via strptime
    """
    non_null = series.dropna()
    if non_null.empty:
        return None

    # ── Case 1 & 2: pandas-parsed datetime64 ──────────────────────────────────
    if pd.api.types.is_datetime64_any_dtype(non_null):
        has_time = (
            (non_null.dt.hour != 0) |
            (non_null.dt.minute != 0) |
            (non_null.dt.second != 0)
        ).any()

        if has_time:
            # DateTime column — pick the first DATETIME_FORMAT that round-trips
            for fmt, display_fmt in DATETIME_FORMATS:
                try:
                    as_strings = non_null.dt.strftime(fmt).head(20)
                    if all(datetime.strptime(v, fmt) for v in as_strings):
                        return display_fmt
                except Exception:
                    continue
            return None

        # Date-only column — disambiguate ambiguous formats using day values
        # If any day > 12, month-first formats (MM/dd, MM-dd) are impossible
        day_over_12 = _has_day_over_12(non_null)

        for fmt, display_fmt in DATE_FORMATS:
            # Skip month-first formats if any day > 12
            if day_over_12 and fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y",
                                        "%m/%d/%y", "%m-%d-%y", "%m.%d.%y"):
                continue
            try:
                as_strings = non_null.dt.strftime(fmt).head(20)
                if all(datetime.strptime(v, fmt) for v in as_strings):
                    return display_fmt
            except Exception:
                continue
        return None

    # ── Case 3: Raw string column ──────────────────────────────────────────────
    # Check DateTime formats first (strings may include time)
    sample = non_null.astype(str).head(20)
    for fmt, display_fmt in DATETIME_FORMATS + DATE_FORMATS:
        try:
            matched = 0
            for val in sample:
                datetime.strptime(val.strip(), fmt)
                matched += 1
            if matched == len(sample):
                return display_fmt
        except ValueError:
            continue
    return None