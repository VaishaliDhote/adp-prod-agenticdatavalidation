# ============================================================================
# Deliverable 8/10 - FPS execution routes: collect user files and run the real
# ADP File Processing Service flow (upload -> persist -> process -> audit).
# ============================================================================
"""FPS execution routes: ``/fps/*``.

* ``POST /fps/execute`` - multipart upload of one or more input files; uploads
  them to the entity's SMB share, records metadata in DocumentsDvts (+ S3
  backup), triggers processing, and writes an audit entry.
* ``GET  /fps/status``  - list request status for a business entity.
* ``GET  /fps/metadata`` - integration/config sanity metadata.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.dependencies import get_fps_execution_service
from core.errors import ErrorCode, build_error
from core.validation import sanitize_business_keys
from services.fps_execution_service import (
    FpsExecutionError,
    FpsExecutionService,
    UserFile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fps", tags=["fps-execution"])


def _parse_form_data(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.INVALID_PARAM,
                "form_data must be a JSON object string.",
                field="form_data",
            ),
        )


def _parse_source_keys(raw: Optional[str], count: int) -> list[str]:
    """Optional per-file sourceKey overrides (JSON array or comma-separated)."""
    if not raw:
        return [""] * count
    keys: list[str]
    try:
        parsed = json.loads(raw)
        keys = [str(k) for k in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        keys = [k.strip() for k in raw.split(",")]
    keys = (keys + [""] * count)[:count]
    return keys


@router.get("/metadata")
async def fps_metadata(
    service: FpsExecutionService = Depends(get_fps_execution_service),
):
    """Return FPS integration/config metadata for verification."""
    cfg = service._config  # read-only introspection
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "8/10 - Real ADP File Processing Service execution",
            "configured": cfg.configured,
            "shared_file_base_url": cfg.shared_file_base_url,
            "process_base_url": cfg.process_base_url,
            "audit_base_url": cfg.audit_base_url,
            "app_code": cfg.app_code,
            "source": cfg.source,
            "audit_enabled": bool(cfg.audit_base_url and cfg.jwt_token),
        }
    )


@router.post("/execute")
async def fps_execute(
    session_id: str = Form(...),
    business_unit: str = Form(...),
    platform: str = Form(...),
    category: str = Form(...),
    useremail: str = Form(...),
    files: list[UploadFile] = File(...),
    form_data: Optional[str] = Form(None),
    source_keys: Optional[str] = Form(None),
    unique_id: Optional[str] = Form(None),
    service: FpsExecutionService = Depends(get_fps_execution_service),
):
    """Collect user files and run the real FPS execution flow."""
    if not files:
        raise HTTPException(
            status_code=400,
            detail=build_error(ErrorCode.FPS_NO_FILES, "At least one file is required.", field="files"),
        )

    clean = sanitize_business_keys(
        session_id=session_id,
        business_unit=business_unit,
        platform=platform,
        category=category,
    )
    fd = _parse_form_data(form_data)
    keys = _parse_source_keys(source_keys, len(files))

    user_files: list[UserFile] = []
    for uf, key in zip(files, keys):
        content = await uf.read()
        if not content:
            raise HTTPException(
                status_code=400,
                detail=build_error(
                    ErrorCode.EMPTY_UPLOAD,
                    f"Uploaded file '{uf.filename}' is empty.",
                    field="files",
                ),
            )
        user_files.append(
            UserFile(filename=uf.filename or "file", content=content, source_key=key)
        )

    try:
        result = await run_in_threadpool(
            service.execute,
            session_id=clean["session_id"],
            bu=clean["business_unit"],
            platform=clean["platform"],
            category=clean["category"],
            useremail=useremail,
            files=user_files,
            form_data=fd,
            unique_id=(unique_id or "").strip(),
        )
    except FpsExecutionError as exc:
        msg = str(exc)
        code = ErrorCode.FPS_NOT_CONFIGURED if "not configured" in msg.lower() else ErrorCode.FPS_EXECUTION_FAILED
        status = 503 if code == ErrorCode.FPS_NOT_CONFIGURED else 502
        raise HTTPException(status_code=status, detail=build_error(code, msg))

    return JSONResponse(content=result.to_payload())


@router.get("/status")
async def fps_status(
    business_unit: str,
    platform: str,
    category: str,
    useremail: str,
    service: FpsExecutionService = Depends(get_fps_execution_service),
):
    """List request status for a business entity (FPS ``/v2/request/by``)."""
    clean = sanitize_business_keys(
        business_unit=business_unit, platform=platform, category=category,
    )
    try:
        body = await run_in_threadpool(
            service.get_status,
            bu=clean["business_unit"],
            platform=clean["platform"],
            category=clean["category"],
            useremail=useremail,
        )
    except FpsExecutionError as exc:
        raise HTTPException(
            status_code=503,
            detail=build_error(ErrorCode.FPS_NOT_CONFIGURED, str(exc)),
        )
    return JSONResponse(content={"status": "success", "data": body})
