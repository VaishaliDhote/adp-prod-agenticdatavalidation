# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 7 / Deliverable 8 - /get_configuration endpoint. REUSABLE.
# Rework: repoint to the deterministic JSON compiler + external API execution
#         loop instead of the current Python pipeline entry.
# ============================================================================
"""Configuration generation routes: ``/get_configuration``."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from core.aws_error_mapping import raise_aws_http_exception
from core.dependencies import get_fetch_config_service
from core.errors import ErrorCode, build_error
from core.validation import require_param, sanitize_business_keys
from services.chat_seeding_service import ChatSeedingService
from services.fetch_configuration_service import FetchConfigurationService
from utils.serializer import make_serializable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["configuration"])

# Seeding service is stateless — one shared instance is fine.
_chat_seeder = ChatSeedingService()


@router.get("/get_configuration")
async def fetch_configuration(
    request: Request,
    fetch_config_svc: FetchConfigurationService = Depends(get_fetch_config_service),
):
    """Generate a transformation config for the given session and auto-execute it."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")

    clean = sanitize_business_keys(
        session_id=session_id,
        business_unit=business_unit,
        platform=platform,
        category=category,
    )
    session_id, business_unit = clean["session_id"], clean["business_unit"]
    platform, category = clean["platform"], clean["category"]

    try:
        result = await fetch_config_svc.run(
            session_id=session_id,
            business_unit=business_unit,
            platform=platform,
            category=category,
        )

        # After successful generation, seed chat so the UI's "Save" button
        # (which posts e.g. "save config") flows through the confirm pipeline
        # instead of the first-message review branch.
        _chat_seeder.seed_after_generation(
            session_id=session_id,
            business_unit=business_unit,
            platform=platform,
            category=category,
            summary=result.summary,
        )

        return JSONResponse(content=make_serializable({
            "status": "success",
            "session_id": result.session_id,
            "business_unit": result.business_unit,
            "platform": result.platform,
            "category": result.category,
            "summary": result.summary,
            "timings": result.timings,
            "draft_s3_key": result.draft_s3_key,
            "execution_id": result.execution_id,
            "execution": result.execution,
            "download_url": result.download_url,
            "output_filename": result.output_filename,
        }))
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning("[fetch_configuration] Bad request for session=%s: %s", session_id, e)
        raise HTTPException(status_code=400, detail=build_error(ErrorCode.INVALID_CONFIG_REQUEST, str(e)))
    except FileNotFoundError as e:
        logger.warning("[fetch_configuration] Resource not found for session=%s: %s", session_id, e)
        raise HTTPException(status_code=404, detail=build_error(ErrorCode.REFERENCE_FILE_NOT_FOUND, str(e)))
    except RuntimeError as e:
        logger.exception("[fetch_configuration] Pipeline error for session=%s", session_id)
        raise HTTPException(status_code=500, detail=build_error(ErrorCode.CONFIG_PIPELINE_FAILED, str(e)))
    except Exception as e:
        logger.exception("[fetch_configuration] Unexpected error for session=%s", session_id)
        raise_aws_http_exception(
            e,
            ErrorCode.CONFIG_GENERATION_FAILED,
        )
