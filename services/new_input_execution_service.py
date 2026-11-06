# ============================================================================
# Deliverable 10 - New-input (future) execution workflow (tasks 15-22).
#
# The production flow: a new input file arrives, DVTS identifies the applicable
# APPROVED configuration (falling back to confirmed), sends the config to the
# existing execution API (composed block-by-block push, reusing Deliverable 8/9),
# handles the validation response, and surfaces the transformed output for the
# user. There is no single "execute input + config" endpoint in the backend and
# output retrieval is a manual network-share -> drop-folder step, so this service
# automates everything that can be automated and degrades gracefully offline via
# the deterministic simulator.
# ============================================================================
"""
services.new_input_execution_service
====================================
Retrieve the applicable stored configuration and execute a new input file
through the existing API, returning a display-ready result.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from contract.models import ConfigurationContract
from knowledge.audit_store import (
    EVENT_EXECUTION_ATTEMPTED,
    EVENT_EXECUTION_FAILED,
    EVENT_EXECUTION_SUCCEEDED,
    record_audit_event,
)
from knowledge.config_store import get_applicable_config_json
from services.config_reapply_service import ConfigReapplyError, ConfigReapplyService
from storage.dynamodb.document_repository import fetch_documents_by_session
from storage.s3.file_repository import (
    FILES_BUCKET,
    generate_presigned_download_url,
)

logger = logging.getLogger(__name__)

_S3_FILES_BUCKET = os.getenv("S3_FILES_BUCKET", FILES_BUCKET)


class NewInputExecutionError(RuntimeError):
    """Raised when a new-input execution cannot proceed (no config / bad config)."""


class ConfigNotFoundError(NewInputExecutionError):
    """Raised when no approved or confirmed config exists for the scope."""


@dataclass
class NewInputExecutionResult:
    """Structured outcome of a new-input execution (tasks 15-22)."""

    success: bool
    config_source: str  # "approved" | "confirmed"
    run_id: str
    used_simulator: bool
    input_files: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    output_comparison: Optional[dict[str, Any]] = None
    output_comparison_pending: bool = False
    output_download_url: Optional[str] = None
    message: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "config_source": self.config_source,
            "run_id": self.run_id,
            "used_simulator": self.used_simulator,
            "input_files": self.input_files,
            "blocks": self.blocks,
            "rejections": self.rejections,
            "output_comparison": self.output_comparison,
            "output_comparison_pending": self.output_comparison_pending,
            "output_download_url": self.output_download_url,
            "message": self.message,
        }


class NewInputExecutionService:
    """Execute a new input file against the applicable stored configuration."""

    def __init__(self, reapply_service: Optional[ConfigReapplyService] = None) -> None:
        self._reapply = reapply_service or ConfigReapplyService()

    def execute(
        self,
        bu: str,
        platform: str,
        category: str,
        session_id: str,
        *,
        input_document_id: str = "",
        auth_token: Optional[str] = None,
        compare_output: bool = False,
        produced_output_file: Optional[str] = None,
        known_output_file: Optional[str] = None,
        output_sheet: Optional[str] = None,
        actor: str = "",
    ) -> NewInputExecutionResult:
        """Run the future-execution workflow for a new input file.

        Raises:
            ConfigNotFoundError: when no approved/confirmed config exists.
            NewInputExecutionError: when the stored config is not parseable or the
                backend push fails unexpectedly.
        """
        record_audit_event(
            bu, platform, category, EVENT_EXECUTION_ATTEMPTED,
            actor=actor, session_id=session_id,
            detail={"input_document_id": input_document_id or None},
        )

        # 1. Retrieve the applicable stored JSON (task 17): approved -> confirmed.
        config_json, source = get_applicable_config_json(bu, platform, category)
        if not config_json:
            record_audit_event(
                bu, platform, category, EVENT_EXECUTION_FAILED,
                actor=actor, session_id=session_id,
                detail={"reason": "no_applicable_config"},
            )
            raise ConfigNotFoundError(
                f"No approved or confirmed configuration found for {bu}/{platform}/{category}."
            )

        # 2. Parse + re-validate the stored config into a typed contract (task 24).
        try:
            contract = ConfigurationContract.model_validate_json_document(config_json)
        except Exception as exc:  # noqa: BLE001 - normalize to a domain error
            record_audit_event(
                bu, platform, category, EVENT_EXECUTION_FAILED,
                actor=actor, session_id=session_id,
                detail={"reason": "config_parse_failed", "error": str(exc)},
            )
            raise NewInputExecutionError(
                f"Stored configuration for {bu}/{platform}/{category} is not valid: {exc}"
            ) from exc

        # 3. Resolve the new input file(s) for this session (task 16). Best-effort:
        #    surfaces file metadata + a presigned download so the caller can trace
        #    exactly which input was executed; never blocks execution.
        input_files = self._resolve_input_files(session_id, input_document_id)

        # 4. Push the config through the existing API (task 18) and handle the
        #    validation response (task 19). Reuses the Deliverable 8/9 primitives;
        #    falls back to the offline simulator when no backend URL is configured.
        try:
            reapply = self._reapply.reapply(
                contract,
                bu=bu,
                platform=platform,
                category=category,
                session_id=session_id,
                auth_token=auth_token,
                compare_output=compare_output,
                produced_output_file=produced_output_file,
                known_output_file=known_output_file,
                output_sheet=output_sheet,
            )
        except ConfigReapplyError as exc:
            record_audit_event(
                bu, platform, category, EVENT_EXECUTION_FAILED,
                actor=actor, session_id=session_id,
                detail={"reason": "backend_push_failed", "error": str(exc)},
            )
            raise NewInputExecutionError(str(exc)) from exc

        # 5. Surface the transformed output (tasks 20, 21): presigned download for
        #    a produced/executed output file when one is available in S3.
        output_url = self._resolve_output_download_url(session_id, input_document_id)

        result = NewInputExecutionResult(
            success=reapply.success,
            config_source=source,
            run_id=reapply.run_id,
            used_simulator=reapply.used_simulator,
            input_files=input_files,
            blocks=reapply.blocks,
            rejections=reapply.rejections,
            output_comparison=reapply.output_comparison,
            output_comparison_pending=reapply.output_comparison_pending,
            output_download_url=output_url,
            message=(
                reapply.message
                if reapply.success
                else "The execution API rejected the configuration for this input."
            ),
        )

        record_audit_event(
            bu, platform, category,
            EVENT_EXECUTION_SUCCEEDED if reapply.success else EVENT_EXECUTION_FAILED,
            actor=actor, session_id=session_id,
            detail={
                "config_source": source,
                "run_id": reapply.run_id,
                "used_simulator": reapply.used_simulator,
                "rejection_count": len(reapply.rejections),
            },
        )
        logger.info(
            "[new-input-exec] bu=%s platform=%s category=%s source=%s success=%s run=%s",
            bu, platform, category, source, reapply.success, reapply.run_id,
        )
        return result

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _resolve_input_files(session_id: str, input_document_id: str) -> list[dict[str, Any]]:
        """Return input file metadata (+ presigned download) for the session.

        Never raises: input-file resolution is informational for the caller and
        must not block execution.
        """
        try:
            docs = fetch_documents_by_session(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[new-input-exec] could not list session documents: %s", exc)
            return []

        input_like = [
            d for d in docs
            if str(d.get("file_type", "")).upper() in {"INPUT_FILE", "INPUT_REF"}
        ]
        if input_document_id:
            input_like = [d for d in input_like if d.get("document_id") == input_document_id] or input_like

        resolved: list[dict[str, Any]] = []
        for d in input_like:
            entry = {
                "document_id": d.get("document_id"),
                "filename": d.get("filename"),
                "file_type": d.get("file_type"),
                "s3_key": d.get("s3_key"),
            }
            try:
                if d.get("s3_key"):
                    entry["download_url"] = generate_presigned_download_url(
                        d["s3_key"], bucket=d.get("s3_bucket") or _S3_FILES_BUCKET
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[new-input-exec] presign failed for %s: %s", d.get("s3_key"), exc)
            resolved.append(entry)
        return resolved

    @staticmethod
    def _resolve_output_download_url(session_id: str, input_document_id: str) -> Optional[str]:
        """Presigned download for the most recent executed output, if any (task 21)."""
        try:
            outputs = fetch_documents_by_session(session_id, file_type="EXECUTED_OUTPUT")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[new-input-exec] could not list executed outputs: %s", exc)
            return None
        if not outputs:
            return None
        if input_document_id:
            scoped = [o for o in outputs if o.get("document_id") == input_document_id]
            outputs = scoped or outputs
        latest = max(outputs, key=lambda o: o.get("created_at", ""))
        s3_key = latest.get("s3_key")
        if not s3_key:
            return None
        try:
            return generate_presigned_download_url(
                s3_key, bucket=latest.get("s3_bucket") or _S3_FILES_BUCKET
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[new-input-exec] presign output failed for %s: %s", s3_key, exc)
            return None


__all__ = [
    "NewInputExecutionService",
    "NewInputExecutionResult",
    "NewInputExecutionError",
    "ConfigNotFoundError",
]
