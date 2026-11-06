# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop routes.
# Exposes the Sprint 8 execute -> compare -> analyze -> correct -> regenerate loop
# over HTTP so the deliverable can be verified against the live server (mirrors
# the Deliverable 4/6/7 verification routes).
# ============================================================================
"""Accuracy-loop routes: ``/accuracy-loop/*``.

* ``GET  /accuracy-loop/metadata`` - Sprint 8 task-coverage metadata.
* ``POST /accuracy-loop/run``      - run the full loop from a posted canonical
  model (+ optional loop context / controls), returning the final configuration,
  the final accuracy, and the complete per-iteration history.
* ``GET  /accuracy-loop/session``  - ingest a session's input + expected-output
  files then run the loop end-to-end.
* ``GET  /accuracy-loop/history``  - fetch a previously stored run's history.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from accuracy_loop.models import AccuracyLoopContext
from accuracy_loop.output_comparison import CompareOptions
from canonical.models import CanonicalAnalysisModel
from core.dependencies import get_accuracy_loop_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from knowledge.iteration_store import get_iteration_history, list_iteration_runs
from services.accuracy_loop_service import (
    AccuracyLoopError,
    AccuracyLoopResult,
    AccuracyLoopService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accuracy-loop", tags=["accuracy-loop"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _bearer_token(request: Request) -> Optional[str]:
    """Extract the caller's Bearer token to forward to the backend (task 2)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _parse_context(payload: dict[str, Any]) -> Optional[AccuracyLoopContext]:
    ctx = payload.get("context")
    if not ctx:
        return None
    return AccuracyLoopContext.model_validate(ctx)


def _extract_model(payload: dict[str, Any]) -> CanonicalAnalysisModel:
    raw = payload.get("model", payload)
    return CanonicalAnalysisModel.model_validate(raw)


def _outcome_payload(result: AccuracyLoopResult) -> dict[str, Any]:
    """Build the loop-run response block."""
    outcome = result.outcome
    report = outcome.complete_config_report
    return {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "used_simulator": result.used_simulator,
        "history_persisted": result.persisted,
        "final_accuracy": outcome.final_accuracy.accuracy if outcome.final_accuracy else None,
        "target_accuracy": None,  # filled by caller context below
        "stop_reason": outcome.stop_reason.value if outcome.stop_reason else None,
        "iteration_count": len(outcome.iterations),
        "accuracy_report": (
            outcome.final_accuracy.model_dump(mode="json") if outcome.final_accuracy else None
        ),
        "iterations": [it.model_dump(mode="json") for it in outcome.iterations],
        "configuration": outcome.document,
        "complete_config_report": {
            "is_valid": getattr(report, "is_valid", None),
            "contract_valid": getattr(report, "contract_valid", None),
            "error_count": getattr(report, "error_count", None),
            "warning_count": getattr(report, "warning_count", None),
        },
    }


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
@router.get("/metadata")
async def accuracy_loop_metadata():
    """Return Sprint 8 task-coverage metadata for verification."""
    return JSONResponse(
        content={
            "status": "success",
            "deliverable": "8 - Existing API Execution & Development Accuracy Loop",
            "task_coverage": {
                "1_integrate_existing_api": "accuracy_loop.client.ExecutionApiClient",
                "2_configure_api_connection": "accuracy_loop.models.ExecutionApiConfig.from_env",
                "3_prepare_input_file_request": "accuracy_loop.composer.ConfigPushComposer",
                "4_prepare_generated_json_request": "accuracy_loop.composer.ConfigPushComposer.compose",
                "5_send_input_and_json": "accuracy_loop.client.ExecutionApiClient.push",
                "6_handle_validation_failures": "accuracy_loop.client._from_response (errorCode)",
                "7_handle_transformation_failures": "accuracy_loop.client._extract_rejections",
                "8_capture_api_response": "accuracy_loop.models.BlockPushResult",
                "9_retrieve_output_files": "accuracy_loop.output_comparison.OutputLocations (network share -> drop folder)",
                "10_test_input_against_json": "accuracy_loop.output_comparison.compare_output_files",
                "11_compare_with_known_output": "accuracy_loop.comparison.compare_with_known_output",
                "12_identify_validation_mismatches": "accuracy_loop.comparison.build_mismatches (VALIDATION)",
                "13_identify_transformation_mismatches": "accuracy_loop.comparison.build_mismatches (TRANSFORMATION)",
                "14_identify_mapping_mismatches": "accuracy_loop.comparison.build_mismatches (MAPPING)",
                "15_identify_json_configuration_errors": "accuracy_loop.comparison.build_mismatches (CONFIG)",
                "16_define_accuracy_measurement": "accuracy_loop.accuracy.compute_accuracy",
                "17_agent_based_mismatch_analysis": "accuracy_loop.analysis.MismatchAnalyzer.analyze",
                "18_generate_rule_change_suggestions": "accuracy_loop.analysis.MismatchAnalyzer._deterministic",
                "19_update_ir_on_approved_correction": "accuracy_loop.loop.AccuracyLoopController._apply",
                "20_regenerate_affected_json": "complete_config assembler via CompleteConfigService.assemble",
                "21_reapply_json_through_api": "accuracy_loop.loop.AccuracyLoopController.run (re-push)",
                "22_repeat_execution_cycle": "accuracy_loop.loop.AccuracyLoopController.run (loop)",
                "23_max_five_iterations": "accuracy_loop.accuracy.should_stop (MAX_ITERATIONS)",
                "24_95_percent_stopping_criterion": "accuracy_loop.accuracy.should_stop (TARGET_ACCURACY)",
                "25_preserve_iteration_history": "knowledge.iteration_store.save_iteration_history",
            },
            "pending_tasks": {},
            "output_comparison_workflow": {
                "1_backend_writes_output": (
                    "The existing backend/RPA writes the produced output file to the "
                    "network share directory (see GET /accuracy-loop/output-locations)."
                ),
                "2_operator_downloads": (
                    "The operator navigates to the network share, downloads the produced "
                    "output file, and pastes it into the local drop folder "
                    "(data/executed_outputs by default)."
                ),
                "3_compare": (
                    "POST /accuracy-loop/compare-output diffs the produced file against the "
                    "known/expected output (data/expected_outputs) cell-by-cell, or set "
                    "context.compare_output=true on /run to fold it into the final accuracy."
                ),
            },
        }
    )


