# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - Graph token store (access/security support, task 23). REUSABLE as-is.
# ============================================================================
"""
storage/dynamodb/token_repository.py
======================================
GraphTokenDvtsTable access layer.

Table design
------------
  PK  = token          (String) — HMAC-SHA256 hash of the raw Bearer token
  GSI = SessionTokenIndex  PK: session_id  SK: created_at
  GSI = UserTokenIndex     PK: user_id     SK: created_at  (optional — see fetch_by_user)

TTL
---
  `ttl` (Number) — Unix epoch seconds — DynamoDB auto-expiry.
  `expires_at` (String) — ISO 8601 — human-readable, used by service layer.
"""
from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)

TABLE        = os.getenv("GRAPH_TOKEN_TABLE", "GraphTokenDvtsTable")
_HMAC_SECRET = os.getenv("TOKEN_HMAC_SECRET", "change-me-in-production").encode()


# ── Value object ──────────────────────────────────────────────────────────────

@dataclass
class TokenRecord:
    """Represents one row in GraphTokenDvtsTable (pre-hash)."""
    raw_token:  str
    session_id: str
    user_id:    str
    user_name:  str
    user_email: str
    expires_at: str   # ISO 8601
    is_active:  bool = True


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hash(raw_token: str) -> str:
    """HMAC-SHA256 of the raw token — raw token is never stored."""
    return hmac.new(_HMAC_SECRET, raw_token.encode("utf-8"), digestmod="sha256").hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_epoch(iso: str) -> int:
    """Convert ISO 8601 string to Unix epoch int for DynamoDB TTL."""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _record_from_item(item: dict) -> TokenRecord:
    """Reconstruct a TokenRecord from a DynamoDB item."""
    return TokenRecord(
        raw_token  = item["token"],        # stored as hash — not the raw token
        session_id = item.get("session_id", ""),
        user_id    = item.get("user_id", ""),
        user_name  = item.get("user_name", ""),
        user_email = item.get("user_email", ""),
        expires_at = item.get("expires_at", ""),
        is_active  = item.get("is_active", False),
    )


# ── Repository ────────────────────────────────────────────────────────────────

