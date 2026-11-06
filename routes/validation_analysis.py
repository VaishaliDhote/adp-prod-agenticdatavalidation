# ============================================================================
# Deliverable 3 - Validation Rule Identification routes. REUSABLE.
# Exposes the Sprint 3 validation-analysis layer over HTTP so the deliverable
# can be verified against the live server (mirrors Deliverable 1's
# /ingest_session and Deliverable 2's /contract/* routes).
# ============================================================================
"""Validation-analysis routes: ``/validation-analysis/*``.

* ``GET  /validation-analysis/catalog``   - the fixed supported-validation
  catalogue (what the existing API can represent; Sprint 3 task 19).
* ``GET  /validation-analysis/metadata``  - task-coverage metadata mapping every
  Sprint 3 task to the layer that implements it.
* ``GET  /validation-analysis/session``   - ingest a session's input files
  (Deliverable 1) then run validation rule identification over them.
* ``POST /validation-analysis/analyze``   - run identification over a posted
  canonical analysis model (no ingestion / AWS needed).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from canonical.models import CanonicalAnalysisModel
from core.dependencies import get_validation_analysis_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.validation_analysis_service import (
    ValidationAnalysisError,
    ValidationAnalysisService,
)
from validation_analysis.catalog import all_specs
from validation_analysis.enums import SupportedValidation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/validation-analysis", tags=["validation-analysis"])


def _summary_payload(result: Any) -> dict[str, Any]:
    """Build a lightweight summary block for quick verification."""
    s = result.summary
    return {
        "input_file_count": s.input_file_count,
        "sheet_count": s.sheet_count,
        "column_count": s.column_count,
        "requirement_count": s.requirement_count,
        "supported_count": s.supported_count,
        "unsupported_count": s.unsupported_count,
        "mandatory_column_count": s.mandatory_column_count,
        "nullable_column_count": s.nullable_column_count,
        "requirements_by_kind": s.requirements_by_kind,
    }


@router.get("/catalog")
async def validation_catalog():
    """Return the fixed catalogue of supported validations (task 19)."""
    return JSONResponse(
        content={
            "status": "success",
            "supported_validation_count": len(all_specs()),
            "catalog": [
                {
                    "kind": spec.kind.value,
                    "contract_field": spec.contract_field,
                    "parameters": list(spec.parameters),
                    "applies_to": sorted(t.value for t in spec.applies_to),
                    "keyword": spec.keyword,
                    "description": spec.description,
                }
                for spec in all_specs()
            ],
        }
    )


@router.get("/metadata")
async def validation_metadata():
    """Return Sprint 3 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "3 - Validation Rule Identification",
            "supported_validation_kinds": [v.value for v in SupportedValidation],
            "task_coverage": {
                "1_workflow": "ValidationAnalysisService.analyze_session/analyze_model",
                "2_analyze_input_structure": "analyzer._expected_structure",
                "3_expected_column_names": "ExpectedStructure.expected_columns[].name",
                "4_expected_column_count": "ExpectedStructure.expected_column_count",
                "5_expected_column_order": "ExpectedColumn.position (sorted)",
                "6_mandatory_columns": "ColumnValidationProfile.is_mandatory",
                "7_optional_columns": "is_mandatory == False",
                "8_mandatory_values": "SupportedValidation.MANDATORY (isMandatory=Y)",
                "9_nullable_columns": "ColumnValidationProfile.is_nullable",
                "10_data_types": "SupportedValidation.DATA_TYPE (dataType)",
                "11_date_formats": "SupportedValidation.DATE_FORMAT (dataFormat)",
                "12_numeric_formats": "NUMERIC_FORMAT / MIN_VALUE / MAX_VALUE",
                "13_string_formats": "REGEX_PATTERN / STRING_CHECK",
                "14_allowed_value_formats": "SupportedValidation.LIST_OF_VALUES",
                "15_duplicate_constraints": "SupportedValidation.UNIQUE",
                "16_length_constraints": "MIN_LENGTH / MAX_LENGTH",
                "17_pattern_constraints": "REGEX_PATTERN / SPECIAL_CHAR_NOT_ALLOWED",
                "18_other_supported": "catalog.all_specs (full supported set)",
                "19_compare_with_api": "validation_analysis.catalog",
                "20_mark_supported": "SupportStatus.SUPPORTED",
                "21_detect_unsupported": "SupportStatus.UNSUPPORTED + result.unsupported",
                "22_inventory": "ValidationRuleInventory",
                "23_capture_parameters": "ValidationRequirement.parameters",
                "24_source_column_uuids": "ColumnValidationProfile.source_column_uuid",
                "25_analysis_output": "ValidationAnalysisResult",
            },
        }
    )


@router.get("/session")
async def analyze_session(
    request: Request,
    service: ValidationAnalysisService = Depends(get_validation_analysis_service),
):
    """Ingest a session's input files then run validation rule identification.

    Query params:
        session_id: The session whose input files live under
            ``<input_base>/<session_id>``.
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
        logger.warning("[validation-analysis] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[validation-analysis] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, ValidationAnalysisError) as e:
        logger.exception("[validation-analysis] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_ANALYSIS_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[validation-analysis] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_ANALYSIS_FAILED, str(e)),
        )


@router.post("/analyze")
async def analyze_model(
    payload: dict[str, Any],
    service: ValidationAnalysisService = Depends(get_validation_analysis_service),
):
    """Run validation rule identification over a posted canonical model.

    Body:
        A serialized :class:`~canonical.models.CanonicalAnalysisModel`. This path
        needs no ingestion / AWS and is ideal for direct verification.
    """
    try:
        model = CanonicalAnalysisModel.model_validate(payload)
    except Exception as e:  # noqa: BLE001 - invalid canonical model
        logger.warning("[validation-analysis] Invalid canonical model: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(service.analyze_model, model)
    except ValidationAnalysisError as e:
        logger.exception("[validation-analysis] Analysis failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_ANALYSIS_FAILED, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "summary": _summary_payload(result),
            "analysis": result.model_dump(mode="json"),
        }
    )
