# ============================================================================
# Deliverable 1 - Data type & format detection (tasks 11-12). REUSABLE.
# Deterministic (non-LLM) inference of a column's logical data type and
# recognizable value-format pattern from its pandas Series. Fully implemented
# and reused by the Excel parser (and the future CSV parser).
# ============================================================================
"""
ingestion.type_detection
=========================
Deterministic column data-type and value-format detection.

The Excel parser calls :func:`detect_column_profile` per column to populate
the canonical column's ``data_type`` and ``detected_format`` fields. Detection
is intentionally rule-based (regex + pandas dtype inspection) so results are
reproducible and explainable, with no dependency on an LLM.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pandas.api import types as ptypes

from canonical.enums import DataType, DetectedFormat

# -- Value-format regex patterns -------------------------------------------
# Anchored patterns evaluated against stringified sample values.
_PATTERNS: list[tuple[DetectedFormat, re.Pattern[str]]] = [
    (DetectedFormat.DATETIME_ISO, re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")),
    (DetectedFormat.DATE_ISO, re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    # Compact 8-digit date (yyyyMMdd) - constrained to plausible 1900-2099
    # calendar values so bare numeric IDs are not misread as dates.
    (DetectedFormat.DATE_COMPACT, re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")),
    # US vs EU cannot always be disambiguated; treat >12 first component as EU.
    (DetectedFormat.DATE_US, re.compile(r"^(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/\d{4}$")),
    (DetectedFormat.DATE_EU, re.compile(r"^(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/\d{4}$")),
    (DetectedFormat.EMAIL, re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    (DetectedFormat.UUID, re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")),
    (DetectedFormat.CURRENCY, re.compile(r"^[\$\u00a3\u20ac]\s?-?\d{1,3}(,\d{3})*(\.\d+)?$")),
    (DetectedFormat.PERCENTAGE, re.compile(r"^-?\d+(\.\d+)?\s?%$")),
    (DetectedFormat.DECIMAL, re.compile(r"^-?\d{1,3}(,\d{3})*\.\d+$|^-?\d+\.\d+$")),
    (DetectedFormat.INTEGER, re.compile(r"^-?\d{1,3}(,\d{3})*$|^-?\d+$")),
]

_BOOLEAN_TOKENS = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}
_MAX_SAMPLE = 50


def _clean_samples(series: pd.Series) -> list[str]:
    """Return up to ``_MAX_SAMPLE`` non-null stringified sample values."""
    non_null = series.dropna()
    if non_null.empty:
        return []
    head = non_null.head(_MAX_SAMPLE)
    return [str(v).strip() for v in head.tolist() if str(v).strip() != ""]


def _distinct_with_counts(series: pd.Series) -> list[tuple[str, int]]:
    """Return ``(value, row_count)`` for every distinct value in the WHOLE column.

    Detection must reflect the entire column, not a head sample, otherwise an
    edge-case value in row 10,000 could violate a rule inferred from row 1. We
    scan *distinct* values (cheap) but keep their true row counts so majority
    thresholds remain exact over all rows - correct AND scalable to large files.
    """
    non_null = series.dropna()
    if non_null.empty:
        return []
    stripped = non_null.astype(str).str.strip()
    stripped = stripped[stripped != ""]
    if stripped.empty:
        return []
    counts = stripped.value_counts()
    return [(str(value), int(count)) for value, count in counts.items()]


def detect_data_type(series: pd.Series) -> DataType:
    """Infer the logical :class:`DataType` of a pandas Series (task 11).

    Uses the pandas dtype first (fast, reliable for typed columns) and otherwise
    inspects EVERY distinct value in the whole column (not a sample) so the
    inferred type is never wrong because a sample missed an edge case.
    """
    if ptypes.is_bool_dtype(series):
        return DataType.BOOLEAN
    if ptypes.is_integer_dtype(series):
        return DataType.INTEGER
    if ptypes.is_float_dtype(series):
        return DataType.DECIMAL
    if ptypes.is_datetime64_any_dtype(series):
        return DataType.DATETIME

    pairs = _distinct_with_counts(series)
    if not pairs:
        return DataType.UNKNOWN

    values = [value for value, _ in pairs]
    lowered = {value.lower() for value in values}
    if lowered <= _BOOLEAN_TOKENS:
        return DataType.BOOLEAN

    # Classify every DISTINCT value once; because each row's value is one of
    # these, an ``all(...)`` over the distinct set holds for the whole column.
    fmt_votes = [detect_format_for_value(value) for value in values]
    if fmt_votes and all(f in (DetectedFormat.DATE_ISO, DetectedFormat.DATE_US, DetectedFormat.DATE_EU, DetectedFormat.DATE_COMPACT) for f in fmt_votes):
        return DataType.DATE
    if fmt_votes and all(f is DetectedFormat.DATETIME_ISO for f in fmt_votes):
        return DataType.DATETIME
    if fmt_votes and all(f is DetectedFormat.INTEGER for f in fmt_votes):
        return DataType.INTEGER
    if fmt_votes and all(f in (DetectedFormat.INTEGER, DetectedFormat.DECIMAL, DetectedFormat.CURRENCY, DetectedFormat.PERCENTAGE) for f in fmt_votes):
        return DataType.DECIMAL

    return DataType.STRING


def detect_format_for_value(value: str) -> DetectedFormat:
    """Return the first matching :class:`DetectedFormat` for a single value."""
    for fmt, pattern in _PATTERNS:
        if pattern.match(value):
            return fmt
    return DetectedFormat.UNKNOWN


def detect_format(series: pd.Series) -> DetectedFormat:
    """Infer the dominant value-format pattern of a Series (task 12).

    A format is reported only when a strong majority (>= 90%) of the WHOLE
    column's non-null values match the same pattern; otherwise ``NONE``. The
    majority is computed over every row (via distinct-value counts), so the
    threshold reflects the full column rather than a head sample.
    """
    pairs = _distinct_with_counts(series)
    if not pairs:
        return DetectedFormat.NONE

    votes: dict[DetectedFormat, int] = {}
    total = 0
    for value, count in pairs:
        fmt = detect_format_for_value(value)
        total += count
        votes[fmt] = votes.get(fmt, 0) + count

    votes.pop(DetectedFormat.UNKNOWN, None)
    if not votes or total == 0:
        return DetectedFormat.NONE

    winner, count = max(votes.items(), key=lambda kv: kv[1])
    if count / total >= 0.9:
        return winner
    return DetectedFormat.NONE


def sample_values(series: pd.Series, limit: int = 5) -> list[Any]:
    """Return up to ``limit`` representative non-null sample values (task 10)."""
    non_null = series.dropna()
    if non_null.empty:
        return []
    return non_null.head(limit).tolist()
