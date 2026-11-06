# ============================================================================
# Deliverable 9 - Targeted-editing agent (JSON contract).
# Orchestrates one conversational editing turn against the exact JSON contract:
# classify intent (reuse IntentClassificationService), handle confirm/cancel, and
# drive JsonConfigEditorService for edit/clarify/question turns. On a successful
# edit it appends a confirmation prompt; on confirm it saves the confirmed JSON.
# ============================================================================
"""
agents.json_config_editor_agent
===============================
Agent for conversational, UUID-anchored JSON configuration editing.
"""
from __future__ import annotations

import logging
from typing import Any

from services.intent_classification_service import (
    IntentClassificationService,
    UserIntent,
)
from services.json_config_editor_service import JsonConfigEditorService

logger = logging.getLogger(__name__)


class JsonConfigEditorAgent:
    """Coordinate intent classification + JSON edit for one turn."""

    def __init__(self, session_id: str = "") -> None:
        self._service = JsonConfigEditorService(session_id=session_id)
        self._intent_service = IntentClassificationService(session_id=session_id)
        self.session_id = session_id

    async def run(
        self,
        bu: str,
        platform: str,
        category: str,
        user_query: str,
        current_json: str,
        config_version: str = "",
    ) -> dict[str, Any]:
        """Run one editing turn and return a stable result dict."""
        history = self._service._safe_history()

        intent = await self._intent_service.classify(
            user_query=user_query,
            conversation_history=history,
            use_llm_fallback=True,
        )
        logger.info("[JsonConfigEditorAgent] intent=%s session=%s", intent, self.session_id)

        if intent == UserIntent.CONFIRM:
            return self._build_response(
                reply="CONFIRMED",
                confirmed=True,
                proposed_json=current_json,
                intent=intent.value,
            )

        if intent == UserIntent.CANCEL:
            return self._build_response(
                reply="Changes cancelled. Starting fresh from the confirmed configuration.",
                confirmed=False,
                proposed_json="",  # signals reload from confirmed
                intent=intent.value,
            )

        # EDIT / CLARIFY / QUESTION
        result = await self._service.process_turn(
            bu=bu,
            platform=platform,
            category=category,
            user_query=user_query,
            current_json=current_json,
            config_version=config_version,
        )
        result["intent"] = intent.value
        result.setdefault("validation_warnings", [])

        # Append a confirmation prompt only when a concrete change was proposed.
        if result.get("proposed_json") and result["proposed_json"] != current_json:
            result["reply"] = self._append_confirmation_prompt(result["reply"])
        return result

    async def save(self, bu: str, platform: str, category: str, json_content: str) -> str:
        """Persist the confirmed JSON configuration."""
        return await self._service.save_confirmed_config(bu, platform, category, json_content)

    def to_contract(self, json_content: str):
        """Parse an edited JSON document into a typed contract (for reapply)."""
        return self._service.to_contract(json_content)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _build_response(
        reply: str,
        confirmed: bool,
        proposed_json: str,
        intent: str,
    ) -> dict[str, Any]:
        return {
            "reply": reply,
            "confirmed": confirmed,
            "proposed_json": proposed_json,
            "changes": [],
            "changes_display": "",
            "affected_section": None,
            "affected_uuid": None,
            "affected_columns": [],
            "intent": intent,
            "validation_warnings": [],
        }

    @staticmethod
    def _append_confirmation_prompt(reply: str) -> str:
        return (
            reply
            + "\n\n---\n\n"
            + "**Is this what you meant?** Reply 'confirm' to save these changes, "
            + "or let me know what adjustments you'd like to make."
        )


__all__ = ["JsonConfigEditorAgent"]
