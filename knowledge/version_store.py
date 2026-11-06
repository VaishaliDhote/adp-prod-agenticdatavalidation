# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2 - Config version tracking. REUSABLE as-is for JSON config versions.
# ============================================================================
"""
knowledge/version_store.py
---------------------------
Tracks config version numbers per BU/Platform/Category scope.
Stores version counter in DynamoDB DocumentsTable under a dedicated VERSION_COUNTER item.

Version naming: default → v1 → v2 → v3 ...
Confirmed versions are timestamped separately by config_store.
"""
from __future__ import annotations

import logging
import os

from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)

TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")


def _counter_pk(bu: str, platform: str, category: str) -> str:
    return f"VERSION_COUNTER#{bu}#{platform}#{category}"


def get_next_version(bu: str, platform: str, category: str, session_id: str) -> str:
    """
    Atomically increment and return the next draft version string.
    First call returns 'v1', second 'v2', etc.
    Uses DynamoDB atomic ADD expression — safe under concurrent edits.
    """
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")

    resp = get_table(TABLE).update_item(
        Key={
            "PK": _counter_pk(bu, platform, category),
            "SK": f"SESSION#{session_id}",
        },
        UpdateExpression="ADD version_counter :inc SET bu = :bu, platform = :p, category = :c, session_id = :s",
        ExpressionAttributeValues={
            ":inc": 1,
            ":bu":  bu,
            ":p":   platform,
            ":c":   category,
            ":s":   session_id,
        },
        ReturnValues="UPDATED_NEW",
    )
    counter = int(resp["Attributes"]["version_counter"])
    version = f"v{counter}"
    logger.info("Next version=%s bu=%s platform=%s category=%s session=%s", version, bu, platform, category, session_id)
    return version


def get_current_version(bu: str, platform: str, category: str, session_id: str) -> str:
    """
    Returns the current version string without incrementing.
    Returns 'default' if no versions exist yet.
    """
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")

    resp = get_table(TABLE).get_item(Key={
        "PK": _counter_pk(bu, platform, category),
        "SK": f"SESSION#{session_id}",
    })
    item    = resp.get("Item")
    counter = int(item.get("version_counter", 0)) if item else 0
    return f"v{counter}" if counter > 0 else "default"


def list_versions(bu: str, platform: str, category: str, session_id: str) -> list[str]:
    """
    Returns all draft version strings for a session in order: ['default', 'v1', 'v2', ...]
    """
    current = get_current_version(bu, platform, category, session_id)
    if current == "default":
        return ["default"]
    count = int(current[1:])  # strip 'v'
    return ["default"] + [f"v{i}" for i in range(1, count + 1)]
