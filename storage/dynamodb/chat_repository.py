# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Edit conversation history store (task 27). REUSABLE as-is.
# ============================================================================
"""
storage/dynamodb/chat_repository.py
=====================================
ChatMessageDvtsTable access layer.

Table design
------------
  PK  = session_id   (String) — one partition per chat session
  SK  = created_at   (String) — ISO 8601 + ULID suffix
                                e.g. 2025-01-15T10:30:00.123456+00:00#01ARZ3NDEKTSV4RRFFQ69G5FAV
                                Guarantees strict sort order AND uniqueness
                                even when two messages arrive in the same millisecond.

Why ULID suffix
---------------
  Pure ISO timestamp SK collides if two PutItem calls land in the same
  microsecond (e.g. user + assistant stored back-to-back in the same turn).
  ULID is time-sortable and URL-safe — appending it after '#' keeps the
  SK lexicographically ordered by time while guaranteeing uniqueness.

Access patterns
---------------
  /edit_config load history  → Query PK=<session_id>, ScanIndexForward=True
  /edit_config store message → PutItem PK=<session_id>, SK=<iso>#<ulid>
"""
from __future__ import annotations

import logging
import os
import time
import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from boto3.dynamodb.conditions import Key

from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)

TABLE = os.getenv("CHAT_MESSAGE_TABLE", "ChatMessageDvtsTable")

_ULID_CHARS = string.ascii_uppercase + string.digits


# ── Value object ──────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    """Represents one row in ChatMessageDvtsTable."""
    session_id:     str
    role:           str            # "user_message" | "assistant_response"
    message_text:   str
    bu:             str = ""
    platform:       str = ""
    category:       str = ""
    config_version: str = ""       # which YAML version this turn was based on


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ulid() -> str:
    """
    Generate a 26-char ULID-like string (time-sortable, URL-safe).
    Uses millisecond timestamp (10 chars) + 16 random chars.
    """
    ts = int(time.time() * 1000)
    time_chars = []
    for _ in range(10):
        ts, rem = divmod(ts, 32)
        time_chars.append(_ULID_CHARS[rem])
    time_part   = "".join(reversed(time_chars))
    random_part = "".join(random.choices(_ULID_CHARS, k=16))
    return time_part + random_part


def _make_sk() -> str:
    """SK = ISO 8601 UTC timestamp + '#' + ULID — sortable and unique."""
    iso = datetime.now(timezone.utc).isoformat()
    return f"{iso}#{_ulid()}"


# ── Repository ────────────────────────────────────────────────────────────────

class ChatRepository:
    """
    All DynamoDB operations for ChatMessageDvtsTable.

    Usage:
        repo = ChatRepository()
        repo.insert(ChatMessage(session_id=..., role="user_message", message_text=...))
        history = repo.fetch_history(session_id)
    """

    def insert(self, message: ChatMessage) -> None:
        """
        PutItem — store one chat message.
        SK is auto-generated as <ISO_timestamp>#<ULID> to guarantee
        strict ordering and uniqueness within a session partition.
        """
        session_id = sanitize_key(message.session_id, "session_id")
        get_table(TABLE).put_item(Item={
            "session_id":     session_id,
            "created_at":     _make_sk(),
            "role":           message.role,
            "message_text":   message.message_text,
            "bu":             message.bu,
            "platform":       message.platform,
            "category":       message.category,
            "config_version": message.config_version,
        })
        logger.debug("Inserted chat message role=%s session=%s", message.role, session_id)

    def fetch_history(self, session_id: str) -> list[dict]:
        """
        Query PK = <session_id>, ScanIndexForward=True (ASC) —
        returns full conversation oldest-first, shaped for LLM context.
        """
        session_id = sanitize_key(session_id, "session_id")
        resp = get_table(TABLE).query(
            KeyConditionExpression=Key("session_id").eq(session_id),
            ScanIndexForward=True,
        )
        return [
            {"role": item["role"], "content": item["message_text"]}
            for item in resp.get("Items", [])
        ]