class TokenRepository:
    """All DynamoDB operations for GraphTokenDvtsTable."""

    def fetch_by_token(self, raw_token: str) -> TokenRecord | None:
        """
        O(1) GetItem by hashed token.
        Returns None if not found.
        """
        token_hash = _hash(raw_token)
        logger.info("[fetch_by_token] START table=%s hash_prefix=%s...", TABLE, token_hash[:8])
        try:
            resp = get_table(TABLE).get_item(Key={"token": token_hash})
            item = resp.get("Item")
            if not item:
                logger.info("[fetch_by_token] RESULT token not found in table=%s", TABLE)
                return None
            record = _record_from_item(item)
            logger.info(
                "[fetch_by_token] RESULT found user_id=%s session_id=%s is_active=%s expires_at=%s",
                record.user_id, record.session_id, record.is_active, record.expires_at,
            )
            return record
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            logger.error("[fetch_by_token] ERROR ClientError code=%s message=%s table=%s", code, msg, TABLE)
            raise
        except Exception as exc:
            logger.error("[fetch_by_token] ERROR unexpected exception=%s table=%s", exc, TABLE)
            raise

    def fetch_by_user(self, user_id: str) -> TokenRecord | None:
        """
        GSI query on UserTokenIndex: most recent token record for a user_id.
        Returns None if not found OR if the GSI does not exist yet (graceful fallback).

        NOTE: If UserTokenIndex has not been created on the table yet, this method
        logs a warning and returns None (treated as returning user — safe default)
        rather than crashing the entire auth flow.
        """
        logger.info("[fetch_by_user] START table=%s index=UserTokenIndex user_id=%s", TABLE, user_id)
        try:
            resp = get_table(TABLE).query(
                IndexName="UserTokenIndex",
                KeyConditionExpression=Key("user_id").eq(user_id),
                ScanIndexForward=False,   # DESC — most recent first
                Limit=1,
            )
            items = resp.get("Items", [])
            if not items:
                logger.info("[fetch_by_user] RESULT no records found for user_id=%s", user_id)
                return None
            record = _record_from_item(items[0])
            logger.info(
                "[fetch_by_user] RESULT found session_id=%s expires_at=%s is_active=%s",
                record.session_id, record.expires_at, record.is_active,
            )
            return record
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            if code == "ValidationException" and "UserTokenIndex" in msg:
                logger.warning(
                    "[fetch_by_user] WARNING UserTokenIndex does not exist on table=%s — "
                    "falling back to is_first_time=False. Create the GSI to enable first-time detection.",
                    TABLE,
                )
                return None   # safe fallback — treated as returning user
            logger.error("[fetch_by_user] ERROR ClientError code=%s message=%s table=%s", code, msg, TABLE)
            raise
        except Exception as exc:
            logger.error("[fetch_by_user] ERROR unexpected exception=%s table=%s", exc, TABLE)
            raise

    def fetch_by_session(self, session_id: str) -> TokenRecord | None:
        """
        GSI query on SessionTokenIndex: latest active token for a session.
        """
        session_id = sanitize_key(session_id, "session_id")
        logger.info("[fetch_by_session] START table=%s index=SessionTokenIndex session_id=%s", TABLE, session_id)
        try:
            resp = get_table(TABLE).query(
                IndexName="SessionTokenIndex",
                KeyConditionExpression=Key("session_id").eq(session_id),
                FilterExpression=Attr("is_active").eq(True),
                ScanIndexForward=False,   # DESC — most recent first
                Limit=1,
            )
            items = resp.get("Items", [])
            if not items:
                logger.info("[fetch_by_session] RESULT no active token found for session_id=%s", session_id)
                return None
            record = _record_from_item(items[0])
            logger.info(
                "[fetch_by_session] RESULT found user_id=%s expires_at=%s is_active=%s",
                record.user_id, record.expires_at, record.is_active,
            )
            return record
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            logger.error("[fetch_by_session] ERROR ClientError code=%s message=%s table=%s", code, msg, TABLE)
            raise
        except Exception as exc:
            logger.error("[fetch_by_session] ERROR unexpected exception=%s table=%s", exc, TABLE)
            raise

    def upsert(self, record: TokenRecord) -> None:
        """
        PutItem — insert or overwrite a token record.
        Called on first-time verification and after token expiry + re-auth.
        """
        session_id = sanitize_key(record.session_id, "session_id")
        logger.info(
            "[upsert] START table=%s user_id=%s session_id=%s expires_at=%s",
            TABLE, record.user_id, session_id, record.expires_at,
        )
        try:
            get_table(TABLE).put_item(Item={
                "token":      _hash(record.raw_token),
                "session_id": session_id,
                "user_id":    record.user_id,
                "user_name":  record.user_name,
                "user_email": record.user_email,
                "expires_at": record.expires_at,
                "ttl":        _to_epoch(record.expires_at),
                "is_active":  record.is_active,
                "created_at": _now_iso(),
            })
            logger.info(
                "[upsert] SUCCESS token stored user_id=%s session_id=%s",
                record.user_id, session_id,
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            logger.error("[upsert] ERROR ClientError code=%s message=%s table=%s", code, msg, TABLE)
            raise
        except Exception as exc:
            logger.error("[upsert] ERROR unexpected exception=%s table=%s", exc, TABLE)
            raise

    def invalidate(self, raw_token: str) -> None:
        """
        Soft-revoke a token — sets is_active=False.
        DynamoDB TTL will auto-delete the item when ttl elapses.
        """
        token_hash = _hash(raw_token)
        logger.info("[invalidate] START table=%s hash_prefix=%s...", TABLE, token_hash[:8])
        try:
            get_table(TABLE).update_item(
                Key={"token": token_hash},
                UpdateExpression="SET is_active = :f",
                ExpressionAttributeValues={":f": False},
            )
            logger.info("[invalidate] SUCCESS token soft-revoked hash_prefix=%s...", token_hash[:8])
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            msg  = exc.response["Error"]["Message"]
            logger.error("[invalidate] ERROR ClientError code=%s message=%s table=%s", code, msg, TABLE)
            raise
        except Exception as exc:
            logger.error("[invalidate] ERROR unexpected exception=%s table=%s", exc, TABLE)
            raise
