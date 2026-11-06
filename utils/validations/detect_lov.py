# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (allowed values / LOV, task 14). REUSABLE.
# ============================================================================
import pandas as pd

# Maximum number of distinct values a column can have to still be
# treated as a controlled list (LOV).
# e.g. Country (<=250), Status (<=10), Tax Category (<=20) -> LOV
# e.g. Employee Name (50+ unique names) -> NOT LOV
MAX_LOV_CARDINALITY = 30

# Values whose average character length exceeds this are free-text, not LOV.
# e.g. "United States" (13 chars) -> fine
#      "John Michael Smith" (18 chars) -> free-text, skip LOV
MAX_LOV_AVG_VALUE_LENGTH = 15


def detect_lov(series: pd.Series, threshold: float = 0.3) -> dict | None:
    """
    Detect if a column is a List of Values (LOV).

    A column is treated as an LOV only when ALL of the following hold:
      1. unique_ratio       <= threshold          (default 30%)
      2. n_unique           <= MAX_LOV_CARDINALITY (default 30)
         -- prevents Employee Name with 40 unique values being flagged
      3. avg value length   <= MAX_LOV_AVG_VALUE_LENGTH (default 15 chars)
         -- prevents long free-text fields being treated as LOV

    Examples:
        Status       -> ["Active","Inactive"]           -> LOV
        Tax Category -> ["Labor","Material","Service"]  -> LOV
        Country      -> ["US","UK","AU",...]            -> LOV (if <= 30 unique)
        Employee Name-> ["John Smith","Jane Doe",...]   -> NOT LOV (too many/long)
        Description  -> free-text                      -> NOT LOV
    """
    non_null = series.dropna()

    if len(non_null) == 0:
        return None

    unique_vals  = non_null.unique()
    n_unique     = len(unique_vals)
    unique_ratio = n_unique / len(non_null)

    # Gate 1: ratio threshold
    if unique_ratio > threshold:
        return None

    # Gate 2: absolute cardinality -- stops name columns on large files
    if n_unique > MAX_LOV_CARDINALITY:
        return None

    # Gate 3: average value length -- stops free-text / long description columns
    avg_len = pd.Series(unique_vals).astype(str).str.len().mean()
    if avg_len > MAX_LOV_AVG_VALUE_LENGTH:
        return None

    return {
        "lovCheckType":          "static",
        "lovValidationRequired": "Y",
        "allowedValues":         unique_vals.tolist(),
    }
