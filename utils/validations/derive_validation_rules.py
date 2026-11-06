# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification. REUSABLE (core orchestrator).
# Rework: map derived rules to supported API validations (D3 tasks 19-21),
# assign source-column UUIDs (task 24), and align emitted field names with the
# exact JSON contract (Deliverable 2). This must remain identification only -
# no row-by-row enforcement (that engine was removed to dead_code/).
# ============================================================================
import pandas as pd
from utils.validations.detect_data_type import detect_data_type
from utils.validations.detect_date_format import detect_date_format
from utils.validations.detect_number_format import detect_number_format
from utils.validations.detect_string_pattern import detect_string_check_rule
from utils.validations.detect_special_chars import detect_special_chars
from utils.validations.detect_lov import detect_lov
from utils.validations.detect_column_semantics import detect_column_semantics, get_policy


def derive_column_validation_rules(column: str, series: pd.Series) -> dict:
    """
    Derive intelligent, context-aware validation rules for a single column.

    Rules are suppressed or adjusted based on the column's *semantic type*
    (inferred from the column name and data content) so that nonsensical rules
    are never generated.  Examples:
      - Phone numbers  → no min/max, no LOV, '+'/'-' not flagged as special chars
      - Employee names → no LOV, no derived regex, no min/max
      - Age / Salary   → min/max meaningful; LOV suppressed
      - Address/Notes  → fully free-text: no LOV, no regex, no special-char list
      - Status / Flag  → LOV is the primary check

    Empty-cell handling:
      - Cells that are NaN/None/blank strings are treated as missing values.
      - isMandatory is set to "Y" only when NO rows are empty.
      - If the ENTIRE column is empty, a safe default rule is returned immediately
        so that downstream detectors never receive an empty series and crash.
    """
    # Normalise blank strings ("", "  ") to NaN so they count as missing
    series = series.replace(r"^\s*$", pd.NA, regex=True)

    non_null = series.dropna()

    # ── Guard: column is completely empty ────────────────────────────────────
    if non_null.empty:
        return {
            "columnName":                   column,
            "dataType":                     "String",
            "semanticType":                 "GENERIC",
            "isMandatory":                  "N",
            "dateFormat":                   None,
            "numberFormat":                 None,
            "minValue":                     None,
            "maxValue":                     None,
            "minLengthAllowed":             None,
            "maxLengthAllowed":             None,
            "uniqueValueInColumn":          None,
            "specialCharNotAllowed":        None,
            "stringCheckRule":              None,
            "regex":                        None,
            "lovCheckType":                 None,
            "lovValidationRequired":        None,
            "allowedValues":                None,
            "transformationRequired":       None,
            "conditionalValidationRule":    None,
            "dataTransformationRules":      None,
            "specialCharToBeRemoved":       None,
            "dataExclusionRules":           None,
            "columnRequiredInErrorFile":    None,
            "validationRuleType":           "client",
            "_warning":                     "Column is entirely empty — no rules derived",
        }

    # ── Step 1: Detect fundamental data type ─────────────────────────────────
    data_type = detect_data_type(non_null)

    # ── Step 2: Detect semantic type from column name + data content ──────────
    semantic_type = detect_column_semantics(column, series, data_type)
    policy = get_policy(semantic_type)

    # ── Step 3: Conditional detections gated by semantic policy ──────────────

    # Date / DateTime format
    date_format = None
    if data_type in ("Date", "DateTime"):
        date_format = detect_date_format(non_null)

    # Numeric format
    number_format = None
    if data_type == "Numeric":
        number_format = detect_number_format(non_null)

    # Min / Max  — suppressed for PHONE, EMAIL, NAME, IDENTIFIER, ADDRESS, etc.
    min_value = None
    max_value = None
    if policy.include_min_max:
        if data_type in ("Numeric", "Date", "DateTime"):
            min_value = str(non_null.min())
            max_value = str(non_null.max())
        # Age: clamp to sensible human range (0–130)
        if semantic_type == "AGE":
            try:
                min_value = str(max(0, float(min_value)))
                max_value = str(min(130, float(max_value)))
            except (TypeError, ValueError):
                pass
        # Percentage: clamp to 0-1 or 0-100 based on observed scale
        if semantic_type == "PERCENTAGE":
            try:
                raw_max = float(max_value)
                if raw_max <= 1.0:
                    min_value = str(max(0.0, float(min_value)))
                    max_value = str(min(1.0, raw_max))
                else:
                    min_value = str(max(0.0, float(min_value)))
                    max_value = str(min(100.0, raw_max))
            except (TypeError, ValueError):
                pass
        # Quantity / Currency: floor at 0 unless data genuinely contains negatives
        if semantic_type in ("QUANTITY", "CURRENCY"):
            try:
                observed_min = float(min_value)
                if observed_min >= 0:
                    min_value = str(max(0.0, observed_min))
            except (TypeError, ValueError):
                pass

    # String length — only for String data type
    min_length = None
    max_length = None
    if policy.include_length and data_type == "String":
        lengths = non_null.astype(str).str.len()
        min_length = int(lengths.min())
        max_length = int(lengths.max())

    # LOV — suppressed for NAME, PHONE, EMAIL, ADDRESS, DESCRIPTION, numeric semantics
    lov_info = None
    if policy.include_lov:
        lov_info = detect_lov(series)

    # Special characters — suppressed for columns where those chars are valid
    special_chars = None
    if policy.include_special_chars and data_type == "String":
        special_chars = detect_special_chars(non_null)

    # String pattern / regex — suppressed for free-text columns
    string_rule = None
    if policy.include_string_rule and data_type == "String":
        string_rule = detect_string_check_rule(non_null)
        # Suppress noisy *derived* patterns for high-cardinality, long-value columns
        # (derived regex on free-text produces misleading validation rules)
        if string_rule and string_rule.get("source") == "derived_from_data":
            unique_ratio = non_null.nunique() / max(len(non_null), 1)
            avg_len = non_null.astype(str).str.len().mean()
            if unique_ratio > 0.5 and avg_len > 20:
                string_rule = {"stringCheckRule": "any", "regex": ".*", "source": "fallback"}

    # Uniqueness — suppressed for columns where duplication is expected (names, desc)
    unique_in_column = None
    if policy.include_uniqueness:
        unique_in_column = "Y" if non_null.nunique() == len(non_null) else "N"

    # ── Step 4: Build the final rule dict ─────────────────────────────────────
    return {
        # ── Identity ───────────────────────────────────────────────────────────
        "columnName":               column,
        "dataType":                 data_type,
        "semanticType":             semantic_type,

        # ── Presence ───────────────────────────────────────────────────────────
        "isMandatory":              "Y" if series.isnull().sum() == 0 else "N",

        # ── Format detection ───────────────────────────────────────────────────
        "dateFormat":               date_format,
        "numberFormat":             number_format,

        # ── Range validation (suppressed for identifiers, phones, names, etc.) ──
        "minValue":                 min_value,
        "maxValue":                 max_value,

        # ── String length ──────────────────────────────────────────────────────
        "minLengthAllowed":         min_length,
        "maxLengthAllowed":         max_length,

        # ── Uniqueness (suppressed for non-key columns) ────────────────────────
        "uniqueValueInColumn":      unique_in_column,

        # ── Special character list (suppressed where chars are valid) ──────────
        "specialCharNotAllowed":    special_chars,

        # ── Pattern / regex (suppressed for free-text columns) ─────────────────
        "stringCheckRule":          string_rule["stringCheckRule"] if string_rule else None,
        "regex":                    string_rule["regex"] if string_rule else None,

        # ── List of values (suppressed for names, phones, addresses, etc.) ──────
        "lovCheckType":             lov_info["lovCheckType"] if lov_info else None,
        "lovValidationRequired":    lov_info["lovValidationRequired"] if lov_info else None,
        "allowedValues":            lov_info["allowedValues"] if lov_info else None,

        # ── Fields requiring human review ──────────────────────────────────────
        "transformationRequired":       None,
        "conditionalValidationRule":    None,
        "dataTransformationRules":      None,
        "specialCharToBeRemoved":       None,
        "dataExclusionRules":           None,
        "columnRequiredInErrorFile":    None,
        "validationRuleType":           "client",
    }


def derive_validation_rules(df: pd.DataFrame) -> dict:
    """
    Derive validation rules for all columns in a DataFrame
    Returns dict of column_name → validation rules
    """
    validation_rules = {}

    for column in df.columns:
        print(f"  Processing column: {column}...")
        validation_rules[column] = derive_column_validation_rules(column, df[column])

    return validation_rules