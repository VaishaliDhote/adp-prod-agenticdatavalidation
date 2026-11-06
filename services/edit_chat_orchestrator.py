# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Edit-chat orchestration for /edit_configuration_chat (tasks 9-10, 27).
# REUSABLE. Rework: retarget to JSON config + UUID block location.
# ============================================================================
"""
services.edit_chat_orchestrator
================================
All business logic for the ``/edit_configuration_chat`` endpoint.

The route handler is reduced to: parameter validation + ``await
EditChatOrchestrator(...).run(...)`` + ``JSONResponse(result)``.

Responsibilities encapsulated here:
  - Locate the YAML to edit (provided / draft / confirmed) and its source.
  - Persist a debug copy of the YAML for inspection.
  - Resolve the authoritative DynamoDB summary (with structured 404/500s).
  - Branch on first-message-in-session vs. continued conversation.
  - Drive ``run_config_editor_pipeline`` and map its state into a stable
    JSON response shape.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from core.errors import ErrorCode, build_error
from core.paths import AppPaths
from graph_builder import run_config_editor_pipeline
from services.config_reapply_service import ConfigReapplyError, ConfigReapplyService
from services.json_config_editor_service import (
    JsonConfigEditError,
    JsonConfigEditorService,
    get_config_json_with_source,
)
from storage.dynamodb.business_repository import fetch_category_summary
from storage.dynamodb.chat_repository import ChatMessage, ChatRepository

logger = logging.getLogger(__name__)

_VALID_CONFIG_SOURCES = {"provided", "draft", "confirmed", "none"}

# UI contract: the first message of an edit session is one of these two
# trigger phrases. They tell us which entry point the user came from and
# mean "show me the summary, I'll describe changes next" — they are NOT
# real edit requests, so they must not be sent through the editor pipeline.
#
#   "edit generated config[uration]" → came from /getconfig (draft)
#   "edit existing  config[uration]" → opened edit cold (confirmed)
_TRIGGER_GENERATED = re.compile(r"^\s*edit\s+generated\s+config(uration)?\s*[.!?]?\s*$", re.IGNORECASE)
_TRIGGER_EXISTING  = re.compile(r"^\s*edit\s+existing\s+config(uration)?\s*[.!?]?\s*$", re.IGNORECASE)


def _classify_first_message_trigger(user_query: str) -> str | None:
    """Return ``"generated"`` / ``"existing"`` for the two UI trigger phrases,
    or ``None`` if the first message is actually a real edit request."""
    if _TRIGGER_GENERATED.match(user_query or ""):
        return "generated"
    if _TRIGGER_EXISTING.match(user_query or ""):
        return "existing"
    return None


@dataclass
class EditChatRequest:
    """Inputs for one ``/edit_configuration_chat`` call (already sanitized)."""

    session_id: str
    business_unit: str
    platform: str
    category: str
    user_query: str
    current_json: str | None = None
    config_version: str = ""
    auth_token: Optional[str] = None
    reapply: bool = True


class EditChatOrchestrator:
    """Drives the conversational config-editor turn for one request."""

    def __init__(
        self,
        chat_repo: ChatRepository | None = None,
        debug_dir: Path = AppPaths.DEBUG_CONFIG_DIR,
        reapply_service: ConfigReapplyService | None = None,
    ) -> None:
        self._chat_repo = chat_repo or ChatRepository()
        self._debug_dir = debug_dir
        self._reapply_service = reapply_service

    # ── Public entry point ────────────────────────────────────────────────

    async def run(self, req: EditChatRequest) -> dict[str, Any]:
        json_to_edit, config_source = self._resolve_json(req)
        self._save_debug_copy(req.session_id, config_source, json_to_edit)
        config_summary = self._fetch_summary(req, json_to_edit)

        # First-message handling: only the two UI trigger phrases short-circuit
        # to the "review summary" reply. Any other first message is a real edit
        # request and must go through the pipeline so the user isn't ignored.
        if self._is_first_message(req.session_id):
            trigger = _classify_first_message_trigger(req.user_query)
            if trigger is not None:
                return self._handle_first_message(
                    req, config_summary, config_source, trigger=trigger,
                )
            logger.info(
                "[EditChatOrchestrator] First message is not a known trigger "
                "phrase (%r) — routing directly to the editor pipeline.",
                (req.user_query or "")[:80],
            )

        return await self._handle_pipeline_turn(req, json_to_edit, config_source, config_summary)

    # ── Steps ─────────────────────────────────────────────────────────────

    def _resolve_json(self, req: EditChatRequest) -> tuple[str, str]:
        """Return ``(json_to_edit, config_source)`` or raise 404 CONFIG_NOT_FOUND."""
        if req.current_json:
            return req.current_json, "provided"

        info = get_config_json_with_source(
            req.business_unit, req.platform, req.category, req.session_id
        )
        json_to_edit = info.get("json") or ""
        source = info.get("source", "none")
        logger.info(
            "[EditChatOrchestrator] JSON config source=%s length=%d",
            source, len(json_to_edit),
        )

        # Lazy regeneration fallback (task 2 / task 15): if no draft/confirmed
        # JSON exists yet, deterministically regenerate the complete contract
        # from the session and persist it as the session draft, so editing is
        # self-sufficient without a separate generation-time store.
        if not json_to_edit:
            json_to_edit = self._regenerate_and_store(req)
            if json_to_edit:
                source = "regenerated"

        if not json_to_edit:
            raise HTTPException(
                status_code=404,
                detail=build_error(
                    ErrorCode.CONFIG_NOT_FOUND,
                    "No configuration found for this selection. "
                    "Please generate the configuration first from the Generate Configuration tab.",
                    business_unit=req.business_unit,
                    platform=req.platform,
                    category=req.category,
                    session_id=req.session_id,
                ),
            )
        return json_to_edit, source

    def _regenerate_and_store(self, req: EditChatRequest) -> str:
        """Best-effort: regenerate the complete JSON from the session and store it.

        Returns the JSON string on success, or ``""`` when the session cannot be
        ingested/generated (e.g. the input files are not present).
        """
        try:
            from complete_config.models import CompleteConfigContext
            from services.complete_config_service import CompleteConfigService
            from knowledge.config_store import store_draft_json
            import os as _os

            ctx = CompleteConfigContext(
                bu=req.business_unit,
                platform=req.platform,
                data_category=req.category,
            )
            result = CompleteConfigService().generate_from_session(req.session_id, ctx)
            json_content = result.to_json(indent=2)
            try:
                store_draft_json(
                    bu=req.business_unit,
                    platform=req.platform,
                    category=req.category,
                    session_id=req.session_id,
                    json_content=json_content,
                    s3_bucket=_os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs"),
                )
            except Exception as exc:  # noqa: BLE001 - persistence is best-effort
                logger.warning("[EditChatOrchestrator] Could not persist regenerated draft: %s", exc)
            return json_content
        except Exception as exc:  # noqa: BLE001 - regeneration is a fallback only
            logger.info("[EditChatOrchestrator] Could not regenerate config from session: %s", exc)
            return ""

    def _save_debug_copy(self, session_id: str, config_source: str, json_text: str) -> None:
        """Best-effort debug dump of the JSON being edited. Never raises."""
        try:
            session_dir = self._debug_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            safe_source = config_source if config_source in _VALID_CONFIG_SOURCES else "unknown"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_path = session_dir / f"{safe_source}_{timestamp}.json"
            debug_path.write_text(json_text, encoding="utf-8")
            logger.info("[EditChatOrchestrator] Saved debug config to %s", debug_path)
        except Exception as exc:
            logger.warning("[EditChatOrchestrator] Could not save debug config: %s", exc)

    def _fetch_summary(self, req: EditChatRequest, json_to_edit: str) -> str:
        """Return the review summary.

        Prefer the authoritative DynamoDB category summary; if it is missing,
        deterministically build one from the JSON contract itself so the review
        experience works even before the first confirm regenerates the cache.
        """
        try:
            summary = fetch_category_summary(req.business_unit, req.platform, req.category)
        except Exception as exc:
            logger.error("[EditChatOrchestrator] Failed to fetch summary from DynamoDB: %s", exc)
            summary = None

        if summary:
            return summary

        # Deterministic fallback from the JSON contract (tasks 2-8).
        try:
            builder = JsonConfigEditorService(session_id=req.session_id)
            return builder.generate_summary(json_to_edit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EditChatOrchestrator] Could not build JSON summary: %s", exc)
            raise HTTPException(
                status_code=404,
                detail=build_error(
                    ErrorCode.CONFIG_SUMMARY_MISSING,
                    "Configuration has not been generated yet for this selection. "
                    "Please generate the configuration first from the Generate Configuration tab.",
                    business_unit=req.business_unit,
                    platform=req.platform,
                    category=req.category,
                ),
            )

    def _is_first_message(self, session_id: str) -> bool:
        return len(self._chat_repo.fetch_history(session_id)) == 0

    def _handle_first_message(
        self,
        req: EditChatRequest,
        config_summary: str,
        config_source: str,
        trigger: str,
    ) -> dict[str, Any]:
        """Persist the opening turn and return the 'review' response.

        ``trigger`` is ``"generated"`` (came from /getconfig, draft config)
        or ``"existing"`` (opened edit cold, confirmed config). The reply
        wording is tailored to whichever entry point the UI used:

          * ``"generated"`` — the user just saw the freshly generated summary
            on the previous screen, so we do NOT repeat it. We only ask what
            they want to change.
          * ``"existing"``  — the user is opening edit cold and has not seen
            the saved summary yet, so we show it before asking for changes.
        """
        if trigger == "generated":
            assistant_reply = (
                "What changes would you like to make to the generated configuration? "
                "Please describe them and I'll apply them for you."
            )
            include_summary_in_payload = False
        else:  # "existing"
            assistant_reply = (
                "Here is the summary of your existing configuration. Please review it and "
                "let me know what changes you'd like to make.\n\n"
                f"{config_summary}"
            )
            include_summary_in_payload = True

        for role, text in (("user_message", req.user_query), ("assistant_response", assistant_reply)):
            self._chat_repo.insert(ChatMessage(
                session_id=req.session_id,
                role=role,
                message_text=text,
                bu=req.business_unit,
                platform=req.platform,
                category=req.category,
                config_version=req.config_version,
            ))

        return {
            "status": "in_progress",
            "session_id": req.session_id,
            "intent": "review",
            "trigger": trigger,
            "reply": assistant_reply,
            # Only echo the summary in the payload for the "existing" flow.
            # For "generated", the UI already has it from the getconfig response.
            "summary": config_summary if include_summary_in_payload else None,
            "config_source": config_source,
            "has_changes": False,
            "changes": [],
            "affected_columns": [],
            "validation_warnings": [],
            "hint": "Describe the changes you want to apply to the configuration.",
        }

    async def _handle_pipeline_turn(
        self,
        req: EditChatRequest,
        json_to_edit: str,
        config_source: str,
        config_summary: str,
    ) -> dict[str, Any]:
        """Run the LangGraph config-editor pipeline and shape its output."""
        try:
            result_state = await run_config_editor_pipeline(
                bu=req.business_unit,
                platform=req.platform,
                category=req.category,
                session_id=req.session_id,
                user_query=req.user_query,
                current_json=json_to_edit,
                config_version=req.config_version,
            )
        except JsonConfigEditError as exc:
            raise HTTPException(
                status_code=400,
                detail=build_error(
                    ErrorCode.CONFIG_EDITOR_PIPELINE_FAILED,
                    f"Configuration is not editable: {exc}",
                ),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=build_error(
                    ErrorCode.CONFIG_EDITOR_PIPELINE_FAILED,
                    f"Config editor pipeline failed: {exc}",
                ),
            )

        if result_state.get("confirmed"):
            # tasks 25, 26 - reapply the confirmed JSON through the existing API
            # and surface the regenerated-output comparison.
            reapply_payload = await self._reapply(req, result_state.get("proposed_json") or "")
            return {
                "status": "confirmed",
                "session_id": req.session_id,
                "message": f"Configuration saved successfully. {result_state.get('save_status', '')}",
                "reapply": reapply_payload,
            }

        intent = result_state.get("intent", "edit")
        if intent == "cancel":
            return {
                "status": "cancelled",
                "session_id": req.session_id,
                "reply": result_state.get("reply", ""),
                "hint": "Session reset. Your next request will start from the confirmed configuration.",
            }

        proposed = result_state.get("proposed_json") or ""
        has_changes = bool(proposed and proposed != json_to_edit)
        # If the assistant returned a clarifying question (no JSON proposed),
        # don't tell the user to "Reply 'confirm'" — there's nothing to confirm.
        if has_changes:
            hint = "Reply 'confirm' to save changes, or continue editing."
        else:
            hint = "Describe the changes you want to apply to the configuration."

        return {
            "status": "in_progress",
            "session_id": req.session_id,
            "reply": result_state.get("reply", ""),
            "intent": intent,
            "config_source": config_source,
            "has_changes": has_changes,
            "changes": result_state.get("changes", []),
            "changes_display": result_state.get("changes_display", ""),
            "affected_section": result_state.get("affected_section"),
            "affected_uuid": result_state.get("affected_uuid"),
            "affected_columns": result_state.get("affected_columns", []),
            "validation_warnings": result_state.get("validation_warnings", []),
            "summary": config_summary or result_state.get("summary"),
            "hint": hint,
        }

    # ── Reapply (tasks 25, 26) ─────────────────────────────────────────────

    async def _reapply(self, req: EditChatRequest, confirmed_json: str) -> dict[str, Any] | None:
        """Push the confirmed JSON through the existing API; never breaks the save."""
        if not req.reapply or not confirmed_json:
            return None
        try:
            service = self._reapply_service or ConfigReapplyService()
            editor = JsonConfigEditorService(session_id=req.session_id)
            contract = editor.to_contract(confirmed_json)
            result = await run_in_threadpool(
                service.reapply,
                contract,
                bu=req.business_unit,
                platform=req.platform,
                category=req.category,
                session_id=req.session_id,
                auth_token=req.auth_token,
            )
            return result.to_payload()
        except ConfigReapplyError as exc:
            logger.warning("[EditChatOrchestrator] Reapply failed: %s", exc)
            return {"success": False, "message": f"Reapply failed: {exc}"}
        except Exception as exc:  # noqa: BLE001 - reapply must never break the save
            logger.warning("[EditChatOrchestrator] Reapply error: %s", exc)
            return {"success": False, "message": f"Reapply error: {exc}"}
