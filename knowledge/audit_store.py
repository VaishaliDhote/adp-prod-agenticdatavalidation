# ============================================================================
# Deliverable 10 - Audit trail (task 25).
#
# Records significant workflow events - config generation, storage, approval,
# user edits, execution attempts, and API results - so the configuration
# lifecycle is fully traceable. Follows the DynamoDB DocumentsTable pattern used
# by knowledge/iteration_store.py (PK=AUDIT#<bu>#<platform>#<category>), and
# degrades gracefully to an in-process cache when AWS is unreachable so audit
# writes never break the request path (or offline tests).
# ============================================================================
"""
knowledge/audit_store.py
------------------------
Persist and retrieve DVTS audit events (Deliverable 10, task 25).

Stored in ``DocumentsTable`` under ``PK=AUDIT#<bu>#<platform>#<category>`` and
``SK=EVT#<utc-iso>#<ulid>`` so events sort chronologically. Each item captures
the event type, actor, and a JSON-serialisable detail payload.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")

# In-process fallback cache: PK -> list[item]. Used when DynamoDB is unavailable
# (offline dev / CI) so recent events are still queryable in-process.
_LOCAL_CACHE: dict[str, list[dict[str, Any]]] = {}

# Stable audit event-type vocabulary (extend as needed).
EVENT_CONFIG_GENERATED = "CONFIG_GENERATED"
EVENT_CONFIG_DRAFT_SAVED = "CONFIG_DRAFT_SAVED"
EVENT_CONFIG_CONFIRMED = "CONFIG_CONFIRMED"
EVENT_CONFIG_APPROVED = "CONFIG_APPROVED"
EVENT_CONFIG_EDITED = "CONFIG_EDITED"
EVENT_EXECUTION_ATTEMPTED = "EXECUTION_ATTEMPTED"
EVENT_EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
EVENT_EXECUTION_FAILED = "EXECUTION_FAILED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to ``Decimal`` (DynamoDB rejects floats)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert DynamoDB ``Decimal`` back to int/float (JSON-safe)."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_from_dynamo(v) for v in value]
    return value


def _pk(bu: str, platform: str, category: str) -> str:
    return f"AUDIT#{bu}#{platform}#{category}"


def record_audit_event(
    bu: str,
    platform: str,
    category: str,
    event_type: str,
    *,
    actor: str = "",
    session_id: str = "",
    detail: dict[str, Any] | None = None,
) -> bool:
    """Record one audit event (task 25).

    Returns ``True`` when written to DynamoDB, ``False`` when only the local
    fallback cache was updated. Never raises on persistence failure.
    """
    pk = _pk(bu, platform, category)
    sk = f"EVT#{_now()}#{uuid.uuid4().hex[:12]}"
    item = {
        "PK": pk,
        "SK": sk,
        "record_type": "AUDIT_EVENT",
        "bu": bu,
        "platform": platform,
        "category": category,
        "event_type": event_type,
        "actor": actor,
        "session_id": session_id,
        "detail": detail or {},
        "created_at": _now(),
    }
    _LOCAL_CACHE.setdefault(pk, []).append(item)

    try:
        from storage.dynamodb.client import get_table, sanitize_key

        safe_item = dict(item)
        safe_item["PK"] = _pk(
            sanitize_key(bu, "bu"),
            sanitize_key(platform, "platform"),
            sanitize_key(category, "category"),
        )
        get_table(TABLE).put_item(Item=_to_dynamo(safe_item))
        logger.info("Recorded audit event=%s bu=%s platform=%s category=%s", event_type, bu, platform, category)
        return True
    except Exception as exc:  # noqa: BLE001 - audit must never break the request path
        logger.warning("Audit event persisted to local cache only (DynamoDB unavailable): %s", exc)
        return False


def list_audit_events(
    bu: str,
    platform: str,
    category: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List recent audit events for a scope, newest first (DynamoDB then cache)."""
    pk = _pk(bu, platform, category)
    try:
        from boto3.dynamodb.conditions import Key
        from storage.dynamodb.client import get_table, sanitize_key

        resp = get_table(TABLE).query(
            KeyConditionExpression=Key("PK").eq(
                _pk(
                    sanitize_key(bu, "bu"),
                    sanitize_key(platform, "platform"),
                    sanitize_key(category, "category"),
                )
            ) & Key("SK").begins_with("EVT#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
        if items:
            return [_from_dynamo(i) for i in items]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audit event listing fell back to local cache: %s", exc)

    cached = sorted(
        _LOCAL_CACHE.get(pk, []),
        key=lambda i: i.get("SK", ""),
        reverse=True,
    )
    return cached[:limit]


__all__ = [
    "record_audit_event",
    "list_audit_events",
    "EVENT_CONFIG_GENERATED",
    "EVENT_CONFIG_DRAFT_SAVED",
    "EVENT_CONFIG_CONFIRMED",
    "EVENT_CONFIG_APPROVED",
    "EVENT_CONFIG_EDITED",
    "EVENT_EXECUTION_ATTEMPTED",
    "EVENT_EXECUTION_SUCCEEDED",
    "EVENT_EXECUTION_FAILED",
]
