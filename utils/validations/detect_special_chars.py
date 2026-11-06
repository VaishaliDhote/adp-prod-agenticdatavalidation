# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (length/special-char constraints,
# tasks 16 & 18). REUSABLE.
# ============================================================================
import re
import pandas as pd

def detect_special_chars(series: pd.Series) -> list | None:
    """Detect special characters present in column values"""
    special_chars = set()
    pattern = r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]'

    for val in series.dropna().astype(str):
        chars = re.findall(pattern, val)
        special_chars.update(chars)

    return list(special_chars) if special_chars else None