# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Identification (semantic-aware rule tuning). REUSABLE.
# ============================================================================
import re
import pandas as pd

# ---------------------------------------------------------------------------
# Semantic type constants
# ---------------------------------------------------------------------------
PHONE       = "PHONE"
EMAIL       = "EMAIL"
NAME        = "NAME"
IDENTIFIER  = "IDENTIFIER"
ADDRESS     = "ADDRESS"
DESCRIPTION = "DESCRIPTION"
AGE         = "AGE"
PERCENTAGE  = "PERCENTAGE"
CURRENCY    = "CURRENCY"
QUANTITY    = "QUANTITY"
FLAG        = "FLAG"
DATE        = "DATE"
BOOLEAN     = "BOOLEAN"
GENERIC     = "GENERIC"

# ---------------------------------------------------------------------------
# Keyword regexes (match against column name)
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(
    r"\b(phone|mobile|cell|fax|contact|tel|telephone|whatsapp|sms|msisdn|"
    r"ph[_\s]?no|mob[_\s]?no|contact[_\s]?no)\b", re.IGNORECASE)

_EMAIL_RE = re.compile(
    r"\b(email|e[_\-\s]?mail|mail[_\s]?id|emailid)\b", re.IGNORECASE)

_NAME_RE = re.compile(
    r"\b(first[_\s]?name|last[_\s]?name|full[_\s]?name|middle[_\s]?name|"
    r"employee[_\s]?name|customer[_\s]?name|client[_\s]?name|vendor[_\s]?name|"
    r"supplier[_\s]?name|contact[_\s]?name|company[_\s]?name|firm[_\s]?name|"
    r"employer[_\s]?name|manager[_\s]?name|person[_\s]?name|salesperson[_\s]?name|"
    r"product[_\s]?name|item[_\s]?name|brand[_\s]?name)\b", re.IGNORECASE)

# standalone "name" only when it IS the full column name or ends with it
_NAME_SUFFIX_RE = re.compile(r"(^name$|_name$|[_\s]name$)", re.IGNORECASE)

_IDENTIFIER_RE = re.compile(
    r"\b(emp[_\s]?id|employee[_\s]?id|customer[_\s]?id|order[_\s]?id|"
    r"invoice[_\s]?id|account[_\s]?no|acct|serial|sku|barcode|pan|"
    r"passport|license|licence|aadhar|ssn|tax[_\s]?id|ifsc|swift|iban|"
    r"upc|isbn|uuid|guid|token|zip|postal[_\s]?code)\b", re.IGNORECASE)

# standalone id/code/ref/no only when used as whole word
_ID_SUFFIX_RE = re.compile(r"(^id$|_id$|[_\s]id$|^code$|_code$|^ref$|_ref$|^no$|_no$|^num$|_num$|^number$|_number$)", re.IGNORECASE)

_ADDRESS_RE = re.compile(
    r"\b(address|addr|street|city|state|country|location|area|district|"
    r"region|province|landmark|locality|suburb|town|village|ward|"
    r"billing[_\s]?address|shipping[_\s]?address|mailing[_\s]?address)\b", re.IGNORECASE)

_DESCRIPTION_RE = re.compile(
    r"\b(description|desc|notes?|comments?|remarks?|details?|narrative|"
    r"summary|memo|feedback|review|message|info|information|reason|"
    r"explanation|observation)\b", re.IGNORECASE)

_AGE_RE = re.compile(r"\b(age|age[_\s]?years?|years?[_\s]?old)\b", re.IGNORECASE)

_PERCENTAGE_RE = re.compile(
    r"\b(percent|percentage|pct|discount[_\s]?pct|tax[_\s]?rate|"
    r"interest[_\s]?rate|commission[_\s]?pct|utilization|fill[_\s]?rate|"
    r"growth[_\s]?rate|margin[_\s]?pct)\b", re.IGNORECASE)

# "rate" and "ratio" only as standalone words (not part of longer words)
_RATE_RE = re.compile(r"(^rate$|_rate$|^ratio$|_ratio$)", re.IGNORECASE)

_CURRENCY_RE = re.compile(
    r"\b(salary|salaries|wage|pay|compensation|amount|price|cost|revenue|"
    r"income|profit|loss|budget|expense|fee|charge|value|total|subtotal|"
    r"tax[_\s]?amount|invoice[_\s]?amount|payment|credit|debit|balance|"
    r"gross|net|ctc|remuneration|earnings?|spend|expenditure|turnover|"
    r"proceeds?|disbursement)\b", re.IGNORECASE)

