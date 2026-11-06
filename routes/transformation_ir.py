# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation routes.
# REUSABLE. Exposes the Sprint 6 IR + transformation-JSON generation layer over
# HTTP so the deliverable can be verified against the live server (mirrors
# Deliverable 4's /validation-ir/* routes for transformations).
# ============================================================================
"""Transformation-IR routes: ``/transformation-ir/*``.

* ``GET  /transformation-ir/metadata`` - Sprint 6 task-coverage metadata.
* ``POST /transformation-ir/build``    - build the Transformation IR from a
  posted canonical model (no ingestion / AWS needed).
* ``POST /transformation-ir/generate`` - build IR -> compile exact
  ``transformation-rules`` + ``dataset-transformation-rules`` JSON -> validate
  the generated block, from a posted canonical model.
* ``GET  /transformation-ir/session``  - ingest a session's input + expected-
  output files then run the full generate pipeline end-to-end.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from canonical.models import CanonicalAnalysisModel
from core.dependencies import get_transformation_ir_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.transformation_analysis_service import TransformationAnalysisError
from services.transformation_ir_service import (
    GenerationResult,
    TransformationIRError,
    TransformationIRService,
)
from transformation_analysis.enums import SupportedTransformation
from transformation_ir.models import TransformationGenerationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transformation-ir", tags=["transformation-ir"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_context(
    payload: dict[str, Any]
) -> Optional[TransformationGenerationContext]:
    """Build an optional generation context from a request body ``context`` key."""
    ctx = payload.get("context")
    if not ctx:
        return None
    return TransformationGenerationContext.model_validate(ctx)


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
        "dataset_rule_count": len(ir.dataset_rules),
        "rejected_count": ir.rejected_count(),
        "rejected": [r.model_dump(mode="json") for r in ir.rejected],
    }


def _generation_payload(result: GenerationResult) -> dict[str, Any]:
    """Assemble the full generate response block."""
    return {
        "ir": _ir_payload(result.ir),
        "transformation_rules": result.rows,
        "transformation_rule_count": len(result.rows),
        "dataset_transformation_rules": result.dataset_rows,
        "dataset_transformation_rule_count": len(result.dataset_rows),
        "block_report": {
            "is_valid": result.report.is_valid,
            "row_count": result.report.row_count,
            "dataset_row_count": result.report.dataset_row_count,
            "error_count": len(result.report.errors),
            "warning_count": len(result.report.warnings),
            "issues": [i.model_dump(mode="json") for i in result.report.issues],
        },
    }


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def transformation_ir_metadata():
    """Return Sprint 6 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "6 - Transformation IR & Transformation JSON Generation",
            "supported_transformation_kinds": [
                t.value for t in SupportedTransformation
            ],
            "task_coverage": {
                "1_define_transformation_ir": "transformation_ir.models.TransformationIR",
                "2_rule_representation": "transformation_ir.models.TransformationRuleIR",
                "3_source_references": "transformation_ir.models.SourceRef",
                "4_target_references": "transformation_ir.models.TargetRef",
                "5_transformation_parameters": "TransformationRuleIR.parameters",
                "6_transformation_dependencies": "ColumnTransformationIR.depends_on -> dependsOn",
                "7_map_intent_to_supported": "builder._map_requirement + catalog.is_supported",
                "8_resolve_exact_keyword": "catalog.get_spec().contract_field/config_op",
                "9_preserve_keyword_casing": "verbatim (Case_Conversion, aggrigation, cnt)",
                "10_reject_unsupported": "TransformationIR.rejected (RejectedTransformation)",
                "11_map_ir_to_json_structure": "compiler._compile_column -> TransformationRule",
                "12_generate_transformation_rule_objects": "TransformationRuleCompiler.compile_rules",
                "13_generate_dataset_rule_objects": "TransformationRuleCompiler.compile_dataset_rules",
                "14_populate_uuid_references": "sourceColumnName + dependsOn + dataSetId",
                "15_populate_source_columns": "op payloads (columns/field)",
                "16_populate_target_columns": "targetColumnName + columnSequence + targetFileName",
                "17_populate_parameters": "compiler._config_payload/_build_default_value",
                "18_preserve_ordering": "ops_for_field/_build_config_entry (sorted by order)",
                "19_generate_transformation_block": "compile / compile_datasets",
                "20_validate_generated_block": "validation.validate_transformation_block",
                "21_validate_exact_keys": "validation._check_exact_keys",
                "22_validate_exact_keywords": "validation._check_transform_keywords",
                "23_test_individual_transformations": "tests/test_transformation_ir_compiler.py",
                "24_test_chained_transformations": "tests/test_transformation_ir_compiler.py",
                "25_test_multi_sheet_transformations": "tests/test_transformation_ir_service.py",
            },
        }
    )


# ---------------------------------------------------------------------------
# build (IR only)
# ---------------------------------------------------------------------------
@router.post("/build")
async def build_ir(
    payload: dict[str, Any],
    service: TransformationIRService = Depends(get_transformation_ir_service),
):
    """Build the Transformation IR from a posted canonical model (tasks 1-10)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[transformation-ir] Invalid build request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        ir = await run_in_threadpool(service.build_ir, model, context)
    except TransformationIRError as e:
        logger.exception("[transformation-ir] IR build failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_IR_FAILED, str(e)),
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
    service: TransformationIRService = Depends(get_transformation_ir_service),
):
    """Build IR, compile exact transformation JSON, validate the block (tasks 1-22)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[transformation-ir] Invalid generate request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(service.generate, model, context)
    except TransformationIRError as e:
        logger.exception("[transformation-ir] Generation failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_IR_FAILED, str(e)),
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
    service: TransformationIRService = Depends(get_transformation_ir_service),
):
    """Ingest a session's input + output files then run the full generate pipeline.

    Query params:
        session_id: The session whose input files live under
            ``<input_base>/<session_id>`` and expected-output files under
            ``<output_base>/<session_id>``.
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
        logger.warning("[transformation-ir] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[transformation-ir] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, TransformationAnalysisError, TransformationIRError) as e:
        logger.exception("[transformation-ir] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_IR_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[transformation-ir] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_IR_FAILED, str(e)),
        )
