# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 8/10 - Config metadata + input/output reference + executed-output
# records (tasks 6-9). REUSABLE. Rework: store JSON UUID mappings (task 8).
# ============================================================================
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key, Attr
from storage.dynamodb.client import get_table, sanitize_key

logger = logging.getLogger(__name__)
TABLE = os.getenv("DOCUMENTS_TABLE", "DocumentsTable")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

# ── Session file records ──────────────────────────────────────────────────────
def insert_document(
    session_id: str,
    file_type: str,
    s3_bucket: str,
    s3_key: str,
    filename: str,
    business_unit: str = "",
    platform: str = "",
    category: str = "",
    version: str = "",
    upload_status: str = "pending",
    execution_id: str = "",
    document_id: str = "",
) -> str:
    session_id  = sanitize_key(session_id, "session_id")
    document_id = document_id or uuid.uuid4().hex
    sk          = _build_sk(file_type, document_id, execution_id)
    item = {
        "PK":               f"SESSION#{session_id}",
        "SK":               sk,
        "file_type":        file_type,
        "session_id":       session_id,
        "document_id":      document_id,
        "s3_bucket":        s3_bucket,
        "s3_key":           s3_key,
        "filename":         filename,
        "business_unit":    business_unit,
        "platform":         platform,
        "category":         category,
        "version":          version,
        "upload_status":    upload_status,
        "is_active":        True,
        "created_at":       _now(),
    }
    get_table(TABLE).put_item(Item=item)
    logger.info("Inserted document file_type=%s session=%s doc_id=%s", file_type, session_id, document_id)
    return document_id


def update_upload_status(session_id: str, file_type: str, document_id: str, status: str) -> None:
    session_id = sanitize_key(session_id, "session_id")
    sk = _build_sk(file_type, document_id)
    get_table(TABLE).update_item(
        Key={"PK": f"SESSION#{session_id}", "SK": sk},
        UpdateExpression="SET upload_status = :s, updated_at = :t",
        ExpressionAttributeValues={":s": status, ":t": _now()},
    )
    logger.info("Updated upload_status=%s doc_id=%s", status, document_id)


def insert_executed_output_document(
    session_id: str,
    execution_id: str,
    s3_bucket: str,
    s3_key: str,
    filename: str,
    business_unit: str = "",
    platform: str = "",
    category: str = "",
    input_document_id: str = "",
) -> str:
    """Record a server-generated executed output file in DynamoDB.
    Uses input_document_id as the record's document_id for consistency —
    every record has a single 'document_id' field pointing to its source.
    SK uniqueness is guaranteed by execution_id: EXECUTED_OUTPUT#{execution_id}#{document_id}
    """
    return insert_document(
        session_id=session_id,
        file_type="EXECUTED_OUTPUT",
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        filename=filename,
        business_unit=business_unit,
        platform=platform,
        category=category,
        upload_status="completed",
        execution_id=execution_id,
        document_id=input_document_id,  # reuse source input's document_id for consistency
    )


def insert_fps_document(
    session_id: str,
    unique_id: str,
    source_key: str,
    smb_location: str,
    filename: str,
    *,
    s3_bucket: str = "",
    s3_key: str = "",
    business_unit: str = "",
    platform: str = "",
    category: str = "",
    save_file_location: str = "",
    request_id: str = "",
    useremail: str = "",
    upload_status: str = "uploaded",
    document_id: str = "",
) -> str:
    """Record a user file collected for FPS execution (Deliverable 8/10).

    The authoritative copy lives on the SMB share (``smb_location``); an S3
    copy (``s3_key``) is kept as backup. One row per file per FPS run
    (``unique_id``) so multiple executions never collide.
    """
    session_id  = sanitize_key(session_id, "session_id")
    document_id = document_id or uuid.uuid4().hex
    sk          = f"FPS_INPUT#{unique_id}#{document_id}"
    item = {
        "PK":                 f"SESSION#{session_id}",
        "SK":                 sk,
        "file_type":          "FPS_INPUT",
        "session_id":         session_id,
        "document_id":        document_id,
        "unique_id":          unique_id,
        "source_key":         source_key,
        "smb_location":       smb_location,
        "s3_bucket":          s3_bucket,
        "s3_key":             s3_key,
        "filename":           filename,
        "business_unit":      business_unit,
        "platform":           platform,
        "category":           category,
        "save_file_location": save_file_location,
        "request_id":         request_id,
        "useremail":          useremail,
        "upload_status":      upload_status,
        "is_active":          True,
        "created_at":         _now(),
    }
    get_table(TABLE).put_item(Item=item)
    logger.info(
        "Inserted FPS document session=%s unique_id=%s source_key=%s doc_id=%s",
        session_id, unique_id, source_key, document_id,
    )
    return document_id