# ---------------------------------------------------------------------------
# output locations (network share + local drop/expected folders)
# ---------------------------------------------------------------------------
@router.get("/output-locations")
async def output_locations(
    service: AccuracyLoopService = Depends(get_accuracy_loop_service),
):
    """Report + provision the produced-output file locations (tasks 9-11).

    Ensures the local drop/expected folders exist, best-effort creates the
    network share directory the backend writes outputs to, and lists the files
    currently available for comparison.
    """
    locations = service.output_locations
    locations.ensure_local_dirs()
    network_ready, network_message = locations.ensure_network_dir()
    return JSONResponse(
        content={
            "status": "success",
            "network_dir": locations.network_dir,
            "network_ready": network_ready,
            "network_message": network_message,
            "drop_dir": str(locations.drop_dir),
            "expected_dir": str(locations.expected_dir),
            "files": locations.list_files(),
        }
    )


# ---------------------------------------------------------------------------
# compare-output (produced vs known output diff)
# ---------------------------------------------------------------------------
@router.post("/compare-output")
async def compare_output(
    payload: dict[str, Any],
    service: AccuracyLoopService = Depends(get_accuracy_loop_service),
):
    """Diff a produced output file against the known output (tasks 9, 10, 11).

    Body (all optional):
      * ``produced`` - produced filename (in the drop folder) or an explicit path;
        defaults to the newest file in the drop folder.
      * ``known`` - known/expected filename (in the expected folder) or a path.
      * ``sheet`` - worksheet to compare for multi-sheet workbooks.
      * ``options`` - ``{trim_whitespace, case_insensitive, float_tolerance, max_cell_diffs}``.
    """
    produced = payload.get("produced")
    known = payload.get("known")
    sheet = payload.get("sheet")
    options = None
    raw_options = payload.get("options")
    if isinstance(raw_options, dict):
        try:
            options = CompareOptions(**raw_options)
        except TypeError as e:
            raise HTTPException(
                status_code=422,
                detail=build_error(ErrorCode.VALIDATION_ERROR, f"Invalid options: {e}"),
            )

    try:
        report = await run_in_threadpool(
            service.compare_output, produced, known, sheet=sheet, options=options
        )
    except AccuracyLoopError as e:
        logger.warning("[accuracy-loop] output comparison failed: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.OUTPUT_COMPARISON_FAILED, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "match": report.match,
            "output_accuracy": report.output_accuracy,
            "report": report.model_dump(mode="json"),
        }
    )


