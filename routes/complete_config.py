# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation routes. REUSABLE.
# Exposes the Sprint 7 deterministic complete-config compiler over HTTP so the
# deliverable can be verified against the live server (mirrors Deliverable 4/6's
# /validation-ir and /transformation-ir routes).
# ============================================================================
"""Complete-config routes: ``/complete-config/*``.

* ``GET  /complete-config/metadata`` - Sprint 7 task-coverage metadata.
* ``POST /complete-config/generate`` - run Deliverables 4 & 6, assemble the exact
  complete configuration JSON, and validate the full document, from a posted
  canonical model.
* ``POST /complete-config/assemble`` - assemble + validate from PRE-COMPILED
  validation / transformation blocks (the pure deterministic compiler step; no
  analysis runs).
* ``GET  /complete-config/session``  - ingest a session's input + expected-output
  files then run the full generate pipeline end-to-end.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from canonical.models import CanonicalAnalysisModel
from complete_config.models import CompleteConfigContext
from core.dependencies import get_complete_config_service, get_ingestion_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.ingestion_service import IngestionService
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.complete_config_service import (
    CompleteConfigError,
    CompleteConfigResult,
    CompleteConfigService,
)
from services.llm_validation_service import (
    LLMValidationError,
    LLMValidationService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/complete-config", tags=["complete-config"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_context(payload: dict[str, Any]) -> Optional[CompleteConfigContext]:
    """Build an optional generation context from a request body ``context`` key."""
    ctx = payload.get("context")
    if not ctx:
        return None
    return CompleteConfigContext.model_validate(ctx)


def _extract_model(payload: dict[str, Any]) -> CanonicalAnalysisModel:
    """Extract the canonical model from a request body.

    Accepts either the model at the top level or nested under a ``model`` key
    (so a caller can post ``{"model": {...}, "context": {...}}``).
    """
    raw = payload.get("model", payload)
    return CanonicalAnalysisModel.model_validate(raw)


def _report_payload(result: CompleteConfigResult) -> dict[str, Any]:
    """Compact validation-report block for the response."""
    report = result.report
    return {
        "is_valid": report.is_valid,
        "contract_valid": report.contract_valid,
        "column_count": report.column_count,
        "validation_count": report.validation_count,
        "transformation_count": report.transformation_count,
        "dataset_count": report.dataset_count,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "issues": [i.model_dump(mode="json") for i in report.issues],
    }


def _rejected_payload(result: CompleteConfigResult) -> dict[str, Any]:
    """Surface any rejected (unsupported) validation/transformation requirements."""
    payload: dict[str, Any] = {}
    if result.validation is not None:
        payload["rejected_validation"] = [
            r.model_dump(mode="json") for r in result.validation.ir.rejected
        ]
    if result.transformation is not None:
        payload["rejected_transformation"] = [
            r.model_dump(mode="json") for r in result.transformation.ir.rejected
        ]
    return payload


def _generation_payload(result: CompleteConfigResult) -> dict[str, Any]:
    """Assemble the full generate/assemble response block."""
    return {
        "configuration": result.document,
        "report": _report_payload(result),
        **_rejected_payload(result),
    }


def _context_from_query(request: Request) -> Optional[CompleteConfigContext]:
    """Build an optional complete-config context from query overrides.

    Only the fields present are overridden; the rest keep their model defaults.
    Supports both camelCase (``dataCategory``) and snake_case aliases.
    """
    params = request.query_params
    token_email = getattr(request.state, "user_email", None)
    overrides = {
        "bu": params.get("bu"),
        "platform": params.get("platform"),
        "data_category": params.get("dataCategory") or params.get("data_category"),
        "useremail": token_email or params.get("useremail"),
        "validation_rule_type": (
            params.get("validationRuleType") or params.get("validation_rule_type")
        ),
    }
    overrides = {k: v for k, v in overrides.items() if v}
    if not overrides:
        return None
    return CompleteConfigContext(**overrides)


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def complete_config_metadata():
    """Return Sprint 7 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "7 - Complete JSON Configuration Generation",
            "task_coverage": {
                "1_define_workflow": "services.complete_config_service.CompleteConfigService",
                "2_combine_column_relations": "complete_config.column_relations.ColumnRelationsBuilder",
                "3_combine_configuration_data": "complete_config.configuration_data.ConfigurationDataBuilder",
                "4_combine_dataset_transformation_rules": "assembler._combine_rows(dataset)",
                "5_combine_transformation_rules": "assembler._combine_rows(transformation)",
                "6_combine_validation_rules": "assembler._combine_rows(validation)",
                "7_resolve_cross_block_references": "assembler._combine_rows (identity re-stamp)",
                "8_populate_mandatory_fields": "typed contract models (ColumnRelation/ConfigurationData)",
                "9_populate_dynamic_metadata": "ConfigurationDataBuilder (filesInfo/inputRules)",
                "10_generate_uuids_where_required": "ColumnRelationsBuilder._resolve_uuid (uuid5)",
                "11_preserve_existing_column_uuids": "ColumnRelationsBuilder._resolve_uuid",
                "12_preserve_exact_key_names": "contract.models (extra='forbid')",
                "13_preserve_exact_keyword_names": "contract.enums (verbatim)",
                "14_preserve_exact_key_casing": "contract.models aliases",
                "15_preserve_exact_hierarchy": "assembler._build_document",
                "16_generate_complete_configuration_json": "CompleteConfigAssembler.assemble",
                "17_validate_complete_json_schema": "contract.validate_contract (mapped)",
                "18_validate_business_keywords": "complete_config.validation (KEYWORD)",
                "19_validate_uuid_references": "complete_config.validation._check_uuid_references",
                "20_validate_source_target_references": "complete_config.validation._check_source_target",
                "21_validate_configuration_consistency": "complete_config.validation._check_consistency",
                "22_generate_deterministic_json": "ConfigurationContract.to_contract_json",
                "23_create_complete_json_test_cases": "tests/test_complete_config_*.py",
            },
        }
    )


# ---------------------------------------------------------------------------
# generate (analysis -> blocks -> assemble -> validate)
# ---------------------------------------------------------------------------
@router.post("/generate")
async def generate(
    payload: dict[str, Any],
    service: CompleteConfigService = Depends(get_complete_config_service),
):
    """Generate the complete configuration from a posted canonical model (tasks 1-22)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[complete-config] Invalid generate request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(service.generate, model, context)
    except CompleteConfigError as e:
        logger.exception("[complete-config] Generation failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
        )

    return JSONResponse(
        content={"status": "success", **_generation_payload(result)}
    )


# ---------------------------------------------------------------------------
# assemble (pre-compiled blocks -> assemble -> validate)
# ---------------------------------------------------------------------------
@router.post("/assemble")
async def assemble(
    payload: dict[str, Any],
    service: CompleteConfigService = Depends(get_complete_config_service),
):
    """Assemble + validate from pre-compiled blocks (pure deterministic step).

    Body::

        {
            "model": {...canonical model...},
            "context": {...optional...},
            "validation_rules": [...],
            "transformation_rules": [...],
            "dataset_transformation_rules": [...]
        }
    """
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
        validation_rules = payload.get("validation_rules")
        transformation_rules = payload.get("transformation_rules")
        dataset_rules = payload.get("dataset_transformation_rules")
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[complete-config] Invalid assemble request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    try:
        result = await run_in_threadpool(
            lambda: service.assemble(
                model,
                context,
                validation_rules=validation_rules,
                transformation_rules=transformation_rules,
                dataset_transformation_rules=dataset_rules,
            )
        )
    except CompleteConfigError as e:
        logger.exception("[complete-config] Assembly failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
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
    service: CompleteConfigService = Depends(get_complete_config_service),
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
        logger.warning("[complete-config] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[complete-config] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, CompleteConfigError) as e:
        logger.exception("[complete-config] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[complete-config] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
        )


# ---------------------------------------------------------------------------
# merged-session (ingest -> deterministic + LLM validation rules -> assemble)
# ---------------------------------------------------------------------------
@router.get("/merged-session")
async def generate_merged_from_session(
    request: Request,
    service: CompleteConfigService = Depends(get_complete_config_service),
    ingestion: IngestionService = Depends(get_ingestion_service),
):
    """Build the FINAL validation config from deterministic + LLM rules.

    Ingests a session once, runs BOTH the deterministic validation/transformation
    compiler and the LLM validation detector over the same canonical model,
    merges the two ``validation-rules`` sets (per-column by ``sourceColumn``),
    then assembles + validates the complete configuration JSON.

    Query params:
        session_id: The session whose input/output files live under the standard
            ``<input_base>/<session_id>`` / ``<output_base>/<session_id>`` layout
            (required).
        prefer: On per-column conflicts, which source wins - ``llm`` (default) or
            ``deterministic``.
        bu, platform, dataCategory, validationRuleType, useremail: Optional
            identity/audit overrides applied to every generated row.
    """
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    prefer = request.query_params.get("prefer", "llm").lower()
    if prefer not in {"llm", "deterministic"}:
        raise HTTPException(
            status_code=400,
            detail=build_error(
                ErrorCode.VALIDATION_ERROR,
                "prefer must be one of: llm, deterministic",
                field="prefer",
            ),
        )
    context = _context_from_query(request)

    try:
        # 1) Ingest the session ONCE into the canonical model.
        model = await run_in_threadpool(ingestion.ingest_session, session_id)
        model.session_id = model.session_id or session_id

        # 2) LLM validation rules (async) over the same model.
        llm_context = context.validation_context() if context else None
        llm_service = LLMValidationService()
        llm_result = await llm_service.generate(
            model, context=llm_context, print_reply=True
        )

        # 3) Deterministic compile + merge + assemble (CPU/IO bound).
        result, counts = await run_in_threadpool(
            service.generate_merged,
            model,
            llm_result.rows,
            context,
            prefer=prefer,
        )
    except ParserNotImplementedError as e:
        logger.warning("[complete-config] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[complete-config] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, LLMValidationError, CompleteConfigError) as e:
        logger.exception("[complete-config] Merged generation failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[complete-config] Unexpected merged error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.COMPLETE_CONFIG_FAILED, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "session_id": session_id,
            "prefer": prefer,
            "source_counts": counts,
            **_generation_payload(result),
        }
    )