def fetch_fps_documents(session_id: str, unique_id: str = "") -> list[dict]:
    """Fetch FPS input files for a session (optionally one run's ``unique_id``)."""
    session_id = sanitize_key(session_id, "session_id")
    prefix = f"FPS_INPUT#{unique_id}#" if unique_id else "FPS_INPUT#"
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"SESSION#{session_id}") & Key("SK").begins_with(prefix),
        FilterExpression=Attr("is_active").eq(True),
    )
    return resp.get("Items", [])


def fetch_documents_by_session(session_id: str, file_type: str = None) -> list[dict]:
    session_id = sanitize_key(session_id, "session_id")
    kce = Key("PK").eq(f"SESSION#{session_id}")
    if file_type:
        kce = kce & Key("SK").begins_with(f"{file_type}#")
    resp = get_table(TABLE).query(
        KeyConditionExpression=kce,
        FilterExpression=Attr("is_active").eq(True),
    )
    logger.debug("Fetched %d documents for session=%s", len(resp.get("Items", [])), session_id)
    return resp.get("Items", [])


# ── Config records ────────────────────────────────────────────────────────────

def insert_config_record(
    business_unit: str,
    platform: str,
    category: str,
    session_id: str,
    s3_bucket: str,
    s3_key: str,
    filename: str,
    file_type: str,   # CONFIG_DRAFT | CONFIG_CONFIRMED
    version: str,
) -> None:
    business_unit = sanitize_key(business_unit, "business_unit")
    platform      = sanitize_key(platform, "platform")
    category      = sanitize_key(category, "category")
    session_id    = sanitize_key(session_id, "session_id")

    if file_type == "CONFIG_CONFIRMED":
        # Extract timestamp from versioned s3_key filename to keep SK and version in sync.
        # s3_key ends with: validation_transformation_config_<timestamp>.yaml
        # Fall back to generating a fresh timestamp only if extraction fails.
        try:
            ts = s3_key.rsplit("_", 1)[-1].replace(".yaml", "")
            if len(ts) != 15:  # YYYYMMDDTHHmmSS
                raise ValueError
        except (ValueError, IndexError):
            ts = _ts()
        sk = f"CONFIRMED#{ts}"
    else:
        sk = f"DRAFT#{session_id}#{version}"

    get_table(TABLE).put_item(Item={
        "PK":            f"CONFIG#{business_unit}#{platform}#{category}",
        "SK":            sk,
        "file_type":     file_type,
        "session_id":    session_id,
        "document_id":   uuid.uuid4().hex,
        "s3_bucket":     s3_bucket,
        "s3_key":        s3_key,
        "filename":      filename,
        "business_unit": business_unit,
        "platform":      platform,
        "category":      category,
        "version":       version,
        "upload_status": "completed",
        "is_active":     True,
        "created_at":    _now(),
    })
    logger.info("Inserted config record file_type=%s bu=%s platform=%s category=%s", file_type, business_unit, platform, category)


def fetch_latest_confirmed_config(bu: str, platform: str, category: str) -> dict | None:
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"CONFIG#{bu}#{platform}#{category}") & Key("SK").begins_with("CONFIRMED#"),
        ScanIndexForward=False,
        Limit=1,
        FilterExpression=Attr("is_active").eq(True),
    )
    items = resp.get("Items", [])
    logger.debug("fetch_latest_confirmed_config found=%s bu=%s", bool(items), bu)
    return items[0] if items else None


def fetch_latest_draft_config(bu: str, platform: str, category: str, session_id: str) -> dict | None:
    bu         = sanitize_key(bu, "bu")
    platform   = sanitize_key(platform, "platform")
    category   = sanitize_key(category, "category")
    session_id = sanitize_key(session_id, "session_id")
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"CONFIG#{bu}#{platform}#{category}") & Key("SK").begins_with(f"DRAFT#{session_id}#"),
        ScanIndexForward=False,
        Limit=1,
        FilterExpression=Attr("is_active").eq(True),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ── Approved (final) config records (Deliverable 10, tasks 13-14) ──────────────

