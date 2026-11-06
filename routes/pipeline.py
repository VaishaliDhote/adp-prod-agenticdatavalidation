# ============================================================================
# End-to-end pipeline routes: generate -> process-API verify -> save.
# Ties Deliverables 1-7 (generate), 8/10 (FPS process execution) and 10 (save)
# into one call so the whole flow can be driven from a single endpoint.
# ============================================================================
"""Pipeline routes: ``/pipeline/*``.

* ``GET  /pipeline/metadata`` - describe the end-to-end flow + config.
* ``POST /pipeline/run``      - run generate -> process-API verify -> save from a
  JSON body (``{session_id, business_unit, platform, category, useremail, ...}``).
* ``GET  /pipeline/session``  - same flow driven entirely by query params.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.dependencies import get_pipeline_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.pipeline_service import PipelineError, PipelineService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _flag(raw: Optional[str], default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no"}


def _run_kwargs(source: dict[str, Any]) -> dict[str, Any]:
    """Build PipelineService.run_from_session kwargs from a params/body dict."""
    kwargs: dict[str, Any] = {}
    bu = source.get("business_unit") or source.get("bu")
    platform = source.get("platform")
    category = source.get("category") or source.get("data_category")
    useremail = source.get("useremail")
    if bu:
        kwargs["bu"] = bu
    if platform:
        kwargs["platform"] = platform
    if category:
        kwargs["category"] = category
    if useremail:
        kwargs["useremail"] = useremail
    if source.get("target_accuracy") is not None:
        try:
            kwargs["target_accuracy"] = float(source["target_accuracy"])
        except (TypeError, ValueError):
            pass
    kwargs["run_process_api"] = _flag(source.get("run_process_api"), True)
    kwargs["treat_output_as_match"] = _flag(source.get("treat_output_as_match"), True)
    kwargs["save_on_success"] = _flag(source.get("save_on_success"), True)
    if isinstance(source.get("form_data"), dict):
        kwargs["form_data"] = source["form_data"]
    return kwargs


def _dispatch(
    service: PipelineService, session_id: str, kwargs: dict[str, Any]
):
    try:
        return service.run_from_session(session_id, **kwargs)
    except (IngestionError, FileParseError, ParserNotImplementedError) as e:
        logger.warning("[pipeline] ingestion failed: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.INGESTION_FAILED, str(e)),
        )
    except PipelineError as e:
        logger.exception("[pipeline] run failed")
        msg = str(e)
        if "No input files" in msg:
            code, status = ErrorCode.PIPELINE_NO_INPUT_FILES, 422
        elif "not configured" in msg.lower():
            code, status = ErrorCode.FPS_NOT_CONFIGURED, 503
        else:
            code, status = ErrorCode.PIPELINE_FAILED, 502
        raise HTTPException(status_code=status, detail=build_error(code, msg))


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def pipeline_metadata(
    service: PipelineService = Depends(get_pipeline_service),
):
    """Describe the end-to-end pipeline and its process-API configuration."""
    fps_cfg = service._fps._config  # read-only introspection
    return JSONResponse(
        content={
            "status": "success",
            "flow": [
                "1. Generate exact JSON configuration from the session's input + expected output files (D1-D7).",
                "2. Upload the input file(s) and trigger the real ADP File Processing Service (D8/10).",
                "3. Score verification (produced output treated as a full match today; accuracy loop is not run).",
                "4. Save the final configuration when accuracy >= target (default 0.85).",
            ],
            "target_accuracy_default": 0.85,
            "process_api_configured": fps_cfg.configured,
            "process_base_url": fps_cfg.process_base_url,
            "shared_file_base_url": fps_cfg.shared_file_base_url,
        }
    )


# ---------------------------------------------------------------------------
# run (JSON body)
# ---------------------------------------------------------------------------
@router.post("/run")
async def run_pipeline(
    payload: dict[str, Any],
    service: PipelineService = Depends(get_pipeline_service),
):
    """Run the end-to-end pipeline from a JSON body."""
    session_id = require_param(payload.get("session_id"), "session_id")
    kwargs = _run_kwargs(payload)
    result = await run_in_threadpool(_dispatch, service, session_id, kwargs)
    return JSONResponse(content={"status": "success", **result.to_payload()})


# ---------------------------------------------------------------------------
# session (query params)
# ---------------------------------------------------------------------------
@router.get("/session")
async def run_pipeline_from_session(
    request: Request,
    service: PipelineService = Depends(get_pipeline_service),
):
    """Run the end-to-end pipeline driven entirely by query params."""
    q = dict(request.query_params)
    session_id = require_param(q.get("session_id"), "session_id")
    kwargs = _run_kwargs(q)
    result = await run_in_threadpool(_dispatch, service, session_id, kwargs)
    # Keep the query response light: omit the full document unless asked.
    include_doc = _flag(q.get("include_document"), False)
    return JSONResponse(
        content={"status": "success", **result.to_payload(include_document=include_doc)}
    )


__all__ = ["router"]
