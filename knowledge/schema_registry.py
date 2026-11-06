# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2/3 - Column schema metadata (expected columns/types/mandatory,
# column-relations). REUSABLE.
# Rework: align schema fields with the exact JSON contract (Deliverable 2).
# ============================================================================
"""
knowledge/schema_registry.py
------------------------------
Stores column schema metadata derived during config generation.
Keyed by BU/Platform/Category so schemas are reusable across sessions.

Schema metadata includes: column names, data types, mandatory flags, allowed values.
Stored in DocumentsTable under PK=SCHEMA#<bu>#<platform>#<category>.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)

TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pk(bu: str, platform: str, category: str) -> str:
    return f"SCHEMA#{bu}#{platform}#{category}"


def save_schema(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    schema: dict[str, Any],
) -> None:
    """
    Upsert schema metadata for a BU/Platform/Category scope.
    schema: dict of {column_name: {dataType, isMandatory, allowedValues, ...}}
    """
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")

    get_table(TABLE).put_item(Item={
        "PK":         _pk(bu, platform, category),
        "SK":         "SCHEMA_META",
        "bu":         bu,
        "platform":   platform,
        "category":   category,
        "session_id": session_id,
        "schema":     schema,
        "is_active":  True,
        "created_at": _now(),
        "updated_at": None,
    })
    logger.info("Saved schema bu=%s platform=%s category=%s", bu, platform, category)


def fetch_schema(bu: str, platform: str, category: str) -> dict[str, Any] | None:
    """
    Fetch schema metadata for a BU/Platform/Category.
    Returns schema dict or None if not found.
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")

    resp = get_table(TABLE).get_item(Key={
        "PK": _pk(bu, platform, category),
        "SK": "SCHEMA_META",
    })
    item = resp.get("Item")
    if not item or not item.get("is_active", False):
        logger.debug("Schema not found bu=%s platform=%s category=%s", bu, platform, category)
        return None
    return item.get("schema")


def schema_exists(bu: str, platform: str, category: str) -> bool:
    return fetch_schema(bu, platform, category) is not None
