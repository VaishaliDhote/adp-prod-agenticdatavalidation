# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (numeric formats, task 12). REUSABLE.
# ============================================================================
import re
import pandas as pd

def detect_number_format(series: pd.Series) -> dict | None:
    """
    Detect number format from a series of numeric strings
    Supported:
        10000.00    → No separator, dot decimal
        10 000,00   → Space thousand, comma decimal
        10,000.00   → Comma thousand, dot decimal
        10.000,00   → Dot thousand, comma decimal
        10'000,00   → Apostrophe thousand, comma decimal
        10000,00    → No separator, comma decimal
        10000       → Plain integer
    """

    number_formats = {
        r"^\-?\d+\.\d+$": {
            "numberFormat": "10000.00",
            "thousandSeparator": None,
            "decimalSeparator": ".",
            "hasDecimal": True
        },
        r"^\-?\d{1,3}(\s\d{3})*,\d+$": {
            "numberFormat": "10 000,00",
            "thousandSeparator": " ",
            "decimalSeparator": ",",
            "hasDecimal": True
        },
        r"^\-?\d{1,3}(,\d{3})*\.\d+$": {
            "numberFormat": "10,000.00",
            "thousandSeparator": ",",
            "decimalSeparator": ".",
            "hasDecimal": True
        },
        r"^\-?\d{1,3}(\.\d{3})*,\d+$": {
            "numberFormat": "10.000,00",
            "thousandSeparator": ".",
            "decimalSeparator": ",",
            "hasDecimal": True
        },
        r"^\-?\d{1,3}('\d{3})*,\d+$": {
            "numberFormat": "10'000,00",
            "thousandSeparator": "'",
            "decimalSeparator": ",",
            "hasDecimal": True
        },
        r"^\-?\d+,\d+$": {
            "numberFormat": "10000,00",
            "thousandSeparator": None,
            "decimalSeparator": ",",
            "hasDecimal": True
        },
        r"^\-?\d+$": {
            "numberFormat": "10000",
            "thousandSeparator": None,
            "decimalSeparator": None,
            "hasDecimal": False
        },
    }

    sample = series.dropna().astype(str).head(20)

    for pattern, format_info in number_formats.items():
        try:
            match_count = sum(
                1 for val in sample
                if re.match(pattern, val.strip())
            )
            if match_count == len(sample):
                return format_info
        except Exception:
            continue

    return None


def normalize_number(value: str, format_info: dict) -> float | None:
    """Normalize a number string to float based on detected format"""
    if format_info is None:
        return value

    val = str(value).strip()

    if format_info["thousandSeparator"]:
        val = val.replace(format_info["thousandSeparator"], "")

    if format_info["decimalSeparator"] and format_info["decimalSeparator"] != ".":
        val = val.replace(format_info["decimalSeparator"], ".")

    try:
        return float(val)
    except ValueError:
        return None