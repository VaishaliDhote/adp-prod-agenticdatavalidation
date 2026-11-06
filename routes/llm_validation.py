# ============================================================================
# LLM-driven validation-rules generation route.
#
# Exposes ONLY the LLM validation flow and returns ONLY the compiled
# ``validation-rules`` JSON array (nothing else - no IR, no block report, no
# raw reply). Given a session id it ingests the session's input files, asks the
# LLM which validations each column needs, compiles the exact
# ``validation-rules`` block and returns just that array.
# ============================================================================
"""LLM validation-rules routes: ``/llm-validation/*``.

* ``GET /llm-validation/session`` - ingest a session's input files, run LLM
  validation detection, compile the ``validation-rules`` JSON and return ONLY
  that array.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.errors import ErrorCode, build_error
from core.validation import require_param
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from services.llm_validation_service import (
    LLMValidationError,
    LLMValidationService,
)
from validation_ir.models import ValidationGenerationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-validation", tags=["llm-validation"])


def _context_from_query(request: Request) -> ValidationGenerationContext:
    """Build an optional generation context from query params.

    Only the fields present are overridden; the rest keep their model defaults.
    """
    params = request.query_params
    # useremail is sourced from the authenticated user's token when available
    # (set by GraphAuthMiddleware as request.state.user_email = userPrincipalName);
    # an explicit ?useremail= query override is used only as a fallback. userrole
    # is intentionally NOT overridable here - it stays static ('agent_role').
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
    return ValidationGenerationContext(**overrides)


@router.get("/session")
async def validation_rules_from_session(request: Request):
    """Ingest a session then return ONLY the compiled ``validation-rules`` JSON.

    Query params:
        session_id: The session whose input files live under
            ``<input_base>/<session_id>`` (required).
        bu, platform, dataCategory, validationRuleType: Optional identity
            overrides for the generated block. ``useremail`` is taken from the
            authenticated token (falling back to a ``?useremail=`` override);
            ``userrole`` is static ('agent_role').
    """
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    context = _context_from_query(request)

    service = LLMValidationService()
    try:
        result = await service.generate_from_session(
            session_id,
            context=context,
            print_reply=True,
        )
        return JSONResponse(content={"validation-rules": result.rows})
    except ParserNotImplementedError as e:
        logger.warning("[llm-validation] Unsupported format session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.PARSER_NOT_IMPLEMENTED, str(e)),
        )
    except (FileParseError, FileNotFoundError) as e:
        logger.warning("[llm-validation] Parse/not-found session=%s: %s", session_id, e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.FILE_PARSE_FAILED, str(e)),
        )
    except (IngestionError, LLMValidationError) as e:
        logger.exception("[llm-validation] Failed session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )
    except Exception as e:  # noqa: BLE001 - final safety net -> unified error
        logger.exception("[llm-validation] Unexpected error session=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.VALIDATION_IR_FAILED, str(e)),
        )
