# ============================================================================
# Deliverable 5 - Transformation Rule Identification routes. REUSABLE.
# Exposes the Sprint 5 transformation-analysis layer over HTTP so the
# deliverable can be verified against the live server (mirrors Deliverable 3's
# /validation-analysis/* and Deliverable 4's /validation-ir/* routes).
# ============================================================================
"""Transformation-analysis routes: ``/transformation-analysis/*``.

* ``GET  /transformation-analysis/catalog``   - the fixed supported-transformation
  catalogue (what the existing API can represent; Sprint 5 tasks 14-15).
* ``GET  /transformation-analysis/metadata``  - task-coverage metadata mapping
  every Sprint 5 task to the layer that implements it.
* ``GET  /transformation-analysis/session``   - ingest a session's input +
  expected-output files (Deliverable 1) then run transformation rule
  identification over the differences.
* ``POST /transformation-analysis/analyze``   - run identification over a posted
  canonical analysis model (no ingestion / AWS needed).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from canonical.models import CanonicalAnalysisModel
from core.dependencies import get_transformation_analysis_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.transformation_analysis_service import (
    TransformationAnalysisError,
    TransformationAnalysisService,
)
from transformation_analysis.catalog import all_specs
from transformation_analysis.enums import SupportedTransformation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transformation-analysis", tags=["transformation-analysis"])


def _summary_payload(result: Any) -> dict[str, Any]:
    """Build a lightweight summary block for quick verification."""
    s = result.summary
    return {
        "input_file_count": s.input_file_count,
        "output_file_count": s.output_file_count,
        "sheet_mapping_count": s.sheet_mapping_count,
        "target_column_count": s.target_column_count,
        "requirement_count": s.requirement_count,
        "supported_count": s.supported_count,
        "unsupported_count": s.unsupported_count,
        "direct_count": s.direct_count,
        "renamed_count": s.renamed_count,
        "derived_count": s.derived_count,
        "transformed_count": s.transformed_count,
        "multi_transform_count": s.multi_transform_count,
        "added_column_count": s.added_column_count,
        "removed_column_count": s.removed_column_count,
        "requirements_by_kind": s.requirements_by_kind,
    }


@router.get("/catalog")
async def transformation_catalog():
    """Return the fixed catalogue of supported transformations (tasks 14-15)."""
    return JSONResponse(
        content={
            "status": "success",
            "supported_transformation_count": len(all_specs()),
            "catalog": [
                {
                    "kind": spec.kind.value,
                    "contract_field": spec.contract_field,
                    "config_op": spec.config_op,
                    "parameters": list(spec.parameters),
                    "keyword": spec.keyword,
                    "dataset_level": spec.dataset_level,
                    "description": spec.description,
                }
                for spec in all_specs()
            ],
        }
    )


@router.get("/metadata")
async def transformation_metadata():
    """Return Sprint 5 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "5 - Transformation Rule Identification",
            "supported_transformation_kinds": [t.value for t in SupportedTransformation],
            "task_coverage": {
                "1_workflow": "TransformationAnalysisService.analyze_session/analyze_model",
                "2_analyze_input_output": "TransformationAnalyzer.analyze",
                "3_sheet_mappings": "SheetTransformationMapping (analyzer._sheet_mappings)",
                "4_added_removed_columns": "result.added_columns / result.removed_columns",
                "5_direct_mappings": "TransformationCategory.DIRECT",
                "6_renamed_columns": "TransformationCategory.RENAMED (RENAME requirement)",
                "7_derived_columns": "TransformationCategory.DERIVED (added, no source)",
                "8_transformed_columns": "TransformationCategory.TRANSFORMED (1 value transform)",
                "9_multi_transform_columns": "TransformationCategory.MULTI_TRANSFORM (>1)",
                "10_source_column_link": "ColumnTransformationProfile.source_columns",
                "11_target_column_link": "ColumnTransformationProfile.target_column",
                "12_capture_parameters": "TransformationRequirement.parameters",
                "13_transform_ordering": "TransformationRequirement.order",
                "14_supported_catalog": "transformation_analysis.catalog.all_specs",
                "15_map_supported": "SupportStatus.SUPPORTED",
                "16_detect_unsupported": "SupportStatus.UNSUPPORTED + result.unsupported",
                "17_transformation_required": "ColumnTransformationProfile.transformation_required",
                "18_source_uuid": "ColumnRef.column_uuid (source_columns)",
                "19_target_uuid": "ColumnRef.column_uuid (target_column)",
                "20_inventory_output": "TransformationRuleInventory / TransformationAnalysisResult",
            },
        }
    )


@router.get("/session")
async def analyze_session(
    request: Request,
    service: TransformationAnalysisService = Depends(get_transformation_analysis_service),
):
    """Ingest a session's input + output files then run identification.

    Query params:
        session_id: The session whose input files live under
            ``<input_base>/<session_id>`` and expected-output files under
            ``<output_base>/<session_id>``.
    """
    session_id = require_param(request.query_params.get("session_id"), "session_id")

    try:
        result = await run_in_threadpool(service.analyze_session, session_id)
        return JSONResponse(
            content={
                "status": "success",
                "session_id": session_id,
                "summary": _summary_payload(result),
                "analysis": result.model_dump(mode="json"),
            }
        )
    except ParserNotImplementedError as e:
        logger.warning("[transformation-analysis] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[transformation-analysis] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, TransformationAnalysisError) as e:
        logger.exception("[transformation-analysis] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_ANALYSIS_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[transformation-analysis] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_ANALYSIS_FAILED, str(e)),
        )


@router.post("/analyze")
async def analyze_model(
    payload: dict[str, Any],
    service: TransformationAnalysisService = Depends(get_transformation_analysis_service),
):
    """Run transformation rule identification over a posted canonical model.

    Body:
        A serialized :class:`~canonical.models.CanonicalAnalysisModel` containing
        input + expected-output files and relationships. This path needs no
        ingestion / AWS and is ideal for direct verification.
    """
    try:
        model = CanonicalAnalysisModel.model_validate(payload)
    except Exception as e:  # noqa: BLE001 - invalid canonical model
        logger.warning("[transformation-analysis] Invalid canonical model: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(service.analyze_model, model)
    except TransformationAnalysisError as e:
        logger.exception("[transformation-analysis] Analysis failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.TRANSFORMATION_ANALYSIS_FAILED, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "summary": _summary_payload(result),
            "analysis": result.model_dump(mode="json"),
        }
    )
