# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - BU/Platform/Category metadata store. REUSABLE as-is.
# ============================================================================
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)
TABLE = os.getenv("BUSINESS_PLATFORM_TABLE", "BusinessPlatformCategoryTable")
_ENTITY_BU       = "BU"
_ENTITY_PLATFORM = "PLATFORM"
_ENTITY_CATEGORY = "CATEGORY"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _bu_pk(bu: str) -> str:
    return f"BU#{bu}"
def _platform_pk(bu: str) -> str:
    return f"BU#{bu}"
def _category_pk(bu: str, platform: str) -> str:
    return f"BU#{bu}#PLATFORM#{platform}"
def _platform_sk(platform: str) -> str:
    return f"PLATFORM#{platform}"
def _category_sk(category: str) -> str:
    return f"CATEGORY#{category}"

# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_item(pk: str, sk: str) -> dict | None:
    resp = get_table(TABLE).get_item(Key={"PK": pk, "SK": sk})
    return resp.get("Item")

def _exists_and_active(pk: str, sk: str) -> bool:
    item = _get_item(pk, sk)
    return item is not None and item.get("is_active", False)

def _soft_delete(pk: str, sk: str) -> None:
    """Soft-delete a single item. Raises ValueError if item does not exist."""
    item = _get_item(pk, sk)
    if item is None:
        raise ValueError(f"Item not found: PK={pk} SK={sk}")
    get_table(TABLE).update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression="SET is_active = :f, updated_at = :t",
        ExpressionAttributeValues={":f": False, ":t": _now()},
    )
    logger.info("Soft-deleted PK=%s SK=%s", pk, sk)

def _query_active(pk: str, sk_prefix: str) -> list[dict]:
    """Query all active items under a PK with a given SK prefix."""
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix),
        FilterExpression=Attr("is_active").eq(True),
    )
    return resp.get("Items", [])

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all_bus() -> list[str]:
    """Return names of all active BUs via EntityTypeIndex GSI."""
    logger.debug("Fetching all BUs")
    resp = get_table(TABLE).query(
        IndexName="EntityTypeIndex",
        KeyConditionExpression=Key("entity_type").eq(_ENTITY_BU),
        FilterExpression=Attr("is_active").eq(True),
    )
    return [item["name"] for item in resp.get("Items", [])]

def fetch_platforms(bu: str) -> list[str]:
    """Return names of all active platforms under a BU."""
    bu = sanitize_key(bu, "bu")
    logger.debug("Fetching platforms for BU=%s", bu)
    items = _query_active(_bu_pk(bu), "PLATFORM#")
    return [item["name"] for item in items]

def fetch_categories(bu: str, platform: str) -> list[str]:
    """Return names of all active categories under a BU+Platform."""
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    logger.debug("Fetching categories for BU=%s PLATFORM=%s", bu, platform)
    items = _query_active(_category_pk(bu, platform), "CATEGORY#")
    return [item["name"] for item in items]

def validate_combo(bu: str, platform: str, category: str) -> bool:
    """
    O(1) check that a BU+Platform+Category combo exists and is active.
    Used by /upload_file before accepting a file upload.
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    return _exists_and_active(_category_pk(bu, platform), _category_sk(category))

# ── Insert ────────────────────────────────────────────────────────────────────
def insert_category(bu: str, platform: str, category: str) -> None:
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    if not _exists_and_active(_bu_pk(bu), "META"):
        raise ValueError(f"BU '{bu}' does not exist or is inactive")
    if not _exists_and_active(_platform_pk(bu), _platform_sk(platform)):
        raise ValueError(f"Platform '{platform}' does not exist or is inactive under BU '{bu}'")
    try:
        get_table(TABLE).put_item(
            Item={
                "PK":          _category_pk(bu, platform),
                "SK":          _category_sk(category),
                "entity_type": _ENTITY_CATEGORY,
                "name":        category,
                "is_active":   True,
                "created_at":  _now(),
            },
            ConditionExpression="attribute_not_exists(SK)",
        )
        logger.info("Inserted CATEGORY: %s under BU: %s PLATFORM: %s", category, bu, platform)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(f"Category '{category}' already exists under BU '{bu}' / Platform '{platform}'")
        logger.error("DynamoDB error inserting CATEGORY '%s': %s", category, e)
        raise

# ── Summary ──────────────────────────────────────────────────────────────────

def upsert_category_summary(bu: str, platform: str, category: str, summary: str) -> None:
    """
    Store the transformation summary on the CATEGORY item in DynamoDB.
    Creates or updates the `summary` and `summary_updated_at` attributes.
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    pk = _category_pk(bu, platform)
    sk = _category_sk(category)
    try:
        get_table(TABLE).update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET #s = :summary, summary_updated_at = :ts",
            ExpressionAttributeNames={"#s": "summary"},
            ExpressionAttributeValues={
                ":summary": summary,
                ":ts":      _now(),
            },
        )
        print(f"[upsert_category_summary] ✅ Summary saved to DynamoDB: PK={pk}, SK={sk}")
        logger.info("[business_repository] Upserted summary for BU=%s PLATFORM=%s CATEGORY=%s", bu, platform, category)
    except Exception as exc:
        print(f"[upsert_category_summary] ❌ FAILED: {type(exc).__name__}: {exc}")
        logger.warning("[business_repository] Failed to upsert summary: %s", exc)
        raise


def fetch_category_summary(bu: str, platform: str, category: str) -> str | None:
    """
    Retrieve the transformation summary stored on the CATEGORY item.
    Returns the summary string or None if not present.
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    item = _get_item(_category_pk(bu, platform), _category_sk(category))
    if item is None:
        return None
    return item.get("summary")


# ── Soft Delete ───────────────────────────────────────────────────────────────
def delete_category(bu: str, platform: str, category: str) -> None:
    """Soft-delete a single Category."""
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    _soft_delete(_category_pk(bu, platform), _category_sk(category))