def insert_approved_config_record(
    business_unit: str,
    platform: str,
    category: str,
    session_id: str,
    s3_bucket: str,
    s3_key: str,
    filename: str,
    version: str,
    approved_by: str = "",
) -> str:
    """Record a finalized/approved config in DynamoDB.

    PK = CONFIG#{bu}#{platform}#{category}, SK = APPROVED#<ts>. The <ts> is
    extracted from a versioned filename when present so the SK and the S3 object
    stay in sync; otherwise a fresh timestamp is generated. Returns the record's
    document_id.
    """
    business_unit = sanitize_key(business_unit, "business_unit")
    platform      = sanitize_key(platform, "platform")
    category      = sanitize_key(category, "category")
    session_id    = sanitize_key(session_id, "session_id")

    try:
        ts = s3_key.rsplit("_", 1)[-1].replace(".json", "")
        if len(ts) != 15:  # YYYYMMDDTHHmmSS
            raise ValueError
    except (ValueError, IndexError):
        ts = _ts()

    document_id = uuid.uuid4().hex
    get_table(TABLE).put_item(Item={
        "PK":            f"CONFIG#{business_unit}#{platform}#{category}",
        "SK":            f"APPROVED#{ts}",
        "file_type":     "CONFIG_APPROVED",
        "status":        "approved",
        "session_id":    session_id,
        "document_id":   document_id,
        "s3_bucket":     s3_bucket,
        "s3_key":        s3_key,
        "filename":      filename,
        "business_unit": business_unit,
        "platform":      platform,
        "category":      category,
        "version":       version,
        "approved_by":   approved_by,
        "approved_at":   _now(),
        "upload_status": "completed",
        "is_active":     True,
        "created_at":    _now(),
    })
    logger.info("Inserted approved config record bu=%s platform=%s category=%s by=%s", business_unit, platform, category, approved_by or "?")
    return document_id


def fetch_latest_approved_config(bu: str, platform: str, category: str) -> dict | None:
    """Return the DynamoDB record for the latest approved config, or None."""
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"CONFIG#{bu}#{platform}#{category}") & Key("SK").begins_with("APPROVED#"),
        ScanIndexForward=False,
        Limit=1,
        FilterExpression=Attr("is_active").eq(True),
    )
    items = resp.get("Items", [])
    logger.debug("fetch_latest_approved_config found=%s bu=%s", bool(items), bu)
    return items[0] if items else None


def list_config_records(bu: str, platform: str, category: str) -> list[dict]:
    """List every config metadata record (draft/confirmed/approved) for a scope.

    Newest first (SK descending). Used to surface config version history (task 12).
    """
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"CONFIG#{bu}#{platform}#{category}"),
        ScanIndexForward=False,
        FilterExpression=Attr("is_active").eq(True),
    )
    return resp.get("Items", [])


# ── UUID mappings (Deliverable 10, task 8) ────────────────────────────────────

def store_uuid_mappings(
    business_unit: str,
    platform: str,
    category: str,
    session_id: str,
    mappings: dict,
    version: str = "",
) -> None:
    """Persist file/sheet/column/rule -> UUID mappings for a config (task 8).

    PK = UUIDMAP#{bu}#{platform}#{category}, SK = MAP#<ts>. The mapping dict is
    extracted from the finalized contract so specific configuration blocks can be
    located deterministically later without reparsing the full JSON.
    """
    business_unit = sanitize_key(business_unit, "business_unit")
    platform      = sanitize_key(platform, "platform")
    category      = sanitize_key(category, "category")
    session_id    = sanitize_key(session_id, "session_id")
    get_table(TABLE).put_item(Item={
        "PK":            f"UUIDMAP#{business_unit}#{platform}#{category}",
        "SK":            f"MAP#{_ts()}",
        "record_type":   "UUID_MAPPINGS",
        "session_id":    session_id,
        "business_unit": business_unit,
        "platform":      platform,
        "category":      category,
        "version":       version,
        "mappings":      mappings,
        "is_active":     True,
        "created_at":    _now(),
    })
    logger.info("Stored UUID mappings bu=%s platform=%s category=%s count=%s", business_unit, platform, category, len(mappings.get("columns", []) if isinstance(mappings, dict) else []))


def fetch_uuid_mappings(bu: str, platform: str, category: str) -> dict | None:
    """Return the most recent UUID-mappings record for a scope, or None."""
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    resp = get_table(TABLE).query(
        KeyConditionExpression=Key("PK").eq(f"UUIDMAP#{bu}#{platform}#{category}") & Key("SK").begins_with("MAP#"),
        ScanIndexForward=False,
        Limit=1,
        FilterExpression=Attr("is_active").eq(True),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


# ── Internal ──────────────────────────────────────────────────────────────────

def _build_sk(file_type: str, document_id: str, execution_id: str = "") -> str:
    if file_type == "EXECUTED_OUTPUT":
        # Use execution_id (already contains timestamp) to avoid SK collisions
        ts = execution_id or _ts()
        return f"EXECUTED_OUTPUT#{ts}#{document_id}"
    return f"{file_type}#{document_id}"