_QUANTITY_RE = re.compile(
    r"\b(qty|quantity|count|units?|pieces?|stock|inventory|volume|"
    r"headcount|seats?|order[_\s]?qty|shipped[_\s]?qty)\b", re.IGNORECASE)

_NO_OF_RE = re.compile(r"(^no[_\s]?of|^num[_\s]?of|^total[_\s]?count)", re.IGNORECASE)

_FLAG_RE = re.compile(
    r"(^flag$|_flag$|^is_|^has_|^indicator$|_indicator$|"
    r"^active$|^enabled$|^disabled$|^approved$|^rejected$)", re.IGNORECASE)

# phone-value heuristic
_PHONE_VAL_RE = re.compile(r"^\+?[\d\s\-\(\)]{7,15}$")


def _looks_like_phone_values(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).head(50)
    if len(sample) == 0:
        return False
    hits = sample.apply(lambda v: bool(_PHONE_VAL_RE.match(v.strip()))).sum()
    return (hits / len(sample)) >= 0.80


def detect_column_semantics(column: str, series: pd.Series, data_type: str) -> str:
    col = column.strip()

    if data_type == "Boolean":
        return BOOLEAN
    if data_type in ("Date", "DateTime"):
        return DATE

    if _EMAIL_RE.search(col):
        return EMAIL
    if _PHONE_RE.search(col):
        return PHONE
    if _NAME_RE.search(col) or _NAME_SUFFIX_RE.search(col):
        return NAME
    if _ADDRESS_RE.search(col):
        return ADDRESS
    if _DESCRIPTION_RE.search(col):
        return DESCRIPTION
    if _IDENTIFIER_RE.search(col) or _ID_SUFFIX_RE.search(col):
        return IDENTIFIER

    if data_type == "Numeric":
        if _AGE_RE.search(col):
            return AGE
        if _PERCENTAGE_RE.search(col) or _RATE_RE.search(col):
            return PERCENTAGE
        if _CURRENCY_RE.search(col):
            return CURRENCY
        if _QUANTITY_RE.search(col) or _NO_OF_RE.search(col):
            return QUANTITY

    if _FLAG_RE.search(col):
        return FLAG

    if data_type in ("String", "Numeric") and _looks_like_phone_values(series):
        return PHONE

    return GENERIC


# ---------------------------------------------------------------------------
# Semantic policy
# ---------------------------------------------------------------------------
class SemanticPolicy:
    __slots__ = (
        "include_min_max",
        "include_length",
        "include_lov",
        "include_uniqueness",
        "include_string_rule",
        "include_special_chars",
    )

    def __init__(self, **kwargs):
        self.include_min_max       = True
        self.include_length        = True
        self.include_lov           = True
        self.include_uniqueness    = True
        self.include_string_rule   = True
        self.include_special_chars = True
        for k, v in kwargs.items():
            setattr(self, k, v)


SEMANTIC_POLICIES = {
    PHONE: SemanticPolicy(
        include_min_max=False, include_length=False,
        include_lov=False,     include_uniqueness=True,
        include_string_rule=True, include_special_chars=False),
    EMAIL: SemanticPolicy(
        include_min_max=False, include_length=True,
        include_lov=False,     include_uniqueness=True,
        include_string_rule=True, include_special_chars=False),
    NAME: SemanticPolicy(
        include_min_max=False, include_length=True,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    IDENTIFIER: SemanticPolicy(
        include_min_max=False, include_length=True,
        include_lov=False,     include_uniqueness=True,
        include_string_rule=True, include_special_chars=True),
    ADDRESS: SemanticPolicy(
        include_min_max=False, include_length=True,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    DESCRIPTION: SemanticPolicy(
        include_min_max=False, include_length=True,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    AGE: SemanticPolicy(
        include_min_max=True,  include_length=False,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    PERCENTAGE: SemanticPolicy(
        include_min_max=True,  include_length=False,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    CURRENCY: SemanticPolicy(
        include_min_max=True,  include_length=False,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    QUANTITY: SemanticPolicy(
        include_min_max=True,  include_length=False,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    FLAG: SemanticPolicy(
        include_min_max=False, include_length=False,
        include_lov=True,      include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    DATE: SemanticPolicy(
        include_min_max=True,  include_length=False,
        include_lov=False,     include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    BOOLEAN: SemanticPolicy(
        include_min_max=False, include_length=False,
        include_lov=True,      include_uniqueness=False,
        include_string_rule=False, include_special_chars=False),
    GENERIC: SemanticPolicy(),
}


def get_policy(semantic_type: str) -> SemanticPolicy:
    return SEMANTIC_POLICIES.get(semantic_type, SEMANTIC_POLICIES[GENERIC])