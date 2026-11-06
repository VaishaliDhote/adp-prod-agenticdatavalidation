# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - BU / platform / category metadata routes. REUSABLE.
# Rework: align entity metadata with S3 configuration versioning / lookup keys.
# ============================================================================
"""Business entity routes: BU / platform / category lookup and mutation."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.aws_error_mapping import raise_aws_http_exception
from core.dependencies import get_business_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from services.business_entity_service import BusinessEntityService

router = APIRouter(tags=["business-entity"])


def _user_envelope(request: Request, **extra) -> dict:
    """Common success-response fields injected on every business-entity route."""
    return {
        "status": "success",
        "user_id": request.state.user_id,
        "user_name": request.state.user_name,
        **extra,
    }


@router.get("/fetch_bu")
async def fetch_bu(
    request: Request,
    service: BusinessEntityService = Depends(get_business_service),
):
    """Fetch all business units."""
    session_id = request.query_params.get("session_id", str(uuid.uuid4()))
    try:
        business_units = await run_in_threadpool(service.get_all_business_units)
        return JSONResponse(_user_envelope(
            request,
            session_id=session_id,
            business_units=business_units,
        ))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.FETCH_BU_FAILED)


@router.get("/fetch_platform")
async def fetch_platform(
    request: Request,
    service: BusinessEntityService = Depends(get_business_service),
):
    """Fetch platforms for a business unit."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")

    try:
        platforms = await run_in_threadpool(service.get_platforms, business_unit)
        return JSONResponse(_user_envelope(
            request,
            session_id=session_id,
            business_unit=business_unit,
            platforms=platforms,
        ))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.FETCH_PLATFORM_FAILED)


@router.get("/fetch_category")
async def fetch_category(
    request: Request,
    service: BusinessEntityService = Depends(get_business_service),
):
    """Fetch categories for a business unit and platform."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")

    try:
        categories = await run_in_threadpool(service.get_categories, business_unit, platform)
        return JSONResponse(_user_envelope(
            request,
            session_id=session_id,
            categories=categories,
        ))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.FETCH_CATEGORY_FAILED)


@router.post("/insert_category")
async def insert_category(
    request: Request,
    service: BusinessEntityService = Depends(get_business_service),
):
    """Insert a new category."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")

    try:
        await run_in_threadpool(service.create_category, business_unit, platform, category)
        return JSONResponse(_user_envelope(
            request,
            session_id=session_id,
            message=f"Category '{category}' inserted successfully",
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=build_error(ErrorCode.INVALID_CATEGORY, str(e)))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.INSERT_CATEGORY_FAILED)


@router.delete("/delete_category")
async def delete_category(
    request: Request,
    service: BusinessEntityService = Depends(get_business_service),
):
    """Delete a category."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")

    try:
        await run_in_threadpool(service.remove_category, business_unit, platform, category)
        return JSONResponse(_user_envelope(
            request,
            session_id=session_id,
            message=f"Category '{category}' deleted successfully",
        ))
    except ValueError as e:
        err = str(e)
        if "not found" in err.lower():
            raise HTTPException(status_code=404, detail=build_error(ErrorCode.CATEGORY_NOT_FOUND, err))
        raise HTTPException(status_code=400, detail=build_error(ErrorCode.INVALID_CATEGORY, err))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.DELETE_CATEGORY_FAILED)
