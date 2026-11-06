# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - S3 file storage: input/output files, presigned URLs
# (tasks 7, 16, 20-21). REUSABLE as-is.
# ============================================================================
from __future__ import annotations

import logging
import mimetypes
import os
from typing import Union

import requests

from storage.s3.client import get_s3_client, sanitize_filename
from storage.dynamodb.client import sanitize_key

logger = logging.getLogger(__name__)

FILES_BUCKET     = os.getenv("S3_FILES_BUCKET", "agentic-data-validate-tranform-files")
PRESIGNED_EXPIRY = int(os.getenv("PRESIGNED_URL_EXPIRY_SECONDS", "3600"))

VALID_FILE_ROLES = {
    "input_reference_files",
    "output_reference_files",
    "input_files",
    "executed_output_files",
}

# Explicit overrides for types that mimetypes may not resolve correctly
_CONTENT_TYPE_OVERRIDES: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls":  "application/vnd.ms-excel",
    ".csv":  "text/csv",
    ".json": "application/json",
    ".pdf":  "application/pdf",
    ".zip":  "application/zip",
    ".txt":  "text/plain",
    ".xml":  "application/xml",
    ".parquet": "application/octet-stream",
}


def get_content_type(filename: str) -> str:
    """Resolve Content-Type from filename extension."""
    ext = os.path.splitext(filename)[-1].lower()
    if ext in _CONTENT_TYPE_OVERRIDES:
        return _CONTENT_TYPE_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def build_s3_key(session_id: str, file_role: str, filename: str, bu: str = "", platform: str = "", category: str = "", document_id: str = "") -> str:
    """
    Build a safe S3 key:
      sessions/<session_id>/<bu>/<platform>/<category>/<file_role>/<filename>/<document_id>
    document_id is always appended at the end of the path when provided.
    """
    session_id = sanitize_key(session_id, "session_id")
    filename   = sanitize_filename(filename)
    if file_role not in VALID_FILE_ROLES:
        raise ValueError(f"Invalid file_role: '{file_role}'. Must be one of {VALID_FILE_ROLES}")
    if bu and platform and category:
        bu       = sanitize_key(bu, "bu")
        platform = sanitize_key(platform, "platform")
        category = sanitize_key(category, "category")
        if document_id:
            return f"sessions/{session_id}/{bu}/{platform}/{category}/{file_role}/{document_id}/{filename}"
        return f"sessions/{session_id}/{bu}/{platform}/{category}/{file_role}/{filename}"
    else:
        if document_id:
            return f"sessions/{session_id}/{file_role}/{document_id}/{filename}"
        return f"sessions/{session_id}/{file_role}/{filename}"


def generate_presigned_upload_url(session_id: str, file_role: str, filename: str, bu: str = "", platform: str = "", category: str = "", document_id: str = "") -> tuple[str, str]:
    """
    Generate a presigned PUT URL for direct client-to-S3 upload.
    Pass document_id so the S3 key is scoped per document — prevents filename collisions
    when multiple files are uploaded in the same session.
    Content-Type is automatically derived from the filename extension.
    Returns (presigned_url, s3_key).
    """
    s3_key      = build_s3_key(session_id, file_role, filename, bu, platform, category, document_id)
    content_type = get_content_type(filename)
    url = get_s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket":      FILES_BUCKET,
            "Key":         s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGNED_EXPIRY,
    )
    logger.info("Generated presigned PUT URL session=%s role=%s file=%s content_type=%s", session_id, file_role, filename, content_type)
    return url, s3_key


def generate_presigned_download_url(s3_key: str, bucket: str = FILES_BUCKET) -> str:
    """Generate a presigned GET URL for downloading a file."""
    url = get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=PRESIGNED_EXPIRY,
    )
    logger.info("Generated presigned download URL key=%s", s3_key)
    return url


def upload_file_bytes(content: bytes, session_id: str, file_role: str, filename: str, bu: str, platform: str, category: str, document_id: str = "") -> str:
    """
    Direct server-side upload — used internally by execution pipeline.
    Pass document_id for executed_output_files to scope output under its source input file.
    Returns s3_key.
    """
    s3_key = build_s3_key(session_id, file_role, filename, bu, platform, category, document_id)
    get_s3_client().put_object(Bucket=FILES_BUCKET, Key=s3_key, Body=content)
    logger.info("Uploaded file bytes to s3_key=%s", s3_key)
    return s3_key


def download_file_bytes(s3_key: str, bucket: str = FILES_BUCKET) -> bytes:
    """Download raw bytes from S3."""
    resp = get_s3_client().get_object(Bucket=bucket, Key=s3_key)
    logger.debug("Downloaded file bytes from s3_key=%s", s3_key)
    return resp["Body"].read()
