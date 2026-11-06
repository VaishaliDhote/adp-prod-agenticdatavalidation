# ============================================================================
# REUSABLE AS-IS - no rework required. Reusable request-parameter validators.
# ============================================================================
"""
core.validation
===============
Reusable request-parameter validators.

These helpers raise the standardized HTTPException with the unified error
contract, so route code stays a single declarative line per parameter.
"""
from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from core.errors import ErrorCode, build_error
from utils.path_utils import (
    sanitize_filename,
    sanitize_path_component,
    sanitize_session_id,
)


def require_param(value: str | None, field: str) -> str:
    """Raise 400 MISSING_PARAM if ``value`` is empty/None."""
    if not value:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.MISSING_PARAM,
                f"{field} is required",
                field=field,
            ),
        )
    return value


def require_params(**fields: str | None) -> None:
    """Validate many params at once: ``require_params(session_id=..., bu=...)``."""
    for name, value in fields.items():
        require_param(value, name)


def sanitize_business_keys(
    *,
    session_id: str | None = None,
    business_unit: str | None = None,
    platform: str | None = None,
    category: str | None = None,
    filename: str | None = None,
    document_id: str | None = None,
) -> dict[str, str | None]:
    """
    Apply path-traversal sanitization to any subset of the standard fields.
    Returns a dict of the sanitized values (only keys that were provided).
    Raises 400 INVALID_PARAM on failure.
    """
    try:
        out: dict[str, str | None] = {}
        if session_id is not None:
            out["session_id"] = sanitize_session_id(session_id)
        if business_unit is not None:
            out["business_unit"] = sanitize_path_component(business_unit, "business_unit")
        if platform is not None:
            out["platform"] = sanitize_path_component(platform, "platform")
        if category is not None:
            out["category"] = sanitize_path_component(category, "category")
        if filename is not None:
            out["filename"] = sanitize_filename(filename)
        if document_id is not None:
            # document_id is UUID-shaped; reuse the session-id validator.
            out["document_id"] = sanitize_session_id(document_id)
        return out
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=build_error(ErrorCode.INVALID_PARAM, str(e)),
        )


def reject_unknown_choice(value: str | None, *, field: str, allowed: Iterable[str], code: str) -> str:
    """Generic enum-style validator."""
    allowed_set = set(allowed)
    if value not in allowed_set:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                code,
                f"{field} must be one of {sorted(allowed_set)}",
                field=field,
            ),
        )
    return value
