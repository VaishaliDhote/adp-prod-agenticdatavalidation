# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 3 - Validation Rule Inventory persistence (task 22). REUSABLE.
# Rework: store the identified validation inventory (with API-support flags and
# source-column UUIDs), decoupled from the removed execution-time enforcement.
# ============================================================================
"""
knowledge/rules_store.py
-------------------------
Stores reusable validation rules per BU/Platform/Category.
Rules are derived during config generation and reused during execution validation.
Stored in DocumentsTable under PK=RULES#<bu>#<platform>#<category>.
Each rule set is versioned by session so history is preserved.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key, Attr

from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)

TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pk(bu: str, platform: str, category: str) -> str:
    return f"RULES#{bu}#{platform}#{category}"


def save_rules(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    rules: dict[str, Any],
) -> None:
    """
    Save validation rules for a BU/Platform/Category scope.
    Overwrites existing rules for the same session.
    rules: dict of {column_name: {dataType, isMandatory, minLength, maxLength, ...}}
    """
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")

    get_table(TABLE).put_item(Item={
        "PK":         _pk(bu, platform, category),
        "SK":         f"RULES#{session_id}",
        "bu":         bu,
        "platform":   platform,
        "category":   category,
        "session_id": session_id,
        "rules":      rules,
        "is_active":  True,
        "created_at": _now(),
        "updated_at": None,
    })
    logger.info("Saved rules bu=%s platform=%s category=%s session=%s", bu, platform, category, session_id)


def fetch_latest_rules(bu: str, platform: str, category: str) -> dict[str, Any] | None:
    """
    Fetch the most recently saved rules for a BU/Platform/Category.
    Returns rules dict or None.
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")

    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(_pk(bu, platform, category)) & Key("SK").begins_with("RULES#"),
        FilterExpression=Attr("is_active").eq(True),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    logger.debug("fetch_latest_rules found=%s bu=%s", bool(items), bu)
    return items[0].get("rules") if items else None


def fetch_rules_by_session(bu: str, platform: str, category: str, session_id: str) -> dict[str, Any] | None:
    """Fetch rules saved for a specific session."""
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")

    resp = get_table(TABLE).get_item(Key={
        "PK": _pk(bu, platform, category),
        "SK": f"RULES#{session_id}",
    })
    item = resp.get("Item")
    if not item or not item.get("is_active", False):
        return None
    return item.get("rules")
