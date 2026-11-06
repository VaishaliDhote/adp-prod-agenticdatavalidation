# ============================================================================
# Deliverable 1 - Sprint 1 canonical-model ingestion route. REUSABLE.
# Exposes IngestionService over HTTP so the canonical input/output analysis
# model can be verified against the live server (no test scripts required).
# ============================================================================
"""Ingestion routes: ``/ingest_session``.

Runs the Deliverable 1 ingestion pipeline for a session's uploaded input/output
files and returns the unified :class:`CanonicalAnalysisModel` as JSON, so the
canonical model can be inspected/verified directly against the running API.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from core.dependencies import get_ingestion_service
from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.ingestion_service import IngestionService
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingestion"])


@router.get("/ingest_session")
async def ingest_session(
    request: Request,
    service: IngestionService = Depends(get_ingestion_service),
):
    """Ingest a session's input/output files into the canonical analysis model.

    Query params:
        session_id: The session whose files live under the standard
            ``<input_base>/<session_id>`` and ``<output_base>/<session_id>``
            directory layout.

    Returns:
        The serialized :class:`CanonicalAnalysisModel` plus a lightweight
        counts summary for quick verification.
    """
    session_id = require_param(request.query_params.get("session_id"), "session_id")

    try:
        # ingest_session is synchronous/CPU+IO bound; keep the event loop free.
        model = await run_in_threadpool(service.ingest_session, session_id)

        payload = model.model_dump(mode="json")
        summary = {
            "input_file_count": len(model.input_files),
            "output_file_count": len(model.output_files),
            "relationship_count": len(model.relationships),
            "input_files": [
                {"name": f.name, "sheets": len(f.sheets)} for f in model.input_files
            ],
            "output_files": [
                {"name": f.name, "sheets": len(f.sheets)} for f in model.output_files
            ],
        }

        return JSONResponse(
            content={
                "status": "success",
                "session_id": session_id,
                "summary": summary,
                "canonical_model": payload,
            }
        )
    except ParserNotImplementedError as e:
        logger.warning("[ingest_session] Unsupported format for session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except FileParseError as e:
        logger.warning("[ingest_session] Parse error for session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except FileNotFoundError as e:
        logger.warning("[ingest_session] File not found for session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=404,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except IngestionError as e:
        logger.exception("[ingest_session] Ingestion failed for session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.INGESTION_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[ingest_session] Unexpected error for session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.INGESTION_FAILED, str(e)),
        )
