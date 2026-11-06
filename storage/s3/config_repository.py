# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - S3 config storage: bucket/prefix structure, store/retrieve
# draft+confirmed configs (tasks 2-5, 10-11). REUSABLE.
# Rework: store the exact JSON config (and blocks) instead of YAML.
# ============================================================================
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from storage.s3.client import get_s3_client
from storage.dynamodb.client import sanitize_key

logger = logging.getLogger(__name__)

CONFIGS_BUCKET = os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs")


def _confirmed_prefix(bu: str, platform: str, category: str) -> str:
    return f"configurations/{bu}/{platform}/{category}/confirmed/"


def _draft_prefix(bu: str, platform: str, category: str, session_id: str = None) -> str:
    """
    Draft prefix with optional session_id for session-scoped drafts.
    With session_id: configurations/{bu}/{platform}/{category}/drafts/{session_id}/
    Without session_id: configurations/{bu}/{platform}/{category}/drafts/
    """
    base = f"configurations/{bu}/{platform}/{category}/drafts/"
    if session_id:
        return f"{base}{session_id}/"
    return base


def _sanitize_scope(bu: str, platform: str, category: str, session_id: str = None) -> tuple:
    bu       = sanitize_key(bu, "bu")
    platform = sanitize_key(platform, "platform")
    category = sanitize_key(category, "category")
    if session_id is not None:
        session_id = sanitize_key(session_id, "session_id")
        return bu, platform, category, session_id
    return bu, platform, category


# ── Draft configs ─────────────────────────────────────────────────────────────

def save_draft_config(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    yaml_content: str,
    version: str = None,
) -> str:
    """
    Save a draft YAML under drafts/<session_id>/draft_<timestamp>.yaml
    
    Args:
        version: If provided, uses that as filename. If None, auto-generates timestamp.
    
    Returns s3_key.
    """
    bu, platform, category, session_id = _sanitize_scope(bu, platform, category, session_id)
    
    # Auto-generate versioned filename if not provided
    if version is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        version = f"draft_{timestamp}"
    
    s3_key = f"{_draft_prefix(bu, platform, category, session_id)}{version}.yaml"
    get_s3_client().put_object(
        Bucket=CONFIGS_BUCKET,
        Key=s3_key,
        Body=yaml_content.encode("utf-8"),
        ContentType="application/x-yaml",
    )
    logger.info("Saved draft config session_id=%s version=%s s3_key=%s", session_id, version, s3_key)
    return s3_key