# ---------------------------------------------------------------------------
# run (model -> initial config -> loop)
# ---------------------------------------------------------------------------
@router.post("/run")
async def run_loop(
    payload: dict[str, Any],
    request: Request,
    service: AccuracyLoopService = Depends(get_accuracy_loop_service),
):
    """Run the full accuracy loop from a posted canonical model (tasks 1-25)."""
    try:
        model = _extract_model(payload)
        context = _parse_context(payload)
    except Exception as e:  # noqa: BLE001 - invalid request body
        logger.warning("[accuracy-loop] Invalid run request: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.VALIDATION_ERROR, str(e)),
        )

    token = _bearer_token(request)
    try:
        result = await run_in_threadpool(service.run, model, context, auth_token=token)
    except AccuracyLoopError as e:
        logger.exception("[accuracy-loop] run failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.ACCURACY_LOOP_FAILED, str(e)),
        )

    body = _outcome_payload(result)
    body["target_accuracy"] = (context or AccuracyLoopContext()).target_accuracy
    return JSONResponse(content={"status": "success", **body})


# ---------------------------------------------------------------------------
# session (ingest -> loop)
# ---------------------------------------------------------------------------
@router.get("/session")
async def run_loop_from_session(
    request: Request,
    service: AccuracyLoopService = Depends(get_accuracy_loop_service),
):
    """Ingest a session then run the accuracy loop end-to-end (tasks 1-25)."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    context = _context_from_query(request)
    token = _bearer_token(request)

    try:
        result = await run_in_threadpool(
            service.run_from_session, session_id, context, auth_token=token
        )
    except (IngestionError, FileParseError, ParserNotImplementedError) as e:
        logger.warning("[accuracy-loop] ingestion failed: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.INGESTION_FAILED, str(e)),
        )
    except AccuracyLoopError as e:
        logger.exception("[accuracy-loop] session run failed")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.ACCURACY_LOOP_FAILED, str(e)),
        )

    body = _outcome_payload(result)
    body["target_accuracy"] = (context or AccuracyLoopContext()).target_accuracy
    return JSONResponse(content={"status": "success", **body})


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
@router.get("/history")
async def loop_history(request: Request):
    """Fetch stored iteration history for a run, or list runs for a scope (25)."""
    bu = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")
    run_id = request.query_params.get("run_id")
    session_id = request.query_params.get("session_id")

    if run_id and session_id:
        history = get_iteration_history(bu, platform, category, session_id, run_id)
        if history is None:
            raise HTTPException(
                status_code=404,
                detail=build_error(ErrorCode.NOT_FOUND, "No iteration history for that run"),
            )
        return JSONResponse(content={"status": "success", "history": history})

    runs = list_iteration_runs(bu, platform, category)
    return JSONResponse(content={"status": "success", "runs": runs})


def _context_from_query(request: Request) -> AccuracyLoopContext:
    """Build a loop context from query params (session convenience)."""
    q = request.query_params

    def _flag(name: str, default: bool) -> bool:
        raw = q.get(name)
        if raw is None:
            return default
        return raw.strip().lower() not in {"0", "false", "no"}

    kwargs: dict[str, Any] = {}
    if q.get("business_unit"):
        kwargs["bu"] = q["business_unit"]
    if q.get("platform"):
        kwargs["platform"] = q["platform"]
    if q.get("category"):
        kwargs["data_category"] = q["category"]
    if q.get("max_iterations"):
        try:
            kwargs["max_iterations"] = int(q["max_iterations"])
        except ValueError:
            pass
    if q.get("target_accuracy"):
        try:
            kwargs["target_accuracy"] = float(q["target_accuracy"])
        except ValueError:
            pass
    kwargs["demo_defects"] = _flag("demo_defects", False)
    kwargs["auto_approve"] = _flag("auto_approve", True)
    return AccuracyLoopContext(**kwargs)


__all__ = ["router"]
