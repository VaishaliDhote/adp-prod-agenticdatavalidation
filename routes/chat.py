# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - /edit_configuration_chat endpoint. REUSABLE.
# ============================================================================
"""Config-editor chat route: ``/edit_configuration_chat``."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.validation import require_param, sanitize_business_keys
from services.edit_chat_orchestrator import EditChatOrchestrator, EditChatRequest

router = APIRouter(tags=["chat"])


@router.post("/edit_configuration_chat")
async def edit_configuration_chat(request: Request):
    """Edit configuration via conversational chat (exact JSON contract)."""
    session_id = require_param(request.query_params.get("session_id"), "session_id")
    business_unit = require_param(request.query_params.get("business_unit"), "business_unit")
    platform = require_param(request.query_params.get("platform"), "platform")
    category = require_param(request.query_params.get("category"), "category")
    user_query = require_param(request.query_params.get("user_query"), "user_query")

    clean = sanitize_business_keys(
        session_id=session_id,
        business_unit=business_unit,
        platform=platform,
        category=category,
    )

    # Accept ``current_json`` (preferred). ``current_yaml`` is still read for
    # backward compatibility with older clients but is treated as JSON content.
    current_json = (
        request.query_params.get("current_json")
        or request.query_params.get("current_yaml")
    )

    # Optionally disable the reapply-through-API step (default on).
    reapply_param = (request.query_params.get("reapply") or "").strip().lower()
    reapply = reapply_param not in {"0", "false", "no", "off"}

    auth = request.headers.get("Authorization", "")
    auth_token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None

    edit_req = EditChatRequest(
        session_id=clean["session_id"],
        business_unit=clean["business_unit"],
        platform=clean["platform"],
        category=clean["category"],
        user_query=user_query,
        current_json=current_json,
        config_version=request.query_params.get("config_version", "") or "",
        auth_token=auth_token,
        reapply=reapply,
    )

    result = await EditChatOrchestrator().run(edit_req)
    return JSONResponse(result)
