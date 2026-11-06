# ============================================================================
# Deliverable 8/10 - FPS HTTP client (transport only).
#
# Implements the four calls the DataStudio execution flow makes:
#   * upload_file_by_location   -> shared-file host (multipart)
#   * process_shared_files      -> fps host (JSON)
#   * get_request_status        -> fps host (multipart)
#   * record_audit              -> audit host (JSON, jwttoken header)
#
# Staging authenticates with ``app-code`` alone; the audit host uses a
# ``jwttoken`` header. Transient transport errors are retried; a definitive
# failure raises :class:`FpsApiError` so the orchestrator can stop cleanly.
# ============================================================================
"""fps.client - synchronous HTTP client for the FPS staging services."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from fps.models import (
    FpsConfig,
    FpsDocumentRef,
    FpsProcessResult,
    FpsUploadResult,
)

logger = logging.getLogger(__name__)


class FpsApiError(RuntimeError):
    """Raised when an FPS call fails definitively."""


class FpsClient:
    """Synchronous client for the ADP File Processing Service."""

    def __init__(
        self,
        config: Optional[FpsConfig] = None,
        *,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._config = config or FpsConfig.from_env()
        self._client = client  # injectable for tests

    @property
    def config(self) -> FpsConfig:
        return self._config

    # -- context management -------------------------------------------------
    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
            headers={"origin": self._config.origin, "referer": self._config.origin + "/"},
        )

    # -- step 1: upload a file to the SMB share -----------------------------
    def upload_file_by_location(
        self,
        *,
        file_location: str,
        file_name: str,
        content: bytes,
        source_key: str,
        content_type: str = "application/octet-stream",
    ) -> FpsUploadResult:
        """POST one file to ``/ahub/v1/shared-file/upload-file-by-location``."""
        url = self._config.shared_file_base_url + self._config.upload_path
        data = {"fileLocation": file_location, "fileName": file_name}
        files = {"file": (file_name, content, content_type)}
        headers = {"app-code": self._config.app_code, "accept": "*/*"}

        response = self._send_with_retry(
            method="POST", url=url, headers=headers, data=data, files=files,
        )
        envelope = _safe_json(response)
        success = response.is_success and bool(envelope.get("success", True))
        result = FpsUploadResult(
            file_name=file_name,
            source_key=source_key,
            file_location=file_location,
            location=f"{file_location}{file_name}",
            success=success,
            status_code=response.status_code,
            message=str(envelope.get("message") or ("uploaded" if success else "upload failed")),
            raw=_trim(envelope),
        )
        if not success:
            raise FpsApiError(
                f"FPS upload failed for '{file_name}' "
                f"(status={response.status_code}): {result.message}"
            )
        logger.info("[fps] uploaded %s -> %s", file_name, result.location)
        return result

    # -- step 2: trigger validation + transformation ------------------------
    def process_shared_files(
        self,
        *,
        bu: str,
        platform: str,
        data_category: str,
        unique_id: str,
        save_file_location: str,
        documents: list[FpsDocumentRef],
        useremail: str,
        form_data: Optional[dict[str, Any]] = None,
    ) -> FpsProcessResult:
        """POST the process trigger to ``/v2/shared-files/process``."""
        url = self._config.process_base_url + self._config.process_path
        payload = self._build_process_payload(
            bu=bu, platform=platform, data_category=data_category,
            unique_id=unique_id, save_file_location=save_file_location,
            documents=documents, useremail=useremail, form_data=form_data,
        )
        headers = {"content-type": "application/json", "accept": "*/*"}
        response = self._send_with_retry(
            method="POST", url=url, headers=headers, json_body=payload,
        )
        envelope = _safe_json(response)
        success = response.is_success and bool(envelope.get("success", response.is_success))
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        request_id = (
            envelope.get("requestId")
            or (data.get("requestId") if isinstance(data, dict) else None)
            or (data.get("id") if isinstance(data, dict) else None)
        )
        result = FpsProcessResult(
            success=success,
            status_code=response.status_code,
            unique_id=unique_id,
            request_id=str(request_id) if request_id else None,
            message=str(envelope.get("message") or ("processing" if success else "process failed")),
            raw=_trim(envelope),
        )
        if not success:
            raise FpsApiError(
                f"FPS process trigger failed (status={response.status_code}): {result.message}"
            )
        logger.info("[fps] process triggered unique_id=%s request_id=%s", unique_id, result.request_id)
        return result

    def _build_process_payload(
        self,
        *,
        bu: str,
        platform: str,
        data_category: str,
        unique_id: str,
        save_file_location: str,
        documents: list[FpsDocumentRef],
        useremail: str,
        form_data: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "bu": bu,
            "platform": platform,
            "dataCategory": data_category,
            "uniqueId": unique_id,
            "saveFileLocation": save_file_location,
            "form_data": json.dumps(form_data or {}),
            "documents": [d.as_process_document() for d in documents],
            "useremail": useremail,
            "source": self._config.source,
        }

    # -- step 3: list / poll request status ---------------------------------
    def get_request_status(
        self,
        *,
        bu: str,
        platform: str,
        data_category: str,
        useremail: str,
        process_type: str = "null",
        is_admin: bool = True,
    ) -> dict[str, Any]:
        """POST to ``/v2/request/by`` (multipart) and return the parsed body."""
        url = self._config.process_base_url + self._config.request_by_path
        data = {
            "bu": bu,
            "platform": platform,
            "dataCategory": data_category,
            "processType": process_type,
            "source": self._config.source,
            "useremail": useremail,
            "isAdmin": "true" if is_admin else "false",
        }
        headers = {"accept": "*/*"}
        response = self._send_with_retry(
            method="POST", url=url, headers=headers, data=data, files={},
        )
        return _safe_json(response)

    # -- step 4: audit ------------------------------------------------------
    def record_audit(
        self,
        *,
        bu: str,
        platform: str,
        data_category: str,
        unique_id: str,
        save_file_location: str,
        documents: list[FpsDocumentRef],
        useremail: str,
        form_data: Optional[dict[str, Any]] = None,
        action: str = "File Processing",
    ) -> bool:
        """POST an audit entry to ``/audits`` (best-effort, never raises)."""
        if not self._config.audit_base_url:
            logger.info("[fps] audit skipped: FPS_AUDIT_BASE_URL not configured")
            return False
        url = self._config.audit_base_url + self._config.audit_path
        value = self._build_process_payload(
            bu=bu, platform=platform, data_category=data_category,
            unique_id=unique_id, save_file_location=save_file_location,
            documents=documents, useremail=useremail, form_data=form_data,
        )
        payload = {
            "value": value,
            "esid": self._config.audit_esid,
            "teamId": self._config.audit_team_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "action": action,
        }
        headers = {"content-type": "application/json", "accept": "*/*"}
        if self._config.jwt_token:
            headers["jwttoken"] = self._config.jwt_token
        try:
            response = self._send_with_retry(
                method="POST", url=url, headers=headers, json_body=payload,
            )
            ok = response.is_success
            if not ok:
                logger.warning("[fps] audit returned status=%s", response.status_code)
            return ok
        except FpsApiError as exc:
            logger.warning("[fps] audit failed (ignored): %s", exc)
            return False

    # -- transport ----------------------------------------------------------
    def _send_with_retry(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        data: Optional[dict[str, Any]] = None,
        files: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        owns_client = self._client is None
        client = self._client or self._new_client()
        last_exc: Optional[Exception] = None
        try:
            for attempt in range(self._config.max_retries + 1):
                try:
                    return client.request(
                        method, url, headers=headers,
                        data=data, files=files, json=json_body,
                    )
                except httpx.HTTPError as exc:
                    last_exc = exc
                    if attempt < self._config.max_retries:
                        time.sleep(min(0.5 * (attempt + 1), 2.0))
                        continue
        finally:
            if owns_client:
                client.close()
        raise FpsApiError(f"FPS transport error calling {url}: {last_exc}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {"data": body}


def _trim(envelope: dict[str, Any], limit: int = 4000) -> dict[str, Any]:
    """Cap the stored raw envelope so history payloads stay small."""
    try:
        text = json.dumps(envelope)
    except (TypeError, ValueError):
        return {}
    if len(text) <= limit:
        return envelope
    return {"_truncated": True, "_preview": text[:limit]}
