# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation routes. REUSABLE.
# Exposes the Sprint 4 IR + validation-JSON generation layer over HTTP so the
# deliverable can be verified against the live server (mirrors Deliverable 3's
# /validation-analysis routes).
# ============================================================================
"""Validation-IR routes: ``/validation-ir/*``.

* ``GET  /validation-ir/metadata`` - Sprint 4 task-coverage metadata.
* ``POST /validation-ir/build``    - build the Validation IR from a posted
  canonical model (no ingestion / AWS needed).
* ``POST /validation-ir/generate`` - build IR -> compile exact validation JSON ->
  validate the generated block, from a posted canonical model.
* ``GET  /validation-ir/session``  - ingest a session's input files then run the
  full generate pipeline end-to-end.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from canonical.models import CanonicalAnalysisModel
from core.dependencies import get_validation_ir_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.validation_analysis_service import ValidationAnalysisError
from services.validation_ir_service import (
    GenerationResult,
    ValidationIRError,
    ValidationIRService,
)
from validation_ir.models import ValidationGenerationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/validation-ir", tags=["validation-ir"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_context(payload: dict[str, Any]) -> Optional[ValidationGenerationContext]:
    """Build an optional generation context from a request body ``context`` key."""
    ctx = payload.get("context")
    if not ctx:
        return None
    return ValidationGenerationContext.model_validate(ctx)


def _extract_model(payload: dict[str, Any]) -> CanonicalAnalysisModel:
    """Extract the canonical model from a request body.

    Accepts either the model at the top level or nested under a ``model`` key
    (so a caller can post ``{"model": {...}, "context": {...}}``).
    """
    raw = payload.get("model", payload)
    return CanonicalAnalysisModel.model_validate(raw)


def _ir_payload(ir: Any) -> dict[str, Any]:
    """Lightweight IR summary block for quick verification."""
    return {
        "session_id": ir.session_id,
        "emittable_column_count": len(ir.emittable_columns()),
        "rule_count": ir.rule_count(),
        "rejected_count": ir.rejected_count(),
        "rejected": [r.model_dump(mode="json") for r in ir.rejected],
    }


def _generation_payload(result: GenerationResult) -> dict[str, Any]:
    """Assemble the full generate response block."""
    return {
        "ir": _ir_payload(result.ir),
        "validation_rules": result.rows,
        "validation_rule_count": len(result.rows),
        "block_report": {
            "is_valid": result.report.is_valid,
            "row_count": result.report.row_count,
            "error_count": len(result.report.errors),
            "warning_count": len(result.report.warnings),
            "issues": [i.model_dump(mode="json") for i in result.report.issues],
        },
    }


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def validation_ir_metadata():
    """Return Sprint 4 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "4 - Validation IR & Validation JSON Generation",
            "task_coverage": {
                "1_define_validation_ir": "validation_ir.models.ValidationIR",
                "2_rule_representation": "validation_ir.models.ValidationRuleIR",
                "3_column_references": "validation_ir.models.ColumnReference",
                "4_validation_parameters": "ValidationRuleIR.parameters",
                "5_validation_dependencies": "ValidationRuleIR.depends_on -> dependsOn",
                "6_map_intent_to_supported": "builder._map_requirement + catalog.is_supported",
                "7_resolve_exact_keyword": "catalog.get_spec().contract_field",
                "8_preserve_keyword_casing": "builder._resolve_keyword (verbatim)",
                "9_reject_unsupported": "ValidationIR.rejected (RejectedValidation)",
                "10_map_ir_to_json_structure": "compiler._compile_column -> ValidationRule",
                "11_generate_rule_objects": "ValidationRuleCompiler.compile_rules",
                "12_populate_uuid_references": "sourceColumn + dependsOn",
                "13_populate_column_references": "ColumnReference.column_uuid",
                "14_populate_parameters": "compiler._apply_rule",
                "15_generate_validation_block": "ValidationRuleCompiler.compile",
                "16_validate_generated_block": "validation.validate_validation_block",
                "17_validate_exact_keys": "validation._check_exact_keys",
                "18_validate_exact_keywords": "validation._check_keyword_values",
                "19_validate_uuid_references": "validation._check_uuid_refs",
                "20_test_individual_rules": "tests/test_validation_ir_compiler.py",
                "21_test_combined_rules": "tests/test_validation_ir_compiler.py",
                "22_test_invalid_scenarios": "tests/test_validation_ir_validation.py",
            },
        }
    )


# ---------------------------------------------------------------------------
# build (IR only)
# ---------------------------------------------------------------------------
@router.post("/build")
async def build_ir(
    payload: dict[str, Any],
    service: ValidationIRService = Depends(get_validation_ir_service),
):
    """Build the Validation IR from a posted canonical model (tasks 1-9)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[validation-ir] Invalid build request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        ir = await run_in_threadpool(service.build_ir, model, context)
    except ValidationIRError as e:
        logger.exception("[validation-ir] IR build failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "summary": _ir_payload(ir),
            "ir": ir.model_dump(mode="json"),
        }
    )


# ---------------------------------------------------------------------------
# generate (IR -> JSON -> report)
# ---------------------------------------------------------------------------
@router.post("/generate")
async def generate(
    payload: dict[str, Any],
    service: ValidationIRService = Depends(get_validation_ir_service),
):
    """Build IR, compile exact validation JSON, validate the block (tasks 1-19)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[validation-ir] Invalid generate request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(service.generate, model, context)
    except ValidationIRError as e:
        logger.exception("[validation-ir] Generation failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )

    return JSONResponse(
        content={"status": "success", **_generation_payload(result)}
    )


# ---------------------------------------------------------------------------
# session (ingest -> generate)
# ---------------------------------------------------------------------------
@router.get("/session")
async def generate_from_session(
    request: Request,
    service: ValidationIRService = Depends(get_validation_ir_service),
):
    """Ingest a session's input files then run the full generate pipeline.

    Query params:
        session_id: The session whose input files live under
            ``<input_base>/<session_id>``.
    """
    session_id = require_param(request.query_params.get("session_id"), "session_id")

    try:
        result = await run_in_threadpool(service.generate_from_session, session_id)
        return JSONResponse(
            content={
                "status": "success",
                "session_id": session_id,
                **_generation_payload(result),
            }
        )
    except ParserNotImplementedError as e:
        logger.warning("[validation-ir] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[validation-ir] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, ValidationAnalysisError, ValidationIRError) as e:
        logger.exception("[validation-ir] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[validation-ir] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )
