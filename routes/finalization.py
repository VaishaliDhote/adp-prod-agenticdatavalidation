# ============================================================================
# Deliverable 10 - S3 Storage, Finalization & Future Execution routes.
#
# Exposes the finalize (approve) + store workflow and the new-input (future)
# execution workflow over HTTP so the deliverable can be verified against the
# live server (mirrors the Deliverable 4/6/7/8 verification routes). All state-
# changing routes are authenticated (Bearer) and forward the caller's token to
# the existing execution API.
# ============================================================================
"""Finalization + future-execution routes: ``/finalization/*``.

* ``GET  /finalization/metadata``          - Sprint 10 task-coverage metadata.
* ``POST /finalization/finalize``          - validate + store a config as APPROVED.
* ``GET  /finalization/approved``          - fetch the latest approved config JSON.
* ``GET  /finalization/applicable``        - resolve applicable config (approved->confirmed).
* ``GET  /finalization/versions``          - list stored config versions (history).
* ``GET  /finalization/block``             - retrieve a UUID block from the config.
* ``GET  /finalization/uuid-mappings``     - fetch persisted UUID mappings.
* ``GET  /finalization/audit``             - list the audit trail for a scope.
* ``POST /finalization/execute-new-input`` - run a new input through the stored config.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.dependencies import (
    get_finalization_service,
    get_new_input_execution_service,
)
from core.errors import ErrorCode, build_error
from core.validation import require_param
from knowledge.audit_store import list_audit_events
from knowledge.config_store import (
    get_applicable_config_json,
    get_config_json_version,
    get_latest_approved_json,
    get_uuid_mappings,
    list_config_versions,
)
from services.config_finalization_service import (
    ConfigFinalizationError,
    ConfigFinalizationService,
)
from services.new_input_execution_service import (
    ConfigNotFoundError,
    NewInputExecutionError,
    NewInputExecutionService,
)
from utils.config_block_locator import ConfigBlockLocator, ConfigBlockLocatorError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/finalization", tags=["finalization"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _bearer_token(request: Request) -> Optional[str]:
    """Extract the caller's Bearer token to forward to the backend."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _flag(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", ""}


def _scope_from_query(request: Request) -> tuple[str, str, str]:
    bu = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")
    return bu, platform, category


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def finalization_metadata():
    """Return Sprint 10 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "10 - S3 Storage, Finalization & Future Execution",
            "task_coverage": {
                "1_store_final_json_s3": "storage.s3.config_repository.save_approved_json",
                "2_versioned_and_latest_pointer": "storage.s3.config_repository.save_approved_json (base + _<ts>)",
                "3_approved_dynamodb_record": "storage.dynamodb.document_repository.insert_approved_config_record",
                "4_lookup_latest_approved": "storage.dynamodb.document_repository.fetch_latest_approved_config",
                "5_config_version_history": "storage.dynamodb.document_repository.list_config_records",
                "6_object_version_listing": "storage.s3.config_repository.list_config_object_versions",
                "7_persist_uuid_mappings": "storage.dynamodb.document_repository.store_uuid_mappings",
                "8_derive_uuid_mappings": "services.config_finalization_service._extract_uuid_mappings",
                "9_applicable_config_resolution": "knowledge.config_store.get_applicable_config_json (approved->confirmed)",
                "10_finalize_workflow": "services.config_finalization_service.ConfigFinalizationService.finalize",
                "11_contract_revalidation": "contract.validation.validate_contract (refuse invalid)",
                "12_refresh_summary": "services.json_config_summary.JsonConfigSummaryBuilder",
                "13_new_input_identify_config": "services.new_input_execution_service (get_applicable_config_json)",
                "14_reuse_execution_api": "services.config_reapply_service.ConfigReapplyService.reapply",
                "15_handle_validation_response": "accuracy_loop.client (errorCode / rejections)",
                "16_resolve_new_input_files": "services.new_input_execution_service._resolve_input_files",
                "17_surface_output_download": "services.new_input_execution_service._resolve_output_download_url",
                "18_simulator_fallback": "accuracy_loop.simulator.ExecutionSimulator",
                "19_uuid_block_retrieval": "utils.config_block_locator.ConfigBlockLocator",
                "20_audit_trail": "knowledge.audit_store.record_audit_event / list_audit_events",
                "21_presigned_download": "storage.s3.file_repository.generate_presigned_download_url",
                "22_approval_record_pointer": "knowledge.config_store.store_approved_json",
                "23_edge_case_guards": "ConfigFinalizationError / NewInputExecutionError / ConfigNotFoundError",
            },
            "storage_layout": {
                "s3_configs_prefix": "configurations/{bu}/{platform}/{category}/approved/",
                "s3_base_object": "validation_transformation_config.json (latest pointer)",
                "s3_versioned_object": "validation_transformation_config_<UTC-ts>.json",
                "dynamo_config_pk": "CONFIG#{bu}#{platform}#{category}",
                "dynamo_approved_sk": "APPROVED#<ts>",
                "dynamo_uuidmap_pk": "UUIDMAP#{bu}#{platform}#{category}",
                "dynamo_audit_pk": "AUDIT#{bu}#{platform}#{category}",
            },
        }
    )


# ---------------------------------------------------------------------------
# finalize (validate -> store as approved)
# ---------------------------------------------------------------------------
@router.post("/finalize")
async def finalize_configuration(
    payload: dict[str, Any],
    request: Request,
    service: ConfigFinalizationService = Depends(get_finalization_service),
):
    """Validate + store a configuration as the final APPROVED config (tasks 1-12)."""
    try:
        bu = require_param(payload.get("business_unit"), "business_unit")
        platform = require_param(payload.get("platform"), "platform")
        category = require_param(payload.get("category"), "category")
        session_id = require_param(payload.get("session_id"), "session_id")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)))

    json_content = payload.get("config_json") or payload.get("json_content") or ""
    if isinstance(json_content, (dict, list)):
        import json as _json
        json_content = _json.dumps(json_content)
    if not json_content or not str(json_content).strip():
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, "config_json is required"),
        )

    approved_by = str(payload.get("approved_by") or "")

    try:
        result = await run_in_threadpool(
            service.finalize,
            bu, platform, category, session_id, str(json_content),
            approved_by=approved_by,
        )
    except ConfigFinalizationError as e:
        logger.warning("[finalize] rejected: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.INVALID_CONFIG_CONTRACT, str(e)),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[finalize] failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.FINALIZATION_FAILED, str(e)),
        )

    return JSONResponse(content={"status": "success", **result.to_payload()})


# ---------------------------------------------------------------------------
# approved config (latest)
# ---------------------------------------------------------------------------
@router.get("/approved")
async def approved_configuration(request: Request):
    """Fetch the latest APPROVED configuration JSON for a scope (task 4)."""
    bu, platform, category = _scope_from_query(request)
    json_content = await run_in_threadpool(get_latest_approved_json, bu, platform, category)
    if not json_content:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.APPROVED_CONFIG_NOT_FOUND,
                f"No approved configuration for {bu}/{platform}/{category}",
            ),
        )
    return JSONResponse(content={"status": "success", "config_json": json_content})


# ---------------------------------------------------------------------------
# applicable config (approved -> confirmed)
# ---------------------------------------------------------------------------
@router.get("/applicable")
async def applicable_configuration(request: Request):
    """Resolve the applicable config: approved, else confirmed (task 9)."""
    bu, platform, category = _scope_from_query(request)
    json_content, source = await run_in_threadpool(
        get_applicable_config_json, bu, platform, category
    )
    if not json_content:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.APPLICABLE_CONFIG_NOT_FOUND,
                f"No approved or confirmed configuration for {bu}/{platform}/{category}",
            ),
        )
    return JSONResponse(
        content={"status": "success", "config_source": source, "config_json": json_content}
    )


# ---------------------------------------------------------------------------
# version history
# ---------------------------------------------------------------------------
@router.get("/versions")
async def configuration_versions(request: Request):
    """List stored configuration object versions for a scope (tasks 5, 6)."""
    bu, platform, category = _scope_from_query(request)
    versions = await run_in_threadpool(list_config_versions, bu, platform, category)
    return JSONResponse(content={"status": "success", "count": len(versions), "versions": versions})


# ---------------------------------------------------------------------------
# UUID block retrieval
# ---------------------------------------------------------------------------
@router.get("/block")
async def configuration_block(request: Request):
    """Retrieve a single UUID block from the applicable config (task 19).

    Query params: business_unit, platform, category, and one of ``uuid`` or
    ``column`` (a column name). ``section`` optionally narrows the lookup.
    """
    bu, platform, category = _scope_from_query(request)
    token = request.query_params.get("uuid") or request.query_params.get("column")
    if not token:
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.MISSING_PARAM, "Provide 'uuid' or 'column'"),
        )

    json_content, source = await run_in_threadpool(
        get_applicable_config_json, bu, platform, category
    )
    if not json_content:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.APPLICABLE_CONFIG_NOT_FOUND,
                f"No applicable configuration for {bu}/{platform}/{category}",
            ),
        )

    try:
        locator = ConfigBlockLocator.from_json(json_content)
    except ConfigBlockLocatorError as e:
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.INVALID_CONFIG_CONTRACT, str(e)),
        )

    columns = locator.resolve_column(token)
    if not columns:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.CONFIG_BLOCK_NOT_FOUND,
                f"No column matches '{token}'",
            ),
        )
    if len(columns) > 1:
        raise HTTPException(
            status_code=409,
            detail=build_error(
                ErrorCode.INVALID_PARAM,
                f"Ambiguous column name '{token}' matches {len(columns)} columns; pass 'uuid'",
                candidates=[{"uuid": c.uuid, "columnName": c.column_name} for c in columns],
            ),
        )

    col = columns[0]
    relation = locator.find_column_relation(col.uuid)
    validation = locator.find_validation_rule(col.uuid)
    transformations = locator.find_transformation_rules(col.uuid)
    dataset = locator.find_dataset_rule(col.uuid)

    return JSONResponse(
        content={
            "status": "success",
            "config_source": source,
            "column": {
                "uuid": col.uuid,
                "columnName": col.column_name,
                "sourceKey": col.source_key,
                "position": col.position,
                "dataType": col.data_type,
            },
            "blocks": {
                "column_relation": relation.row if relation else None,
                "validation_rule": validation.row if validation else None,
                "transformation_rules": [t.row for t in transformations],
                "dataset_rule": dataset.row if dataset else None,
            },
        }
    )


# ---------------------------------------------------------------------------
# UUID mappings
# ---------------------------------------------------------------------------
@router.get("/uuid-mappings")
async def uuid_mappings(request: Request):
    """Fetch the persisted file/sheet/column/rule UUID mappings (tasks 7, 8)."""
    bu, platform, category = _scope_from_query(request)
    mappings = await run_in_threadpool(get_uuid_mappings, bu, platform, category)
    if mappings is None:
        raise HTTPException(
            status_code=404,
            detail=build_error(
                ErrorCode.NOT_FOUND,
                f"No UUID mappings stored for {bu}/{platform}/{category}",
            ),
        )
    return JSONResponse(content={"status": "success", "mappings": mappings})


# ---------------------------------------------------------------------------
# audit trail
# ---------------------------------------------------------------------------
@router.get("/audit")
async def audit_trail(request: Request):
    """List the audit trail (config lifecycle + executions) for a scope (task 20)."""
    bu, platform, category = _scope_from_query(request)
    limit = 100
    try:
        if request.query_params.get("limit"):
            limit = max(1, min(1000, int(request.query_params["limit"])))
    except ValueError:
        pass
    events = await run_in_threadpool(list_audit_events, bu, platform, category, limit=limit)
    return JSONResponse(content={"status": "success", "count": len(events), "events": events})


# ---------------------------------------------------------------------------
# execute new input (future execution)
# ---------------------------------------------------------------------------
@router.post("/execute-new-input")
async def execute_new_input(
    payload: dict[str, Any],
    request: Request,
    service: NewInputExecutionService = Depends(get_new_input_execution_service),
):
    """Run a new input file through the applicable stored config (tasks 13-21)."""
    try:
        bu = require_param(payload.get("business_unit"), "business_unit")
        platform = require_param(payload.get("platform"), "platform")
        category = require_param(payload.get("category"), "category")
        session_id = require_param(payload.get("session_id"), "session_id")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)))

    token = _bearer_token(request)
    try:
        result = await run_in_threadpool(
            service.execute,
            bu, platform, category, session_id,
            input_document_id=str(payload.get("input_document_id") or ""),
            auth_token=token,
            compare_output=_flag_value(payload.get("compare_output")),
            produced_output_file=payload.get("produced_output_file"),
            known_output_file=payload.get("known_output_file"),
            output_sheet=payload.get("output_sheet"),
            actor=str(payload.get("actor") or ""),
        )
    except ConfigNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=build_error(ErrorCode.APPLICABLE_CONFIG_NOT_FOUND, str(e)),
        )
    except NewInputExecutionError as e:
        logger.warning("[execute-new-input] failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=build_error(ErrorCode.NEW_INPUT_EXECUTION_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[execute-new-input] unexpected error")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.NEW_INPUT_EXECUTION_FAILED, str(e)),
        )

    return JSONResponse(content={"status": "success", **result.to_payload()})


def _flag_value(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes"}


__all__ = ["router"]
