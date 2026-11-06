# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - File-upload routes (presigned URL + confirm, tasks 16). REUSABLE.
# ============================================================================
"""File-upload routes: presigned URL generation and upload confirmation."""
from __future__ import annotations

import logging
import os

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.aws_error_mapping import raise_aws_http_exception
from core.dependencies import get_business_service, get_file_service
from core.errors import ErrorCode, build_error
from core.validation import (
    require_param,
    reject_unknown_choice,
    sanitize_business_keys,
)
from services.business_entity_service import BusinessEntityService
from services.file_upload_service import FileUploadService
from storage.dynamodb.document_repository import (
    fetch_documents_by_session,
    update_upload_status,
)
from storage.s3.client import get_s3_client
from storage.s3.file_repository import VALID_FILE_ROLES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["file-upload"])

_VALID_UPLOAD_ROLES = VALID_FILE_ROLES - {"executed_output_files"}
_VALID_CONFIRM_TYPES = {"INPUT_REF", "OUTPUT_REF", "INPUT_FILE"}
# Supported upload extensions. Excel + the tabular/structured formats the
# ingestion layer can parse (CSV, JSON).
_VALID_UPLOAD_EXTS = (".xlsx", ".xls", ".csv", ".json")

# When True, skip the BU/platform/category combination check in /upload_file.
# Intended for local testing before the combination has been seeded into
# DynamoDB. Keep False (the default) in production.
_SKIP_COMBINATION_VALIDATION = (
    os.getenv("SKIP_COMBINATION_VALIDATION", "False").lower() == "true"
)


@router.post("/upload_file")
async def upload_file(
    request: Request,
    service: FileUploadService = Depends(get_file_service),
    business_service: BusinessEntityService = Depends(get_business_service),
):
    """Initiate a file upload by generating a presigned URL."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")
    file_role = request.query_params.get("file_role")
    filename = require_param(request.query_params.get("filename"), "filename")

    if file_role not in _VALID_UPLOAD_ROLES:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.INVALID_FILE_ROLE,
                "Invalid file_role. Must be one of: input_reference_files, output_reference_files, input_files",
                field="file_role",
            ),
        )
    if not filename.lower().endswith(_VALID_UPLOAD_EXTS):
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.UNSUPPORTED_FILE_TYPE,
                "Only .xlsx/.xls/.csv/.json files are supported",
                field="filename",
            ),
        )

    clean = sanitize_business_keys(
        session_id=session_id,
        business_unit=business_unit,
        platform=platform,
        category=category,
        filename=filename,
    )
    session_id, business_unit = clean["session_id"], clean["business_unit"]
    platform, category, filename = clean["platform"], clean["category"], clean["filename"]

    try:
        if _SKIP_COMBINATION_VALIDATION:
            logger.warning(
                "[upload_file] SKIP_COMBINATION_VALIDATION=True - bypassing "
                "BU/platform/category check for %s/%s/%s",
                business_unit, platform, category,
            )
        else:
            is_valid_combo = await run_in_threadpool(
                business_service.validate_combination,
                business_unit,
                platform,
                category,
            )
            if not is_valid_combo:
                raise HTTPException(
                    status_code=400,
                    detail=build_error(
                        ErrorCode.INVALID_COMBINATION,
                        f"Invalid combination: {business_unit}/{platform}/{category}",
                        business_unit=business_unit, platform=platform, category=category,
                    ),
                )

        result = await run_in_threadpool(
            service.initiate_upload,
            session_id=session_id,
            business_unit=business_unit,
            platform=platform,
            category=category,
            file_role=file_role,
            filename=filename,
        )
        return JSONResponse({
            "status": "success",
            "session_id": session_id,
            "presigned_url": result.presigned_url,
            "s3_key": result.s3_key,
            "document_id": result.document_id,
            "content_type": result.content_type,
            "message": "PUT the file bytes to presigned_url with the Content-Type header.",
        })
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=build_error(ErrorCode.INVALID_UPLOAD_REQUEST, str(e)))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.UPLOAD_INIT_FAILED)


@router.post("/confirm_upload")
async def confirm_upload(
    request: Request,
    service: FileUploadService = Depends(get_file_service),
):
    """Confirm that a file upload has completed."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    document_id = require_param(request.query_params.get("document_id"), "document_id")
    file_type = reject_unknown_choice(
        request.query_params.get("file_type"),
        field="file_type",
        allowed=_VALID_CONFIRM_TYPES,
        code=ErrorCode.INVALID_FILE_TYPE,
    )

    try:
        docs = await run_in_threadpool(fetch_documents_by_session, session_id, file_type)
        doc = next((d for d in docs if d.get("document_id") == document_id), None)
        if not doc:
            raise HTTPException(
                status_code=404,
                detail=build_error(
                    ErrorCode.DOCUMENT_NOT_FOUND,
                    f"No document found for document_id='{document_id}' in session='{session_id}' with file_type='{file_type}'.",
                    document_id=document_id, session_id=session_id, file_type=file_type,
                ),
            )

        s3_key = doc.get("s3_key")
        s3_bucket = doc.get("s3_bucket", os.getenv("S3_FILES_BUCKET", "agentic-data-validate-tranform-files"))
        if s3_key:
            await run_in_threadpool(_verify_s3_object, s3_key, s3_bucket)

        await run_in_threadpool(update_upload_status, session_id, file_type, document_id, "completed")
        return JSONResponse({
            "status": "success",
            "session_id": session_id,
            "document_id": document_id,
            "upload_status": "completed",
        })
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=build_error(ErrorCode.INVALID_CONFIRM_REQUEST, str(e)))
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.CONFIRM_UPLOAD_FAILED)


def _verify_s3_object(s3_key: str, s3_bucket: str) -> None:
    """Raise the appropriate structured 400 if the S3 object is missing or empty."""
    try:
        head = get_s3_client().head_object(Bucket=s3_bucket, Key=s3_key)
    except HTTPException:
        raise
    except ClientError as head_err:
        aws_code = (head_err.response.get("Error", {}).get("Code") or "").strip()
        if aws_code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(
                status_code=400,
                detail=build_error(
                    ErrorCode.UPLOAD_NOT_FOUND_IN_S3,
                    f"File not found in S3 (key='{s3_key}'): {head_err}. "
                    "Please PUT the file bytes to the presigned URL before confirming.",
                ),
            )
        raise_aws_http_exception(head_err, ErrorCode.CONFIRM_UPLOAD_FAILED)
    except Exception as head_err:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.UPLOAD_NOT_FOUND_IN_S3,
                f"File not found in S3 (key='{s3_key}'): {head_err}. "
                "Please PUT the file bytes to the presigned URL before confirming.",
            ),
        )

    content_length = head.get("ContentLength", 0)
    if content_length == 0:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.EMPTY_UPLOAD,
                f"S3 object is empty (0 bytes) for key='{s3_key}'. "
                "The file was not uploaded correctly. "
                "Please PUT the file bytes to the presigned URL before confirming.",
            ),
        )
    logger.info("[confirm_upload] S3 object verified: key=%s  size=%d bytes", s3_key, content_length)
