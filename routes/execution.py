# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 8/10 - /execute_transformation endpoint. REUSABLE.
# Rework: keep the endpoint; swap the service internals to call the external API
# instead of the quarantined Python executor.
# ============================================================================
"""Transformation execution route: ``/execute_transformation``."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from core.aws_error_mapping import raise_aws_http_exception
from core.dependencies import get_execution_service
from core.errors import ErrorCode, build_error
from core.paths import AppPaths
from core.validation import require_param, sanitize_business_keys
from services.transformation_execution_service import TransformationExecutionService

router = APIRouter(tags=["execution"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/execute_transformation")
async def execute_transformation(
    request: Request,
    service: TransformationExecutionService = Depends(get_execution_service),
):
    """Execute transformation on an input file."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")
    document_id = request.query_params.get("document_id")

    clean = sanitize_business_keys(
        session_id=session_id,
        business_unit=business_unit,
        platform=platform,
        category=category,
        document_id=document_id,
    )
    session_id, business_unit = clean["session_id"], clean["business_unit"]
    platform, category = clean["platform"], clean["category"]
    document_id = clean.get("document_id")

    try:
        result = await service.execute(
            session_id=session_id,
            business_unit=business_unit,
            platform=platform,
            category=category,
            document_id=document_id,
        )

        if not result.validation_passed:
            return _validation_failure_response(service, result, session_id)

        if not result.success:
            _raise_execution_failure(result, business_unit, platform, category, session_id)

        return _success_response(result, session_id)

    except HTTPException:
        raise
    except Exception as e:
        raise_aws_http_exception(e, ErrorCode.EXECUTION_UNEXPECTED_ERROR)


# ── Response helpers ──────────────────────────────────────────────────────

def _validation_failure_response(service, result, session_id: str) -> FileResponse:
    error_report_path = service.build_validation_error_excel(
        mismatched_cols=result.mismatched_cols,
        execution_id=result.execution_id,
        output_dir=AppPaths.EXECUTED_OUTPUT_DIR / result.execution_id,
    )
    error_report_path = _confine_to_executed_dir(Path(error_report_path))
    validation_msg = result.validation_message.encode("ascii", errors="ignore").decode("ascii").strip()

    return FileResponse(
        path=str(error_report_path),
        status_code=422,
        filename=error_report_path.name,
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{error_report_path.name}"',
            "X-Execution-Id": result.execution_id,
            "X-Session-Id": session_id,
            "X-Validation-Msg": validation_msg,
        },
    )


def _raise_execution_failure(result, business_unit: str, platform: str, category: str, session_id: str) -> None:
    err_text = (result.error or "").strip()
    err_lower = err_text.lower()

    if "no confirmed configuration" in err_lower:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.CONFIG_NOT_FOUND,
                "No configuration found for this selection. "
                "Please generate the configuration first from the Generate Configuration tab.",
                business_unit=business_unit,
                platform=platform,
                category=category,
                session_id=session_id,
            ),
        )

    if "no completed input_file" in err_lower:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.INPUT_FILE_NOT_FOUND,
                "No input file has been uploaded for this session yet. "
                "Please upload an input file before running execution.",
                session_id=session_id,
            ),
        )

    status_code = 404 if "not found" in err_lower else 500
    raise HTTPException(
        status_code=status_code,
        detail=build_error(ErrorCode.EXECUTION_FAILED, err_text or "Execution failed."),
    )


def _success_response(result, session_id: str) -> FileResponse:
    output_path = _confine_to_executed_dir(Path(result.output_path))
    return FileResponse(
        path=str(output_path),
        status_code=200,
        filename=output_path.name,
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{output_path.name}"',
            "X-Execution-Id": result.execution_id,
            "X-Session-Id": session_id,
        },
    )


def _confine_to_executed_dir(path: Path) -> Path:
    """Resolve the path and ensure it doesn't escape EXECUTED_OUTPUT_DIR."""
    resolved = path.resolve()
    try:
        resolved.relative_to(AppPaths.EXECUTED_OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail=build_error(
                ErrorCode.OUTPUT_PATH_ESCAPE,
                "Output path escapes the designated output directory.",
            ),
        )
    return resolved
