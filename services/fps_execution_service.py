# ============================================================================
# Deliverable 8/10 - FPS execution orchestrator.
#
# Collects the user's input files during the execution process and drives the
# real ADP File Processing Service flow:
#   1. upload each file to the entity's SMB share (fps.client.upload_file_by_location)
#   2. record metadata in the DocumentsDvts table (+ keep an S3 backup copy)
#   3. trigger validation + transformation (fps.client.process_shared_files)
#   4. write an audit-trail entry (fps.client.record_audit)
#
# Files are persisted to DocumentsDvts (authoritative SMB location + S3 backup);
# the backend writes the transformed output to ``saveFileLocation`` on the share.
# ============================================================================
"""services.fps_execution_service - collect user files and run the FPS flow."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fps.client import FpsApiError, FpsClient
from fps.models import FpsConfig, FpsDocumentRef
from fps.paths import derive_source_key, generate_unique_id, resolve_entity_paths
from storage.dynamodb.document_repository import insert_fps_document
from storage.s3.file_repository import get_content_type, upload_file_bytes

logger = logging.getLogger(__name__)


class FpsExecutionError(RuntimeError):
    """Raised when the FPS execution flow cannot proceed."""


@dataclass
class UserFile:
    """One file collected from the user for execution."""

    filename: str
    content: bytes
    source_key: str = ""          # optional override; derived from filename if blank

    def resolved_source_key(self) -> str:
        return self.source_key.strip() or derive_source_key(self.filename)


@dataclass
class FpsExecutionResult:
    """Structured outcome of an FPS execution run."""

    success: bool
    unique_id: str
    save_file_location: str
    request_id: Optional[str]
    used_backend: bool
    documents: list[dict[str, Any]] = field(default_factory=list)
    audit_recorded: bool = False
    message: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "unique_id": self.unique_id,
            "save_file_location": self.save_file_location,
            "request_id": self.request_id,
            "used_backend": self.used_backend,
            "documents": self.documents,
            "audit_recorded": self.audit_recorded,
            "message": self.message,
        }


class FpsExecutionService:
    """Collect user files and execute them through the real FPS backend."""

    def __init__(
        self,
        client: Optional[FpsClient] = None,
        *,
        config: Optional[FpsConfig] = None,
        keep_s3_copy: bool = True,
    ) -> None:
        self._config = config or FpsConfig.from_env()
        self._client = client or FpsClient(self._config)
        self._keep_s3_copy = keep_s3_copy

    def execute(
        self,
        *,
        session_id: str,
        bu: str,
        platform: str,
        category: str,
        useremail: str,
        files: list[UserFile],
        form_data: Optional[dict[str, Any]] = None,
        unique_id: str = "",
    ) -> FpsExecutionResult:
        """Run the full collect -> upload -> persist -> process -> audit flow.

        Raises:
            FpsExecutionError: when no files are supplied, the backend is not
                configured, or an upload/process call fails.
        """
        if not files:
            raise FpsExecutionError("No input files were provided for execution.")
        if not self._config.configured:
            raise FpsExecutionError(
                "FPS backend is not configured. Set FPS_SHARED_FILE_BASE_URL and "
                "FPS_PROCESS_BASE_URL (and related FPS_* variables) in the environment."
            )

        run_id = unique_id.strip() or generate_unique_id()
        paths = resolve_entity_paths(bu, platform, category, run_id)

        logger.info(
            "[fps-exec] session=%s unique_id=%s files=%d -> upload_dir=%s",
            session_id, run_id, len(files), paths.upload_dir,
        )

        refs: list[FpsDocumentRef] = []
        doc_payloads: list[dict[str, Any]] = []

        # -- steps 1 & 2: upload each file, then persist its metadata --------
        for uf in files:
            source_key = uf.resolved_source_key()
            content_type = get_content_type(uf.filename)

            upload = self._client.upload_file_by_location(
                file_location=paths.upload_dir,
                file_name=uf.filename,
                content=uf.content,
                source_key=source_key,
                content_type=content_type,
            )
            refs.append(upload.to_ref())

            s3_key = self._maybe_store_s3_copy(
                session_id=session_id, bu=bu, platform=platform,
                category=category, uf=uf,
            )

            document_id = insert_fps_document(
                session_id=session_id,
                unique_id=run_id,
                source_key=source_key,
                smb_location=upload.location,
                filename=uf.filename,
                s3_key=s3_key,
                business_unit=bu,
                platform=platform,
                category=category,
                save_file_location=paths.save_location,
                useremail=useremail,
                upload_status="uploaded",
            )
            doc_payloads.append({
                "document_id": document_id,
                "source_key": source_key,
                "filename": uf.filename,
                "smb_location": upload.location,
                "s3_key": s3_key,
            })

        # -- step 3: trigger validation + transformation ---------------------
        try:
            process = self._client.process_shared_files(
                bu=bu, platform=platform, data_category=category,
                unique_id=run_id, save_file_location=paths.save_location,
                documents=refs, useremail=useremail, form_data=form_data,
            )
        except FpsApiError as exc:
            raise FpsExecutionError(str(exc)) from exc

        # -- step 4: audit (best-effort) -------------------------------------
        audit_ok = self._client.record_audit(
            bu=bu, platform=platform, data_category=category,
            unique_id=run_id, save_file_location=paths.save_location,
            documents=refs, useremail=useremail, form_data=form_data,
        )

        return FpsExecutionResult(
            success=process.success,
            unique_id=run_id,
            save_file_location=paths.save_location,
            request_id=process.request_id,
            used_backend=True,
            documents=doc_payloads,
            audit_recorded=audit_ok,
            message=process.message,
        )

    def get_status(
        self, *, bu: str, platform: str, category: str, useremail: str
    ) -> dict[str, Any]:
        """List request status for an entity (step 3 poll)."""
        if not self._config.configured:
            raise FpsExecutionError("FPS backend is not configured.")
        return self._client.get_request_status(
            bu=bu, platform=platform, data_category=category, useremail=useremail,
        )

    # -- internals ----------------------------------------------------------
    def _maybe_store_s3_copy(
        self, *, session_id: str, bu: str, platform: str, category: str, uf: UserFile
    ) -> str:
        """Best-effort S3 backup of the user file; never breaks the FPS flow."""
        if not self._keep_s3_copy:
            return ""
        try:
            return upload_file_bytes(
                content=uf.content,
                session_id=session_id,
                file_role="input_files",
                filename=uf.filename,
                bu=bu,
                platform=platform,
                category=category,
            )
        except Exception as exc:  # noqa: BLE001 - S3 backup is best-effort
            logger.warning("[fps-exec] S3 backup failed for %s: %s", uf.filename, exc)
            return ""
