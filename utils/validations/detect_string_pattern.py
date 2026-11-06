# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (string/pattern constraints,
# tasks 13 & 17). REUSABLE.
# ============================================================================
import re
import pandas as pd
from collections import Counter


def derive_regex_from_data(series: pd.Series) -> dict:
    """
    Derive a regex pattern by observing structure of values in a column
    Character mapping:
        Uppercase → [A-Z]
        Lowercase → [a-z]
        Digit     → \d
        Space     → \s
        Special   → escaped
    """
    non_null = series.dropna().astype(str)

    def value_to_pattern(val: str) -> str:
        pattern = ""
        i = 0
        while i < len(val):
            ch = val[i]
            if ch.isupper():
                j = i
                while j < len(val) and val[j].isupper():
                    j += 1
                count = j - i
                pattern += f"[A-Z]{{{count}}}" if count > 1 else "[A-Z]"
                i = j
            elif ch.islower():
                j = i
                while j < len(val) and val[j].islower():
                    j += 1
                count = j - i
                pattern += f"[a-z]{{{count}}}" if count > 1 else "[a-z]"
                i = j
            elif ch.isdigit():
                j = i
                while j < len(val) and val[j].isdigit():
                    j += 1
                count = j - i
                pattern += f"\\d{{{count}}}" if count > 1 else "\\d"
                i = j
            elif ch == " ":
                pattern += "\\s"
                i += 1
            else:
                pattern += re.escape(ch)
                i += 1
        return f"^{pattern}$"

    patterns = [value_to_pattern(val) for val in non_null]
    pattern_counts = Counter(patterns)
    most_common_pattern, count = pattern_counts.most_common(1)[0]
    coverage = count / len(non_null)

    return {
        "derivedRegex": most_common_pattern,
        "coverage": f"{coverage * 100:.1f}%",
        "totalValues": len(non_null),
        "matchedValues": count,
        "allPatterns": dict(pattern_counts)
    }


def detect_string_check_rule(series: pd.Series) -> dict:
    """
    Step 1: Match known patterns  → email, phone, url etc.
    Step 2: Derive regex from data → observe structure
    Step 3: Fallback               → .*
    """
    non_null = series.dropna().astype(str)

    # ── Known Patterns ───────────────────────────────────────────
    string_patterns = {
        "email":         r"^[a-zA-Z0-9_!#$%&'*+/=?`{|}~^-]+(?:\.[a-zA-Z0-9_!#$%&'*+/=?`{|}~^-]+)*@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
        "phone":         r"^\+?[\d\s\-]{7,15}$",
        "url":           r"^(https?:\/\/)?([\da-z\.-]+)\.([a-z\.]{2,6})([\/\w \.-]*)*\/?$",
        "ipAddress":     r"^(\d{1,3}\.){3}\d{1,3}$",
        "zipCode":       r"^\d{5}(-\d{4})?$",
        "panCard":       r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$",
        "iban":          r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$",
        "alphanumeric":  r"^[a-zA-Z0-9]+$",
        "numericOnly":   r"^\d+$",
        "alphabetsOnly": r"^[a-zA-Z]+$",
    }

    # Step 1: Known Patterns
    for pattern_name, pattern in string_patterns.items():
        try:
            match_count = sum(
                1 for val in non_null
                if re.match(pattern, val.strip())
            )
            if match_count == len(non_null):
                return {
                    "stringCheckRule": pattern_name,
                    "regex": pattern,
                    "source": "known_pattern"
                }
        except Exception:
            continue

    # Step 2: Derive Regex from Data
    derived = derive_regex_from_data(non_null)
    if derived and float(derived["coverage"].replace("%", "")) >= 80.0:
        return {
            "stringCheckRule": "derived",
            "regex": derived["derivedRegex"],
            "coverage": derived["coverage"],
            "source": "derived_from_data",
            "allPatterns": derived["allPatterns"]
        }

    # Step 3: Fallback → .*
    return {
        "stringCheckRule": "any",
        "regex": ".*",
        "source": "fallback"
    }