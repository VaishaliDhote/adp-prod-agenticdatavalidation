# ============================================================================
# REUSABLE AS-IS - no rework required. Unified error contract.
# ============================================================================
"""
core.errors
===========
Unified error contract.

Every error response in this API has the shape::

    {
        "detail": {
            "code":    "<STABLE_ERROR_CODE>",   # UI can branch on this
            "message": "<user-facing text>",    # UI can render this directly
            ...optional extra context fields...
        }
    }

Two mechanisms keep this contract uniform:
  1. ``build_error(code, message, **extra)`` (aliased as ``_err``) — used at
     every explicit ``raise HTTPException`` site.
  2. ``install_error_handlers(app)`` — registers global exception handlers
     that normalize ANY error (plain-string ``HTTPException`` raises, FastAPI
     422 validation errors, uncaught exceptions) into the same shape.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode:
    """Stable, UI-facing error codes. Treat values as part of the public API."""

    # Generic
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Parameter-level
    MISSING_PARAM = "MISSING_PARAM"
    INVALID_PARAM = "INVALID_PARAM"

    # Business-entity
    INVALID_CATEGORY = "INVALID_CATEGORY"
    CATEGORY_NOT_FOUND = "CATEGORY_NOT_FOUND"
    INVALID_COMBINATION = "INVALID_COMBINATION"
    FETCH_BU_FAILED = "FETCH_BU_FAILED"
    FETCH_PLATFORM_FAILED = "FETCH_PLATFORM_FAILED"
    FETCH_CATEGORY_FAILED = "FETCH_CATEGORY_FAILED"
    INSERT_CATEGORY_FAILED = "INSERT_CATEGORY_FAILED"
    DELETE_CATEGORY_FAILED = "DELETE_CATEGORY_FAILED"
    AWS_CREDENTIALS_EXPIRED = "AWS_CREDENTIALS_EXPIRED"
    AWS_CREDENTIALS_UNAVAILABLE = "AWS_CREDENTIALS_UNAVAILABLE"
    AWS_SERVICE_UNAVAILABLE = "AWS_SERVICE_UNAVAILABLE"

    # Upload
    INVALID_FILE_ROLE = "INVALID_FILE_ROLE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    INVALID_UPLOAD_REQUEST = "INVALID_UPLOAD_REQUEST"
    INVALID_CONFIRM_REQUEST = "INVALID_CONFIRM_REQUEST"
    UPLOAD_INIT_FAILED = "UPLOAD_INIT_FAILED"
    CONFIRM_UPLOAD_FAILED = "CONFIRM_UPLOAD_FAILED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    EMPTY_UPLOAD = "EMPTY_UPLOAD"
    UPLOAD_NOT_FOUND_IN_S3 = "UPLOAD_NOT_FOUND_IN_S3"

    # Configuration
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_SUMMARY_MISSING = "CONFIG_SUMMARY_MISSING"
    CONFIG_SUMMARY_FETCH_FAILED = "CONFIG_SUMMARY_FETCH_FAILED"
    CONFIG_PIPELINE_FAILED = "CONFIG_PIPELINE_FAILED"
    CONFIG_GENERATION_FAILED = "CONFIG_GENERATION_FAILED"
    INVALID_CONFIG_REQUEST = "INVALID_CONFIG_REQUEST"
    REFERENCE_FILE_NOT_FOUND = "REFERENCE_FILE_NOT_FOUND"
    CONFIG_EDITOR_PIPELINE_FAILED = "CONFIG_EDITOR_PIPELINE_FAILED"

    # Execution
    INPUT_FILE_NOT_FOUND = "INPUT_FILE_NOT_FOUND"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_UNEXPECTED_ERROR = "EXECUTION_UNEXPECTED_ERROR"
    OUTPUT_PATH_ESCAPE = "OUTPUT_PATH_ESCAPE"

    # Ingestion / Canonical model (Deliverable 1)
    INGESTION_FAILED = "INGESTION_FAILED"
    FILE_PARSE_FAILED = "FILE_PARSE_FAILED"
    SHEET_PARSE_FAILED = "SHEET_PARSE_FAILED"
    PARSER_NOT_IMPLEMENTED = "PARSER_NOT_IMPLEMENTED"

    # JSON contract (Deliverable 2)
    CONTRACT_VALIDATION_ERROR = "CONTRACT_VALIDATION_ERROR"

    # Validation rule identification (Deliverable 3)
    VALIDATION_ANALYSIS_FAILED = "VALIDATION_ANALYSIS_FAILED"

    # Validation IR & validation JSON generation (Deliverable 4)
    VALIDATION_IR_FAILED = "VALIDATION_IR_FAILED"

    # Transformation rule identification (Deliverable 5)
    TRANSFORMATION_ANALYSIS_FAILED = "TRANSFORMATION_ANALYSIS_FAILED"

    # Transformation IR & transformation JSON generation (Deliverable 6)
    TRANSFORMATION_IR_FAILED = "TRANSFORMATION_IR_FAILED"

    # Complete JSON configuration generation (Deliverable 7)
    COMPLETE_CONFIG_FAILED = "COMPLETE_CONFIG_FAILED"

    # Existing API execution & development accuracy loop (Deliverable 8)
    ACCURACY_LOOP_FAILED = "ACCURACY_LOOP_FAILED"
    EXECUTION_API_FAILED = "EXECUTION_API_FAILED"
    API_VALIDATION_FAILED = "API_VALIDATION_FAILED"
    API_TRANSFORMATION_FAILED = "API_TRANSFORMATION_FAILED"
    OUTPUT_COMPARISON_FAILED = "OUTPUT_COMPARISON_FAILED"
    OUTPUT_FILE_NOT_FOUND = "OUTPUT_FILE_NOT_FOUND"

    # FPS (real ADP File Processing Service) execution (Deliverable 8/10)
    FPS_NOT_CONFIGURED = "FPS_NOT_CONFIGURED"
    FPS_EXECUTION_FAILED = "FPS_EXECUTION_FAILED"
    FPS_NO_FILES = "FPS_NO_FILES"

    # End-to-end pipeline (generate -> process-API verify -> save)
    PIPELINE_FAILED = "PIPELINE_FAILED"
    PIPELINE_NO_INPUT_FILES = "PIPELINE_NO_INPUT_FILES"

    # S3 storage, finalization & future execution (Deliverable 10)
    FINALIZATION_FAILED = "FINALIZATION_FAILED"
    INVALID_CONFIG_CONTRACT = "INVALID_CONFIG_CONTRACT"
    APPROVED_CONFIG_NOT_FOUND = "APPROVED_CONFIG_NOT_FOUND"
    APPLICABLE_CONFIG_NOT_FOUND = "APPLICABLE_CONFIG_NOT_FOUND"
    NEW_INPUT_EXECUTION_FAILED = "NEW_INPUT_EXECUTION_FAILED"
    CONFIG_BLOCK_NOT_FOUND = "CONFIG_BLOCK_NOT_FOUND"
    CONFIG_VERSION_NOT_FOUND = "CONFIG_VERSION_NOT_FOUND"


# Map HTTP status → default semantic code when caller didn't supply one.
_STATUS_CODE_MAP: dict[int, str] = {
    400: ErrorCode.BAD_REQUEST,
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: ErrorCode.NOT_FOUND,
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: ErrorCode.VALIDATION_ERROR,
    429: "RATE_LIMITED",
    500: ErrorCode.INTERNAL_ERROR,
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def build_error(code: str, message: str, **extra: Any) -> dict:
    """Build the standardized ``detail`` payload for an HTTPException."""
    payload: dict = {"code": code, "message": message}
    payload.update(extra)
    return payload


# Backwards-compatible short alias used at raise sites.
_err = build_error


def normalize_detail(detail: Any, status_code: int) -> dict:
    """
    Coerce any HTTPException.detail into ``{code, message, ...}``.
    Already-structured payloads are passed through; plain values are wrapped.
    """
    default_code = _STATUS_CODE_MAP.get(status_code, "ERROR")

    if isinstance(detail, dict):
        code = detail.get("code") or default_code
        message = detail.get("message") or detail.get("detail") or "An error occurred."
        extras = {k: v for k, v in detail.items() if k not in ("code", "message")}
        return {"code": code, "message": str(message), **extras}

    return {"code": default_code, "message": str(detail) if detail else "An error occurred."}


def install_error_handlers(app: FastAPI) -> None:
    """Register the three global exception handlers that enforce the contract."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": normalize_detail(exc.detail, exc.status_code)},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(
            str(p) for p in first.get("loc", []) if p not in ("query", "body", "path", "header")
        )
        msg = first.get("msg", "Request validation failed.")
        message = f"{loc}: {msg}" if loc else msg
        return JSONResponse(
            status_code=422,
            content={"detail": build_error(ErrorCode.VALIDATION_ERROR, message, errors=errors)},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("[unhandled_exception] %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": build_error(
                ErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred. Please try again.",
            )},
        )
