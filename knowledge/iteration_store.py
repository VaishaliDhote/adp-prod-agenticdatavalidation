# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Iteration-history persistence (task 25).
#
# Preserves the full per-run accuracy-loop history per BU/Platform/Category so a
# run can be audited and resumed. Follows the DynamoDB ``DocumentsTable`` pattern
# used by the other knowledge stores (PK=ITERATIONS#<bu>#<platform>#<category>),
# but degrades gracefully to an in-process cache when AWS is unreachable so the
# loop (and its offline tests) never fail on persistence alone.
# ============================================================================
"""
knowledge/iteration_store.py
----------------------------
Persist and retrieve accuracy-loop iteration history (Deliverable 8, task 25).

Stored in ``DocumentsTable`` under ``PK=ITERATIONS#<bu>#<platform>#<category>``
and ``SK=ITER#<session_id>#<run_id>``. Each item is a JSON-serialisable run
summary: final accuracy, stop reason, and the ordered iteration records.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")

# In-process fallback cache: PK -> {SK -> item}. Used when DynamoDB is
# unavailable (e.g. offline dev / CI) so history is still queryable in-process.
_LOCAL_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to ``Decimal`` (DynamoDB rejects floats).

    Floats are stringified before ``Decimal`` construction to avoid binary
    floating-point artefacts (e.g. ``0.1`` -> a long inexact Decimal).
    """
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
    """Recursively convert DynamoDB ``Decimal`` values back to int/float.

    Ensures the history returned to the API layer is plain-JSON serialisable
    (``JSONResponse`` cannot serialise ``Decimal``).
    """
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_from_dynamo(v) for v in value]
    return value


def _pk(bu: str, platform: str, category: str) -> str:
    return f"ITERATIONS#{bu}#{platform}#{category}"


def _sk(session_id: str, run_id: str) -> str:
    return f"ITER#{session_id}#{run_id}"


def save_iteration_history(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    run_id: str,
    history: dict[str, Any],
) -> bool:
    """Persist one accuracy-loop run's history (task 25).

    Returns ``True`` when written to DynamoDB, ``False`` when only the local
    fallback cache was updated (never raises on persistence failure).
    """
    pk, sk = _pk(bu, platform, category), _sk(session_id, run_id)
    item = {
        "PK": pk,
        "SK": sk,
        "bu": bu,
        "platform": platform,
        "category": category,
        "session_id": session_id,
        "run_id": run_id,
        "history": history,
        "created_at": _now(),
    }
    _LOCAL_CACHE.setdefault(pk, {})[sk] = item

    try:
        from storage.dynamodb.client import get_table, sanitize_key

        safe_item = dict(item)
        safe_item["PK"] = _pk(
            sanitize_key(bu, "bu"),
            sanitize_key(platform, "platform"),
            sanitize_key(category, "category"),
        )
        safe_item["SK"] = _sk(sanitize_key(session_id, "session_id"), sanitize_key(run_id, "run_id"))
        get_table(TABLE).put_item(Item=_to_dynamo(safe_item))
        logger.info("Saved iteration history bu=%s platform=%s category=%s run=%s", bu, platform, category, run_id)
        return True
    except Exception as exc:  # noqa: BLE001 - persistence must never break the loop
        logger.warning("Iteration history persisted to local cache only (DynamoDB unavailable): %s", exc)
        return False


def get_iteration_history(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    run_id: str,
) -> Optional[dict[str, Any]]:
    """Fetch a single run's history, preferring DynamoDB then local cache."""
    pk, sk = _pk(bu, platform, category), _sk(session_id, run_id)
    try:
        from storage.dynamodb.client import get_table, sanitize_key

        resp = get_table(TABLE).get_item(
            Key={
                "PK": _pk(
                    sanitize_key(bu, "bu"),
                    sanitize_key(platform, "platform"),
                    sanitize_key(category, "category"),
                ),
                "SK": _sk(sanitize_key(session_id, "session_id"), sanitize_key(run_id, "run_id")),
            }
        )
        item = resp.get("Item")
        if item:
            return _from_dynamo(item.get("history"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Iteration history fetch fell back to local cache: %s", exc)

    cached = _LOCAL_CACHE.get(pk, {}).get(sk)
    return cached.get("history") if cached else None


def list_iteration_runs(
    bu: str,
    platform: str,
    category: str,
) -> list[dict[str, Any]]:
    """List run summaries for a scope (local cache; DynamoDB query optional)."""
    pk = _pk(bu, platform, category)
    runs: list[dict[str, Any]] = []
    for item in _LOCAL_CACHE.get(pk, {}).values():
        history = item.get("history", {})
        runs.append({
            "run_id": item.get("run_id"),
            "session_id": item.get("session_id"),
            "created_at": item.get("created_at"),
            "final_accuracy": history.get("final_accuracy"),
            "stop_reason": history.get("stop_reason"),
            "iteration_count": history.get("iteration_count"),
        })
    return runs


__all__ = ["save_iteration_history", "get_iteration_history", "list_iteration_runs"]
