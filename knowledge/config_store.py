# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2 - Config lifecycle & contract-validation storage. REUSABLE.
# Rework: retarget payload from bespoke YAML to the exact API JSON contract
# (draft/confirmed JSON configs) once the JSON models/schema exist.
# ============================================================================
"""
knowledge/config_store.py
--------------------------
Single source of truth for config lifecycle:
  - Stores config metadata (config_id, version, status, s3_key) in DynamoDB DocumentsTable
  - Delegates actual S3 read/write to storage.s3.config_repository
  - Delegates DynamoDB record read/write to storage.dynamodb.document_repository

UnitOfWork pattern: S3 write happens first. If DynamoDB record write fails,
the S3 object is still valid — the DynamoDB record is retried or can be
reconstructed from S3. This is safer than the reverse order.

Status values: draft | confirmed | archived
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from storage.dynamodb.document_repository import (
    insert_config_record,
    fetch_latest_confirmed_config,
    fetch_latest_draft_config,
    insert_approved_config_record,
    fetch_latest_approved_config,
    list_config_records,
    store_uuid_mappings,
    fetch_uuid_mappings,
)
from storage.s3.config_repository import (
    save_draft_config,
    save_confirmed_config,
    fetch_config_yaml,
    fetch_config_yaml_for_session,
    save_config_summary,
    fetch_config_summary,
    save_draft_json,
    save_confirmed_json,
    fetch_config_json_for_session,
    fetch_latest_confirmed_json,
    save_approved_json,
    fetch_latest_approved_json,
    fetch_config_json_by_key,
    list_config_object_versions,
)

logger = logging.getLogger(__name__)

ConfigStatus = Literal["draft", "confirmed", "archived"]


def store_draft(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    yaml_content: str,
    s3_bucket: str,
    version: str = None,
) -> str:
    """
    Save a draft config to S3 then record metadata in DynamoDB.
    S3 write is first — if DynamoDB fails the YAML is still accessible.
    
    Args:
        version: If None, auto-generates timestamp-based version (draft_YYYYMMDDTHHMMSS).
    
    Returns s3_key.
    """
    # 1. Write to S3 first (auto-generates timestamp version if not provided)
    s3_key = save_draft_config(bu, platform, category, session_id, yaml_content, version)
    logger.info("Draft config saved to S3: %s", s3_key)

    # 2. Record in DynamoDB — if this fails the S3 object is still valid
    try:
        insert_config_record(
            business_unit=bu,
            platform=platform,
            category=category,
            session_id=session_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            filename=f"{version}.yaml",
            file_type="CONFIG_DRAFT",
            version=version,
        )
    except Exception as e:
        logger.error("DynamoDB record failed for draft config s3_key=%s: %s", s3_key, e)
        raise

    return s3_key


def store_confirmed(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    yaml_content: str,
    s3_bucket: str,
) -> tuple[str, str]:
    """
    Save confirmed config to S3 (versioned + latest) then record in DynamoDB.
    S3 write is first — if DynamoDB fails the YAML is still accessible.
    Returns (versioned_s3_key, latest_s3_key).
    """
    # 1. Write both versioned and latest to S3
    versioned_key, latest_key = save_confirmed_config(bu, platform, category, yaml_content)
    logger.info("Confirmed config saved to S3: versioned=%s latest=%s", versioned_key, latest_key)

    # 2. Record in DynamoDB
    try:
        insert_config_record(
            business_unit=bu,
            platform=platform,
            category=category,
            session_id=session_id,
            s3_bucket=s3_bucket,
            s3_key=versioned_key,
            filename=versioned_key.split("/")[-1],
            file_type="CONFIG_CONFIRMED",
            version=f"confirmed_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        )
    except Exception as e:
        logger.error("DynamoDB record failed for confirmed config s3_key=%s: %s", versioned_key, e)
        raise

    return versioned_key, latest_key


def get_config_yaml(
    bu: str,
    platform: str,
    category: str,
) -> str | None:
    """
    Fetch config YAML without session context (legacy/fallback).
    Only fetches from confirmed configs.
    Returns YAML string or None.
    """
    return fetch_config_yaml(bu, platform, category)


def get_config_yaml_for_session(
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
    return fetch_config_yaml_for_session(bu, platform, category, session_id)


def get_latest_confirmed_record(bu: str, platform: str, category: str) -> dict | None:
    """Returns the DynamoDB record for the latest confirmed config."""
    return fetch_latest_confirmed_config(bu, platform, category)


def get_latest_draft_record(bu: str, platform: str, category: str, session_id: str) -> dict | None:
    """Returns the DynamoDB record for the latest draft config for a session."""
    return fetch_latest_draft_config(bu, platform, category, session_id)


# ── Config Summary ────────────────────────────────────────────────────────────

def store_summary(bu: str, platform: str, category: str, summary: str) -> str:
    """
    Store the config summary (generated during get_configuration).
    Returns s3_key.
    """
    return save_config_summary(bu, platform, category, summary)


def get_summary(bu: str, platform: str, category: str) -> str | None:
    """
    Fetch the config summary for display in edit_config.
    Returns summary markdown string or None.
    """
    return fetch_config_summary(bu, platform, category)


# ============================================================================
# Deliverable 9/10 - EXACT JSON CONTRACT lifecycle (draft + confirmed).
# Same S3-first UnitOfWork + DynamoDB record pattern as the YAML helpers, but
# the payload is the exact ConfigurationContract JSON document (.json files).
# ============================================================================

def store_draft_json(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    json_content: str,
    s3_bucket: str,
    version: str = None,
) -> str:
    """
    Save a draft JSON config to S3 then record metadata in DynamoDB.
    S3 write is first — if DynamoDB fails the JSON is still accessible.

    Args:
        version: If None, save_draft_json auto-generates a ``draft_<ts>`` stem.

    Returns s3_key.
    """
    s3_key = save_draft_json(bu, platform, category, session_id, json_content, version)
    logger.info("Draft JSON config saved to S3: %s", s3_key)

    try:
        insert_config_record(
            business_unit=bu,
            platform=platform,
            category=category,
            session_id=session_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            filename=s3_key.split("/")[-1],
            file_type="CONFIG_DRAFT",
            version=version,
        )
    except Exception as e:
        logger.error("DynamoDB record failed for draft JSON config s3_key=%s: %s", s3_key, e)
        raise

    return s3_key


def store_confirmed_json(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    json_content: str,
    s3_bucket: str,
) -> tuple[str, str]:
    """
    Save confirmed JSON config to S3 (versioned + latest) then record in DynamoDB.
    S3 write is first — if DynamoDB fails the JSON is still accessible.
    Returns (versioned_s3_key, latest_s3_key).
    """
    versioned_key, latest_key = save_confirmed_json(bu, platform, category, json_content)
    logger.info("Confirmed JSON config saved to S3: versioned=%s latest=%s", versioned_key, latest_key)

    try:
        insert_config_record(
            business_unit=bu,
            platform=platform,
            category=category,
            session_id=session_id,
            s3_bucket=s3_bucket,
            s3_key=versioned_key,
            filename=versioned_key.split("/")[-1],
            file_type="CONFIG_CONFIRMED",
            version=f"confirmed_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        )
    except Exception as e:
        logger.error("DynamoDB record failed for confirmed JSON config s3_key=%s: %s", versioned_key, e)
        raise

    return versioned_key, latest_key


def get_config_json_for_session(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> str | None:
    """
    Fetch config JSON for an editing session with draft->confirmed priority.
    Returns the exact ConfigurationContract JSON string or None.
    """
    return fetch_config_json_for_session(bu, platform, category, session_id)


def get_latest_confirmed_json(bu: str, platform: str, category: str) -> str | None:
    """Fetch the latest confirmed JSON config (no session context). Returns str or None."""
    return fetch_latest_confirmed_json(bu, platform, category)


# ============================================================================
# Deliverable 10 - APPROVED (final) JSON lifecycle + version history (tasks
# 5, 6, 9, 10, 12, 13, 14) and UUID mappings (task 8).
# ============================================================================

def store_approved_json(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    json_content: str,
    s3_bucket: str,
    approved_by: str = "",
    uuid_mappings: dict | None = None,
) -> tuple[str, str]:
    """
    Save an approved (final) JSON config to S3 (versioned + latest) then record
    metadata in DynamoDB. S3 write is first - if DynamoDB fails the JSON is still
    accessible. Optionally persists UUID mappings for later deterministic block
    lookup (task 8). Returns (versioned_s3_key, latest_s3_key).
    """
    versioned_key, latest_key = save_approved_json(bu, platform, category, json_content)
    logger.info("Approved JSON config saved to S3: versioned=%s latest=%s", versioned_key, latest_key)

    version = f"approved_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    try:
        insert_approved_config_record(
            business_unit=bu,
            platform=platform,
            category=category,
            session_id=session_id,
            s3_bucket=s3_bucket,
            s3_key=versioned_key,
            filename=versioned_key.split("/")[-1],
            version=version,
            approved_by=approved_by,
        )
    except Exception as e:
        logger.error("DynamoDB record failed for approved JSON config s3_key=%s: %s", versioned_key, e)
        raise

    if uuid_mappings is not None:
        try:
            store_uuid_mappings(
                business_unit=bu,
                platform=platform,
                category=category,
                session_id=session_id,
                mappings=uuid_mappings,
                version=version,
            )
        except Exception as e:  # noqa: BLE001 - mappings are best-effort metadata
            logger.warning("Failed to store UUID mappings for approved config: %s", e)

    return versioned_key, latest_key


def get_latest_approved_json(bu: str, platform: str, category: str) -> str | None:
    """Fetch the latest approved (final) JSON config. Returns str or None."""
    return fetch_latest_approved_json(bu, platform, category)


def get_applicable_config_json(bu: str, platform: str, category: str) -> tuple[str | None, str]:
    """Resolve the config to run a NEW input file against (tasks 15, 17).

    Priority: latest approved -> latest confirmed. Returns ``(json, source)``
    where source is ``"approved"``, ``"confirmed"``, or ``"none"``.
    """
    approved = fetch_latest_approved_json(bu, platform, category)
    if approved:
        return approved, "approved"
    confirmed = fetch_latest_confirmed_json(bu, platform, category)
    if confirmed:
        return confirmed, "confirmed"
    return None, "none"


def get_latest_approved_record(bu: str, platform: str, category: str) -> dict | None:
    """Return the DynamoDB record for the latest approved config, or None."""
    return fetch_latest_approved_config(bu, platform, category)


def get_config_json_version(s3_key: str) -> str | None:
    """Fetch a specific stored config JSON by its exact S3 key. Returns str or None."""
    return fetch_config_json_by_key(s3_key)


def list_config_versions(bu: str, platform: str, category: str) -> list[dict]:
    """List stored config JSON versions (S3 objects) for a scope, newest first."""
    return list_config_object_versions(bu, platform, category)


def list_config_metadata_records(bu: str, platform: str, category: str) -> list[dict]:
    """List DynamoDB config metadata records (draft/confirmed/approved), newest first."""
    return list_config_records(bu, platform, category)


def get_uuid_mappings(bu: str, platform: str, category: str) -> dict | None:
    """Return the most recent persisted UUID-mappings record for a scope, or None."""
    return fetch_uuid_mappings(bu, platform, category)