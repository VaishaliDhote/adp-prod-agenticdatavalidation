# ============================================================================
# Deliverable 10 - Final JSON storage workflow (tasks 13, 14, 22, 23).
#
# Marks a validated configuration as the final, approved configuration for future
# execution. Validates the complete JSON against the exact contract, stores it
# under the approved/ prefix (versioned + latest) with an approval record, derives
# and persists UUID mappings (task 8) for deterministic future block lookup,
# refreshes the human summary, and records an audit event.
# ============================================================================
"""
services.config_finalization_service
====================================
Validate -> store -> mark-approved workflow for the final DVTS configuration.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from contract.models import ConfigurationContract
from contract.validation import validate_contract
from knowledge.audit_store import EVENT_CONFIG_APPROVED, record_audit_event
from knowledge.config_store import store_approved_json
from services.json_config_summary import JsonConfigSummaryBuilder
from storage.dynamodb.business_repository import upsert_category_summary
from utils.config_block_locator import (
    ConfigBlockLocator,
    ConfigBlockLocatorError,
    SECTION_CONFIGURATION_DATA,
    SECTION_DATASET_TRANSFORMATION_RULES,
    SECTION_TRANSFORMATION_RULES,
    SECTION_VALIDATION_RULES,
)

logger = logging.getLogger(__name__)

_S3_CONFIGS_BUCKET = os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs")


class ConfigFinalizationError(ValueError):
    """Raised when a configuration cannot be finalized (invalid / missing)."""


@dataclass
class FinalizationResult:
    """Structured outcome of a finalize call (tasks 13, 14)."""

    approved: bool
    versioned_s3_key: str
    latest_s3_key: str
    approved_by: str
    validation_warnings: list[str] = field(default_factory=list)
    uuid_mappings: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "versioned_s3_key": self.versioned_s3_key,
            "latest_s3_key": self.latest_s3_key,
            "approved_by": self.approved_by,
            "validation_warnings": self.validation_warnings,
            "uuid_mapping_counts": {
                "files": len(self.uuid_mappings.get("files", [])),
                "sheets": len(self.uuid_mappings.get("sheets", [])),
                "columns": len(self.uuid_mappings.get("columns", [])),
                "rules": len(self.uuid_mappings.get("rules", [])),
            },
        }


class ConfigFinalizationService:
    """Validate and mark a configuration as the final approved config."""

    def __init__(self, s3_bucket: str = _S3_CONFIGS_BUCKET) -> None:
        self._s3_bucket = s3_bucket
        self._summary_builder = JsonConfigSummaryBuilder()

    def finalize(
        self,
        bu: str,
        platform: str,
        category: str,
        session_id: str,
        json_content: str,
        *,
        approved_by: str = "",
    ) -> FinalizationResult:
        """Validate, store as approved, persist UUID mappings, and audit.

        Raises:
            ConfigFinalizationError: when the JSON is missing or contract-invalid.
        """
        if not json_content or not json_content.strip():
            raise ConfigFinalizationError("No configuration JSON provided to finalize.")

        # Contract validation before we mark anything as final (task 24 re-check).
        report = validate_contract(json_content)
        if not report.is_valid:
            errors = "; ".join(f"{i.location}: {i.message}" for i in report.errors[:5])
            raise ConfigFinalizationError(
                f"Refusing to approve an invalid configuration: {errors}"
            )
        warnings = [f"{i.location}: {i.message}" for i in report.warnings]

        # Derive UUID mappings from the (now valid) document (task 8).
        uuid_mappings = self._extract_uuid_mappings(json_content)

        # Store as approved (S3 versioned+latest, DynamoDB record, UUID mappings).
        try:
            versioned_key, latest_key = store_approved_json(
                bu=bu,
                platform=platform,
                category=category,
                session_id=session_id,
                json_content=json_content,
                s3_bucket=self._s3_bucket,
                approved_by=approved_by,
                uuid_mappings=uuid_mappings,
            )
        except Exception as exc:  # noqa: BLE001 - normalize storage failures
            logger.exception("[finalize] failed to store approved config")
            raise ConfigFinalizationError(f"Failed to store approved configuration: {exc}") from exc

        # Refresh the human summary cache (best-effort).
        summary = ""
        try:
            summary = self._summary_builder.build_markdown(json_content)
            upsert_category_summary(bu, platform, category, summary)
        except Exception as exc:  # noqa: BLE001 - summary cache is best-effort
            logger.warning("[finalize] failed to refresh summary: %s", exc)

        # Audit the approval (best-effort; never breaks the request).
        record_audit_event(
            bu, platform, category, EVENT_CONFIG_APPROVED,
            actor=approved_by, session_id=session_id,
            detail={
                "versioned_s3_key": versioned_key,
                "latest_s3_key": latest_key,
                "warning_count": len(warnings),
            },
        )

        logger.info(
            "[finalize] approved config bu=%s platform=%s category=%s by=%s key=%s",
            bu, platform, category, approved_by or "?", versioned_key,
        )
        return FinalizationResult(
            approved=True,
            versioned_s3_key=versioned_key,
            latest_s3_key=latest_key,
            approved_by=approved_by,
            validation_warnings=warnings,
            uuid_mappings=uuid_mappings,
            summary=summary,
        )

    # -- UUID mapping extraction (task 8) -----------------------------------
    @staticmethod
    def _extract_uuid_mappings(json_content: str) -> dict[str, Any]:
        """Extract file/sheet/column/rule -> UUID mappings from the contract.

        Deterministic walk of the exact document; never raises (returns a partial
        mapping on any structural surprise).
        """
        try:
            locator = ConfigBlockLocator.from_json(json_content)
        except ConfigBlockLocatorError:
            return {"files": [], "sheets": [], "columns": [], "rules": []}

        data = locator.document.get("data", {})
        config_data = data.get(SECTION_CONFIGURATION_DATA, {}) or {}

        # Files + sheets from filesInfo (a JSON-string blob or a list).
        files: list[dict[str, Any]] = []
        sheets: list[dict[str, Any]] = []
        files_info = config_data.get("filesInfo")
        entries: list[dict[str, Any]] = []
        if isinstance(files_info, str) and files_info.strip():
            try:
                entries = json.loads(files_info)
            except (json.JSONDecodeError, ValueError):
                entries = []
        elif isinstance(files_info, list):
            entries = files_info
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            source_key = entry.get("sourceKey")
            if source_key is None:
                continue
            files.append({"sourceKey": source_key, "type": entry.get("type")})
            sheets.append({"sourceKey": source_key})

        # Columns from the column-relations catalog.
        columns = [
            {
                "uuid": c.uuid,
                "columnName": c.column_name,
                "sourceKey": c.source_key,
                "position": c.position,
            }
            for c in locator.columns()
        ]

        # Rule -> anchor UUID references.
        rules: list[dict[str, Any]] = []
        for row in data.get(SECTION_VALIDATION_RULES, []) or []:
            uid = row.get("sourceColumn")
            if uid:
                rules.append({"section": SECTION_VALIDATION_RULES, "columnUuid": uid, "id": row.get("id")})
        for row in data.get(SECTION_TRANSFORMATION_RULES, []) or []:
            uid = row.get("sourceColumnName")
            if uid:
                rules.append({
                    "section": SECTION_TRANSFORMATION_RULES,
                    "columnUuid": uid,
                    "targetColumnName": row.get("targetColumnName"),
                    "id": row.get("id"),
                })
        for row in data.get(SECTION_DATASET_TRANSFORMATION_RULES, []) or []:
            uid = row.get("fieldUUID")
            if uid:
                rules.append({"section": SECTION_DATASET_TRANSFORMATION_RULES, "fieldUuid": uid, "id": row.get("id")})

        return {"files": files, "sheets": sheets, "columns": columns, "rules": rules}

    @staticmethod
    def to_contract(json_content: str) -> ConfigurationContract:
        """Parse the finalized JSON into a typed contract (for downstream reapply)."""
        return ConfigurationContract.model_validate_json_document(json_content)


__all__ = [
    "ConfigFinalizationService",
    "FinalizationResult",
    "ConfigFinalizationError",
]