def fetch_draft_yaml(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    version: str,
) -> str | None:
    """Fetch a specific draft version for a session. Returns YAML string or None."""
    bu, platform, category, session_id = _sanitize_scope(bu, platform, category, session_id)
    s3_key = f"{_draft_prefix(bu, platform, category, session_id)}{version}.yaml"
    try:
        resp = get_s3_client().get_object(Bucket=CONFIGS_BUCKET, Key=s3_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Draft config not found version=%s s3_key=%s", version, s3_key)
            return None
        logger.error("S3 error fetching draft config s3_key=%s: %s", s3_key, e)
        raise


def fetch_latest_draft_yaml(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> str | None:
    """
    Fetch the most recently modified draft for a specific session.
    Returns YAML string or None.
    """
    bu, platform, category, session_id = _sanitize_scope(bu, platform, category, session_id)
    s3      = get_s3_client()
    prefix  = _draft_prefix(bu, platform, category, session_id)
    listed  = s3.list_objects_v2(Bucket=CONFIGS_BUCKET, Prefix=prefix)
    objects = listed.get("Contents", [])
    if not objects:
        logger.debug("No draft objects found under prefix=%s", prefix)
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    resp   = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest["Key"])
    logger.info("Fetched latest draft for session=%s s3_key=%s", session_id, latest["Key"])
    return resp["Body"].read().decode("utf-8")


# ── Confirmed configs ─────────────────────────────────────────────────────────

def save_confirmed_config(
    bu: str,
    platform: str,
    category: str,
    yaml_content: str,
) -> tuple[str, str]:
    """
    Saves the confirmed config with the following logic:
      - First time (validation_transformation_config.yaml does not exist):
          Save as validation_transformation_config.yaml  ← base/initial config
      - Subsequent confirmations (base file already exists):
          Save as validation_transformation_config_<timestamp>.yaml  ← versioned record

    Returns (saved_s3_key, latest_key) where latest_key is always the base file path.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    prefix     = _confirmed_prefix(bu, platform, category)
    latest_key = f"{prefix}validation_transformation_config.yaml"
    body       = yaml_content.encode("utf-8")

    s3 = get_s3_client()

    # Check whether the base config already exists
    base_exists = False
    try:
        s3.head_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        base_exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise

    if not base_exists:
        # First time: save as the base (non-versioned) file
        s3.put_object(Bucket=CONFIGS_BUCKET, Key=latest_key, Body=body, ContentType="application/x-yaml")
        logger.info("Saved initial confirmed config as base file key=%s", latest_key)
        return latest_key, latest_key
    else:
        # Subsequent confirmation: save with date suffix only
        timestamp     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        versioned_key = f"{prefix}validation_transformation_config_{timestamp}.yaml"
        s3.put_object(Bucket=CONFIGS_BUCKET, Key=versioned_key, Body=body, ContentType="application/x-yaml")
        logger.info("Saved confirmed config versioned_key=%s", versioned_key)
        return versioned_key, latest_key


def fetch_latest_confirmed_yaml(bu: str, platform: str, category: str) -> str | None:
    """
    Fetch latest confirmed YAML:
      1. Try confirmed/validation_transformation_config.yaml (latest overwrite)
      2. Fallback: list versioned files, pick most recent by LastModified
    Returns YAML string or None.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3         = get_s3_client()
    latest_key = f"{_confirmed_prefix(bu, platform, category)}validation_transformation_config.yaml"

    try:
        resp = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        logger.debug("Fetched latest confirmed config key=%s", latest_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Latest confirmed config not found, falling back to versioned list")
        else:
            logger.warning("Unexpected S3 error fetching confirmed config key=%s: %s", latest_key, e)

    # fallback: pick most recently modified versioned file
    prefix  = _confirmed_prefix(bu, platform, category)
    listed  = s3.list_objects_v2(Bucket=CONFIGS_BUCKET, Prefix=prefix)
    objects = [o for o in listed.get("Contents", []) if o["Key"] != latest_key]
    if not objects:
        logger.debug("No confirmed config objects found under prefix=%s", prefix)
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    resp   = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest["Key"])
    logger.debug("Fetched versioned confirmed config key=%s", latest["Key"])
    return resp["Body"].read().decode("utf-8")


# ── Priority fetch used by /edit_config ───────────────────────────────────────

def fetch_config_yaml_for_session(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> str | None:
    """
    Fetch config YAML for an editing session with proper priority:
      1. drafts/{session_id}/ - latest draft for this session (if any)
      2. confirmed/validation_transformation_config.yaml - base confirmed config

    This ensures edits during a session continue from the latest draft,
    while new sessions start from the confirmed config.

    Returns YAML string or None.
    """
    # Priority 1: Check for session-specific drafts
    yaml_content = fetch_latest_draft_yaml(bu, platform, category, session_id)
    if yaml_content:
        logger.info("Using latest draft for session=%s", session_id)
        return yaml_content

    # Priority 2: Fall back to confirmed config
    yaml_content = fetch_latest_confirmed_yaml(bu, platform, category)
    if yaml_content:
        logger.info("No drafts for session=%s, using confirmed config", session_id)
        return yaml_content

    logger.warning("No config found for bu=%s platform=%s category=%s session=%s", bu, platform, category, session_id)
    return None


def fetch_config_yaml(
    bu: str,
    platform: str,
    category: str,
) -> str | None:
    """
    Fetch config YAML without session context (legacy/fallback).
    Only fetches from confirmed configs.
    Returns YAML string or None.
    """
    return fetch_latest_confirmed_yaml(bu, platform, category)


# ── Config Summary Storage ────────────────────────────────────────────────────

def save_config_summary(
    bu: str,
    platform: str,
    category: str,
    summary: str,
) -> str:
    """
    Save the config summary as a separate markdown file alongside the config.
    This is updated whenever the config is saved via get_configuration.
    
    Returns s3_key.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3_key = f"{_confirmed_prefix(bu, platform, category)}config_summary.md"
    
    get_s3_client().put_object(
        Bucket=CONFIGS_BUCKET,
        Key=s3_key,
        Body=summary.encode("utf-8"),
        ContentType="text/markdown",
    )
    logger.info("Saved config summary to s3_key=%s", s3_key)
    return s3_key


def fetch_config_summary(
    bu: str,
    platform: str,
    category: str,
) -> str | None:
    """
    Fetch the config summary markdown.
    Returns summary string or None.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3_key = f"{_confirmed_prefix(bu, platform, category)}config_summary.md"
    
    try:
        resp = get_s3_client().get_object(Bucket=CONFIGS_BUCKET, Key=s3_key)
        logger.debug("Fetched config summary from key=%s", s3_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Config summary not found at key=%s", s3_key)
            return None
        logger.error("S3 error fetching config summary key=%s: %s", s3_key, e)
        raise


# ============================================================================
# Deliverable 9/10 - EXACT JSON CONTRACT storage.
# Mirrors the YAML helpers above 1:1 but stores the exact ConfigurationContract
# JSON document. Same bucket, same prefixes, same versioning/naming convention;
# only the file extension (.json) and ContentType (application/json) differ:
#   drafts/{session_id}/draft_<ts>.json
#   confirmed/validation_transformation_config.json           (base / latest)
#   confirmed/validation_transformation_config_<ts>.json      (versioned)
# ============================================================================

_JSON_BASE_NAME = "validation_transformation_config.json"
_JSON_CONTENT_TYPE = "application/json"


# ── Draft JSON configs ────────────────────────────────────────────────────────

def save_draft_json(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    json_content: str,
    version: str = None,
) -> str:
    """
    Save a draft JSON config under drafts/<session_id>/draft_<timestamp>.json

    Args:
        version: If provided, used as the filename stem. If None, auto-generates
                 a UTC timestamp stem (``draft_YYYYMMDDTHHMMSS``).

    Returns s3_key.
    """
    bu, platform, category, session_id = _sanitize_scope(bu, platform, category, session_id)

    if version is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        version = f"draft_{timestamp}"

    s3_key = f"{_draft_prefix(bu, platform, category, session_id)}{version}.json"
    get_s3_client().put_object(
        Bucket=CONFIGS_BUCKET,
        Key=s3_key,
        Body=json_content.encode("utf-8"),
        ContentType=_JSON_CONTENT_TYPE,
    )
    logger.info("Saved draft JSON config session_id=%s version=%s s3_key=%s", session_id, version, s3_key)
    return s3_key


def fetch_latest_draft_json(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> str | None:
    """Fetch the most recently modified draft JSON for a session. Returns str or None."""
    bu, platform, category, session_id = _sanitize_scope(bu, platform, category, session_id)
    s3     = get_s3_client()
    prefix = _draft_prefix(bu, platform, category, session_id)
    listed = s3.list_objects_v2(Bucket=CONFIGS_BUCKET, Prefix=prefix)
    objects = [o for o in listed.get("Contents", []) if o["Key"].endswith(".json")]
    if not objects:
        logger.debug("No draft JSON objects found under prefix=%s", prefix)
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    resp   = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest["Key"])
    logger.info("Fetched latest draft JSON for session=%s s3_key=%s", session_id, latest["Key"])
    return resp["Body"].read().decode("utf-8")


# ── Confirmed JSON configs ─────────────────────────────────────────────────────

def save_confirmed_json(
    bu: str,
    platform: str,
    category: str,
    json_content: str,
) -> tuple[str, str]:
    """
    Save the confirmed JSON config with the same base/versioned logic as YAML:
      - First time (base file absent): save validation_transformation_config.json
      - Subsequent confirms: save validation_transformation_config_<timestamp>.json

    Returns (saved_s3_key, latest_key) where latest_key is always the base file.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    prefix     = _confirmed_prefix(bu, platform, category)
    latest_key = f"{prefix}{_JSON_BASE_NAME}"
    body       = json_content.encode("utf-8")

    s3 = get_s3_client()

    base_exists = False
    try:
        s3.head_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        base_exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise

    if not base_exists:
        s3.put_object(Bucket=CONFIGS_BUCKET, Key=latest_key, Body=body, ContentType=_JSON_CONTENT_TYPE)
        logger.info("Saved initial confirmed JSON config as base file key=%s", latest_key)
        return latest_key, latest_key

    timestamp     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    versioned_key = f"{prefix}validation_transformation_config_{timestamp}.json"
    s3.put_object(Bucket=CONFIGS_BUCKET, Key=versioned_key, Body=body, ContentType=_JSON_CONTENT_TYPE)
    logger.info("Saved confirmed JSON config versioned_key=%s", versioned_key)
    return versioned_key, latest_key


def fetch_latest_confirmed_json(bu: str, platform: str, category: str) -> str | None:
    """
    Fetch latest confirmed JSON:
      1. Try confirmed/validation_transformation_config.json (base / latest)
      2. Fallback: list versioned .json files, pick most recent by LastModified
    Returns str or None.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3         = get_s3_client()
    latest_key = f"{_confirmed_prefix(bu, platform, category)}{_JSON_BASE_NAME}"

    try:
        resp = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        logger.debug("Fetched latest confirmed JSON config key=%s", latest_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Latest confirmed JSON config not found, falling back to versioned list")
        else:
            logger.warning("Unexpected S3 error fetching confirmed JSON key=%s: %s", latest_key, e)

    prefix  = _confirmed_prefix(bu, platform, category)
    listed  = s3.list_objects_v2(Bucket=CONFIGS_BUCKET, Prefix=prefix)
    objects = [
        o for o in listed.get("Contents", [])
        if o["Key"] != latest_key and o["Key"].endswith(".json")
    ]
    if not objects:
        logger.debug("No confirmed JSON config objects found under prefix=%s", prefix)
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    resp   = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest["Key"])
    logger.debug("Fetched versioned confirmed JSON config key=%s", latest["Key"])
    return resp["Body"].read().decode("utf-8")


def fetch_config_json_for_session(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> str | None:
    """
    Fetch config JSON for an editing session with the same priority as YAML:
      1. drafts/{session_id}/ - latest draft JSON for this session (if any)
      2. confirmed/validation_transformation_config.json - base confirmed config
    Returns str or None.
    """
    draft = fetch_latest_draft_json(bu, platform, category, session_id)
    if draft:
        logger.info("Using latest draft JSON for session=%s", session_id)
        return draft

    confirmed = fetch_latest_confirmed_json(bu, platform, category)
    if confirmed:
        logger.info("No JSON drafts for session=%s, using confirmed config", session_id)
        return confirmed

    logger.warning(
        "No JSON config found for bu=%s platform=%s category=%s session=%s",
        bu, platform, category, session_id,
    )
    return None


# ============================================================================
# Deliverable 10 - APPROVED (final) JSON configs (tasks 5, 9, 12-14).
# A finalized/approved configuration is stored under a dedicated approved/ prefix
# so it is unambiguously distinct from confirmed drafts. Same base+versioned
# naming convention as confirmed, so history and "latest" retrieval work the same
# way. The future-execution workflow reads the latest approved config (falling
# back to confirmed only when nothing has been approved yet).
#   approved/validation_transformation_config.json           (base / latest)
#   approved/validation_transformation_config_<ts>.json      (versioned history)
# ============================================================================

def _approved_prefix(bu: str, platform: str, category: str) -> str:
    return f"configurations/{bu}/{platform}/{category}/approved/"


def save_approved_json(
    bu: str,
    platform: str,
    category: str,
    json_content: str,
) -> tuple[str, str]:
    """
    Save the approved (final) JSON config with the same base/versioned logic used
    for confirmed configs, but under the approved/ prefix:
      - First time (base file absent): save validation_transformation_config.json
      - Subsequent approvals: save validation_transformation_config_<timestamp>.json

    Returns (saved_s3_key, latest_key) where latest_key is always the base file.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    prefix     = _approved_prefix(bu, platform, category)
    latest_key = f"{prefix}{_JSON_BASE_NAME}"
    body       = json_content.encode("utf-8")

    s3 = get_s3_client()

    base_exists = False
    try:
        s3.head_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        base_exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise

    if not base_exists:
        s3.put_object(Bucket=CONFIGS_BUCKET, Key=latest_key, Body=body, ContentType=_JSON_CONTENT_TYPE)
        logger.info("Saved initial approved JSON config as base file key=%s", latest_key)
        return latest_key, latest_key

    timestamp     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    versioned_key = f"{prefix}validation_transformation_config_{timestamp}.json"
    s3.put_object(Bucket=CONFIGS_BUCKET, Key=versioned_key, Body=body, ContentType=_JSON_CONTENT_TYPE)
    logger.info("Saved approved JSON config versioned_key=%s", versioned_key)
    return versioned_key, latest_key


def fetch_latest_approved_json(bu: str, platform: str, category: str) -> str | None:
    """
    Fetch latest approved JSON:
      1. Try approved/validation_transformation_config.json (base / latest)
      2. Fallback: list versioned .json files, pick most recent by LastModified
    Returns str or None.
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3         = get_s3_client()
    latest_key = f"{_approved_prefix(bu, platform, category)}{_JSON_BASE_NAME}"

    try:
        resp = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest_key)
        logger.debug("Fetched latest approved JSON config key=%s", latest_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Latest approved JSON config not found, falling back to versioned list")
        else:
            logger.warning("Unexpected S3 error fetching approved JSON key=%s: %s", latest_key, e)

    prefix  = _approved_prefix(bu, platform, category)
    listed  = s3.list_objects_v2(Bucket=CONFIGS_BUCKET, Prefix=prefix)
    objects = [
        o for o in listed.get("Contents", [])
        if o["Key"] != latest_key and o["Key"].endswith(".json")
    ]
    if not objects:
        logger.debug("No approved JSON config objects found under prefix=%s", prefix)
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    resp   = s3.get_object(Bucket=CONFIGS_BUCKET, Key=latest["Key"])
    logger.debug("Fetched versioned approved JSON config key=%s", latest["Key"])
    return resp["Body"].read().decode("utf-8")


def fetch_config_json_by_key(s3_key: str) -> str | None:
    """Fetch a specific config JSON object by its exact S3 key. Returns str or None."""
    try:
        resp = get_s3_client().get_object(Bucket=CONFIGS_BUCKET, Key=s3_key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.debug("Config JSON object not found key=%s", s3_key)
            return None
        logger.error("S3 error fetching config JSON key=%s: %s", s3_key, e)
        raise


def list_config_object_versions(bu: str, platform: str, category: str) -> list[dict]:
    """
    List every stored config JSON object (draft, confirmed, approved) with its
    S3 key, lifecycle status, last-modified time, and size, newest first.

    Returns a list of dicts: {status, s3_key, last_modified (ISO str), size,
    is_base}. Used to surface JSON version history (task 12).
    """
    bu, platform, category = _sanitize_scope(bu, platform, category)
    s3 = get_s3_client()
    root = f"configurations/{bu}/{platform}/{category}/"
    _status_for = {
        "drafts/": "draft",
        "confirmed/": "confirmed",
        "approved/": "approved",
    }

    versions: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=CONFIGS_BUCKET, Prefix=root):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            relative = key[len(root):]
            status = "unknown"
            for marker, label in _status_for.items():
                if relative.startswith(marker):
                    status = label
                    break
            last_modified = obj.get("LastModified")
            versions.append({
                "status": status,
                "s3_key": key,
                "last_modified": last_modified.isoformat() if last_modified else None,
                "size": int(obj.get("Size", 0)),
                "is_base": relative.endswith(f"/{_JSON_BASE_NAME}") or relative.endswith(_JSON_BASE_NAME),
            })

    versions.sort(key=lambda v: v["last_modified"] or "", reverse=True)
    logger.debug("Listed %d config JSON versions for bu=%s platform=%s category=%s", len(versions), bu, platform, category)
    return versions
