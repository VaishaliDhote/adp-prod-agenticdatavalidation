# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - chat-history seeding for the review/edit experience. REUSABLE.
# Rework: seed summary grouped by file/sheet/column + block identifiers.
# ============================================================================
"""
services.chat_seeding_service
=============================
Encapsulates the chat-history seeding that happens right after a successful
``/get_configuration`` call.

Why this exists
---------------
The UI's "Save Configuration" button calls ``/edit_configuration_chat`` with a
payload like ``"save config"`` right after ``/get_configuration`` succeeds.
If we don't seed, that call hits the first-message branch and shows a
"review summary" prompt instead of running the confirm-and-save pipeline.

Seeding flips the agent's ``is_first_message`` flag to ``False`` so the intent
classifier routes "save"/"confirm" straight to ``save_config``.

For the *other* flow (brand-new session_id, no prior ``/get_configuration``),
nothing is seeded — the existing review-summary behavior is preserved.
"""
from __future__ import annotations

import logging
from typing import Any

from storage.dynamodb.chat_repository import ChatMessage, ChatRepository

logger = logging.getLogger(__name__)


class ChatSeedingService:
    """Insert an initial assistant message into a freshly-generated session."""

    def __init__(self, repo: ChatRepository | None = None) -> None:
        self._repo = repo or ChatRepository()

    def seed_after_generation(
        self,
        *,
        session_id: str,
        business_unit: str,
        platform: str,
        category: str,
        summary: Any,
    ) -> None:
        """
        Idempotently insert one assistant message into ``session_id``'s chat
        history. No-op if the session already has any messages. Never raises;
        failures are logged so config generation is not affected.
        """
        try:
            existing = self._repo.fetch_history(session_id)
            if existing:
                logger.info(
                    "[ChatSeedingService] History already has %d msgs for session=%s — skipping seed.",
                    len(existing), session_id,
                )
                return

            summary_text = (
                summary if isinstance(summary, str) and summary.strip()
                else "Configuration generated successfully."
            )
            seed_reply = (
                "I've generated the transformation configuration. "
                "Here is the summary of the applied transformations:\n\n"
                f"{summary_text}\n\n"
                "Reply 'save config' to confirm and persist these changes, "
                "or describe any edits you'd like to make."
            )
            self._repo.insert(ChatMessage(
                session_id=session_id,
                role="assistant_response",
                message_text=seed_reply,
                bu=business_unit,
                platform=platform,
                category=category,
                config_version="",
            ))
            logger.info(
                "[ChatSeedingService] Seeded chat history for session=%s.",
                session_id,
            )
        except Exception as exc:
            logger.warning(
                "[ChatSeedingService] Could not seed chat history for session=%s: %s",
                session_id, exc,
            )
