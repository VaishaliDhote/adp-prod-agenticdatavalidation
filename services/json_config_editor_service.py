# ============================================================================
# Deliverable 9 - Targeted-editing business logic (JSON contract).
# One conversational turn:
#   1. Load chat history.
#   2. Interpret the user's request with the LLM into a STRUCTURED edit
#      descriptor (target column + section + minimal field edits) — the LLM never
#      rewrites the full JSON (tasks 10, 19).
#   3. Resolve the target column by UUID/name (ask to clarify on unknown/ambiguous),
#      locate (or minimally seed) the single contract row (tasks 13-17).
#   4. Apply the edit deterministically and splice exactly one row back, preserving
#      every other block byte-for-byte (tasks 20-22).
#   5. Validate the reconstructed document against the exact JSON contract (task 24).
#   6. Save the edited draft JSON to S3 and return a display-ready diff.
# On confirm, regenerate the review summary and store the confirmed JSON.
# ============================================================================
"""
services.json_config_editor_service
====================================
Conversational, UUID-anchored, contract-safe JSON configuration editor.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Optional

from contract.models import ConfigurationContract
from contract.validation import validate_contract
from knowledge.config_store import (
    get_config_json_for_session,
    get_latest_confirmed_json,
    store_confirmed_json,
)
from llm_client import LLMClient
from prompts.json_edit_prompt import JSON_EDIT_SYSTEM_PROMPT
from storage.dynamodb.business_repository import upsert_category_summary
from storage.dynamodb.chat_repository import ChatMessage, ChatRepository
from services.json_config_summary import JsonConfigSummaryBuilder
from utils.config_block_locator import (
    ConfigBlockLocator,
    ConfigBlockLocatorError,
    SECTION_COLUMN_RELATIONS,
    SECTION_CONFIGURATION_DATA,
    SECTION_TRANSFORMATION_RULES,
    SECTION_VALIDATION_RULES,
    anchor_field_for,
)
from utils.config_row_editor import (
    ConfigRowEditor,
    FieldEdit,
    RowEdit,
    RowEditError,
    editable_fields,
)

logger = logging.getLogger(__name__)

_S3_CONFIGS_BUCKET = os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs")

_SECTIONS_EDITABLE = {
    SECTION_VALIDATION_RULES,
    SECTION_TRANSFORMATION_RULES,
    SECTION_COLUMN_RELATIONS,
}


def _extract_json_block(text: str) -> str:
    """Extract the first ```json ... ``` block; fall back to the first {...}."""
    if not text:
        return ""
    match = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: a bare object.
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    return match.group(1).strip() if match else ""


class JsonConfigEditError(ValueError):
    """Raised for unrecoverable JSON edit failures (bad document, etc.)."""


def get_config_json_with_source(
    bu: str, platform: str, category: str, session_id: str
) -> dict[str, Any]:
    """Load the JSON config for an edit session, reporting its source.

    Returns ``{"json": str, "source": "draft"|"confirmed"|"none"}``.
    """
    from storage.s3.config_repository import (
        fetch_latest_draft_json,
        fetch_latest_confirmed_json,
    )

    draft = fetch_latest_draft_json(bu, platform, category, session_id)
    if draft:
        return {"json": draft, "source": "draft"}
    confirmed = fetch_latest_confirmed_json(bu, platform, category)
    if confirmed:
        return {"json": confirmed, "source": "confirmed"}
    return {"json": "", "source": "none"}


class JsonConfigEditorService:
    """Interpret + apply one targeted JSON edit per conversational turn."""

    def __init__(self, session_id: str = "") -> None:
        self._client = LLMClient(session_id=session_id)
        self._repo = ChatRepository()
        self._row_editor = ConfigRowEditor()
        self._summary_builder = JsonConfigSummaryBuilder()
        self.session_id = session_id

    # -- one turn -----------------------------------------------------------
    async def process_turn(
        self,
        bu: str,
        platform: str,
        category: str,
        user_query: str,
        current_json: str,
        config_version: str = "",
    ) -> dict[str, Any]:
        """Run one edit turn; returns a stable result dict (never rewrites full JSON)."""
        try:
            locator = ConfigBlockLocator.from_json(current_json)
        except ConfigBlockLocatorError as exc:
            raise JsonConfigEditError(f"Stored configuration is not valid JSON: {exc}") from exc

        history = self._safe_history()
        context = self._build_context(locator, user_query, history)

        raw = await self._client.generate_response(
            system_prompt=JSON_EDIT_SYSTEM_PROMPT,
            context=context,
        )
        self._store_turn(bu, platform, category, user_query, raw, config_version)

        descriptor = self._parse_descriptor(raw)
        if descriptor is None:
            return self._reply(
                "I couldn't interpret that as a specific change. Could you say which "
                "column and what you'd like to change (e.g. \"make Employee ID mandatory\")?"
            )

        if descriptor.get("action") == "clarify":
            return self._reply(str(descriptor.get("question") or
                                  "Could you clarify which column and change you mean?"))

        # action == "edit"
        return self._apply_edit(bu, platform, category, locator, descriptor)

    # -- edit application ---------------------------------------------------
    def _apply_edit(
        self,
        bu: str,
        platform: str,
        category: str,
        locator: ConfigBlockLocator,
        descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        target = descriptor.get("target") or {}
        column_token = str(target.get("column") or "").strip()
        section = str(target.get("section") or "").strip()

        if section not in _SECTIONS_EDITABLE:
            return self._reply(
                f"I can edit validation, transformation, or column-catalog settings, "
                f"but not {section!r}. What would you like to change?"
            )

        matches = locator.resolve_column(column_token)
        if not matches:
            return self._reply(
                f"I couldn't find a column named {column_token!r}. Please use one of the "
                f"columns shown in the summary."
            )
        if len(matches) > 1:
            names = ", ".join(f"{m.column_name} (`{m.uuid}`)" for m in matches)
            return self._reply(
                f"Multiple columns match {column_token!r}: {names}. "
                f"Which one did you mean (use the UUID)?"
            )
        column = matches[0]

        # Locate (or minimally seed) the row for this column + section.
        located_row, anchor_value = self._locate_or_seed(locator, section, column)
        if located_row is None:
            return self._reply(
                f"There is no editable {section} row for column {column.column_name!r}."
            )

        edits = self._build_field_edits(descriptor.get("edits"))
        if not edits:
            return self._reply(
                "I didn't detect any concrete field change. Could you restate what to change?"
            )

        try:
            result = self._row_editor.apply(
                section, located_row, RowEdit(section=section, anchor_value=anchor_value, edits=edits)
            )
        except RowEditError as exc:
            return self._reply(f"I can't apply that change: {exc}")

        if not result.changed_fields:
            return self._reply(
                f"No change needed — {column.column_name!r} already has those settings."
            )

        # Splice exactly one row back; all other blocks are preserved verbatim.
        try:
            locator.splice_row(section, anchor_value, result.new_row)
        except ConfigBlockLocatorError as exc:
            raise JsonConfigEditError(str(exc)) from exc

        proposed_json = locator.to_json(indent=2)

        # Validate the reconstructed document against the exact JSON contract.
        report = validate_contract(proposed_json)
        warnings = [f"{i.location}: {i.message}" for i in report.warnings]
        if not report.is_valid:
            errors = "; ".join(f"{i.location}: {i.message}" for i in report.errors[:5])
            return self._reply(
                f"That change would make the configuration invalid ({errors}). "
                f"I've left the configuration unchanged."
            )

        changes = self._describe_changes(section, column.column_name, result)
        explanation = str(descriptor.get("explanation") or "").strip()
        reply = explanation or self._default_reply(section, column.column_name, result)

        return {
            "reply": reply,
            "confirmed": False,
            "proposed_json": proposed_json,
            "changes": changes,
            "changes_display": self._changes_display(changes),
            "affected_section": section,
            "affected_uuid": column.uuid,
            "affected_columns": [column.column_name],
            "validation_warnings": warnings,
        }

    def _locate_or_seed(
        self,
        locator: ConfigBlockLocator,
        section: str,
        column,
    ) -> tuple[Optional[dict[str, Any]], str]:
        """Return (row, anchor_value). Seeds a minimal row if none exists yet."""
        if section == SECTION_COLUMN_RELATIONS:
            located = locator.find_column_relation(column.uuid)
            return (located.row if located else None), column.uuid

        if section == SECTION_VALIDATION_RULES:
            located = locator.find_validation_rule(column.uuid)
            if located:
                return located.row, column.uuid
            return self._seed_validation_row(locator, column), column.uuid

        if section == SECTION_TRANSFORMATION_RULES:
            located_list = locator.find_transformation_rules(column.uuid)
            if located_list:
                # Edit the first matching row; anchor is the source uuid.
                return located_list[0].row, column.uuid
            return self._seed_transformation_row(locator, column), column.uuid

        return None, column.uuid

    def _seed_validation_row(self, locator: ConfigBlockLocator, column) -> Optional[dict[str, Any]]:
        cfg = self._config_data(locator)
        if cfg is None:
            return None
        row = {
            "bu": cfg.get("bu", ""),
            "columnRequiredInErrorFile": None,
            "conditionalValidationRule": None,
            "dataCategory": cfg.get("dataCategory", ""),
            "dataExclusionRules": None,
            "dataFormat": column.fmt,
            "dataTransformationRules": None,
            "dataType": column.data_type or "Text",
            "dependsOn": None,
            "errorMessageInfo": None,
            "id": None,
            "isMandatory": None,
            "isSkipValidaitons": False,
            "lovCheckType": None,
            "lovValidationRequired": None,
            "maxLengthAllowed": None,
            "maxValue": None,
            "minLengthAllowed": None,
            "minValue": None,
            "platform": cfg.get("platform", ""),
            "sourceColumn": column.uuid,
            "sourceColumnName": None,
            "specialCharNotAllowed": None,
            "specialCharToBeRemoved": None,
            "stringCheckRule": None,
            "subDataCategory": None,
            "transformationRequired": None,
            "uniqueValueInColumn": None,
            "useremail": cfg.get("useremail", ""),
            "userrole": cfg.get("userrole", ""),
            "validationRuleType": "client",
        }
        return row

    def _seed_transformation_row(self, locator: ConfigBlockLocator, column) -> Optional[dict[str, Any]]:
        cfg = self._config_data(locator)
        if cfg is None:
            return None
        row = {
            "id": None,
            "bu": cfg.get("bu", ""),
            "platform": cfg.get("platform", ""),
            "dataCategory": cfg.get("dataCategory", ""),
            "subDataCategory": None,
            "sourceColumnName": column.uuid,
            "dataType": None,
            "dataFormat": None,
            "targetColumnName": column.column_name,
            "targetFileName": "<null>",
            "columnSequence": column.position,
            "defaultValue": None,
            "dataTransformationRules": None,
            "specialCharToBeRemoved": None,
            "lovCheckType": None,
            "transformationRequired": None,
            "lovValidationRequired": None,
            "dependsOn": None,
            "useremail": cfg.get("useremail", ""),
            "userrole": cfg.get("userrole", ""),
        }
        return row

    @staticmethod
    def _config_data(locator: ConfigBlockLocator) -> Optional[dict[str, Any]]:
        cfg = locator.document.get("data", {}).get(SECTION_CONFIGURATION_DATA)
        return cfg if isinstance(cfg, dict) else None

    # -- context / descriptor ----------------------------------------------
    def _build_context(
        self, locator: ConfigBlockLocator, user_query: str, history: list[dict[str, str]]
    ) -> str:
        data = locator.document.get("data", {})
        val_index = {
            r.get("sourceColumn")
            for r in data.get(SECTION_VALIDATION_RULES, []) or []
        }
        txf_index = {
            r.get("sourceColumnName")
            for r in data.get(SECTION_TRANSFORMATION_RULES, []) or []
        }
        catalog = [
            {
                "uuid": c.uuid,
                "name": c.column_name,
                "dataType": c.data_type,
                "position": c.position,
                "hasValidation": c.uuid in val_index,
                "hasTransformation": c.uuid in txf_index,
            }
            for c in locator.columns()
        ]
        editable = {
            SECTION_VALIDATION_RULES: editable_fields(SECTION_VALIDATION_RULES),
            SECTION_TRANSFORMATION_RULES: editable_fields(SECTION_TRANSFORMATION_RULES),
            SECTION_COLUMN_RELATIONS: editable_fields(SECTION_COLUMN_RELATIONS),
        }
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:])
        return (
            "COLUMN_CATALOG:\n"
            + json.dumps(catalog, ensure_ascii=False, indent=2)
            + "\n\nEDITABLE_FIELDS:\n"
            + json.dumps(editable, ensure_ascii=False, indent=2)
            + "\n\nCONVERSATION:\n"
            + (convo or "(none)")
            + "\n\nUSER_REQUEST:\n"
            + user_query
        )

    def _parse_descriptor(self, raw: str) -> Optional[dict[str, Any]]:
        block = _extract_json_block(raw)
        if not block:
            return None
        try:
            parsed = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict) or "action" not in parsed:
            return None
        return parsed

    @staticmethod
    def _build_field_edits(raw_edits: Any) -> list[FieldEdit]:
        edits: list[FieldEdit] = []
        if not isinstance(raw_edits, list):
            return edits
        for item in raw_edits:
            if not isinstance(item, dict):
                continue
            field_name = item.get("field")
            if not field_name:
                continue
            op = str(item.get("op") or "set").lower()
            edits.append(FieldEdit(field=str(field_name), value=item.get("value"), op=op))
        return edits

    # -- change description -------------------------------------------------
    @staticmethod
    def _describe_changes(section: str, column_name: str, result) -> list[dict[str, Any]]:
        return [
            {
                "section": section,
                "column": column_name,
                "field": field_name,
                "before": result.before.get(field_name),
                "after": result.after.get(field_name),
            }
            for field_name in result.changed_fields
        ]

    @staticmethod
    def _changes_display(changes: list[dict[str, Any]]) -> str:
        lines = []
        for c in changes:
            before = "∅" if c["before"] in (None, "") else c["before"]
            after = "∅" if c["after"] in (None, "") else c["after"]
            lines.append(f"- {c['column']}.{c['field']}: {before} → {after}")
        return "\n".join(lines)

    @staticmethod
    def _default_reply(section: str, column_name: str, result) -> str:
        fields = ", ".join(result.changed_fields)
        return f"Updated {fields} on **{column_name}**."

    # -- helpers ------------------------------------------------------------
    def _reply(self, message: str) -> dict[str, Any]:
        return {
            "reply": message,
            "confirmed": False,
            "proposed_json": "",
            "changes": [],
            "changes_display": "",
            "affected_section": None,
            "affected_uuid": None,
            "affected_columns": [],
            "validation_warnings": [],
        }

    def _safe_history(self) -> list[dict[str, str]]:
        history = self._repo.fetch_history(self.session_id)
        return [
            {"role": m["role"], "content": m["content"]}
            for m in (history or [])
            if isinstance(m, dict) and "role" in m and "content" in m
        ]

    def _store_turn(
        self, bu: str, platform: str, category: str, user_query: str, raw: str, config_version: str
    ) -> None:
        safe_user = html.escape(user_query)
        safe_raw = html.escape(raw)
        for role, text in (("user_message", safe_user), ("assistant_response", safe_raw)):
            self._repo.insert(
                ChatMessage(
                    session_id=self.session_id,
                    role=role,
                    message_text=text,
                    bu=bu,
                    platform=platform,
                    category=category,
                    config_version=config_version,
                )
            )

    # -- summary + confirm --------------------------------------------------
    def generate_summary(self, json_content: str) -> str:
        """Deterministic review summary grouped by file/sheet/column (tasks 2-8)."""
        return self._summary_builder.build_markdown(json_content)

    async def save_confirmed_config(
        self, bu: str, platform: str, category: str, json_content: str
    ) -> str:
        """Persist the confirmed JSON (versioned + latest) and refresh the summary."""
        # Validate before persisting a confirmed config.
        report = validate_contract(json_content)
        if not report.is_valid:
            errors = "; ".join(f"{i.location}: {i.message}" for i in report.errors[:5])
            raise JsonConfigEditError(f"Refusing to confirm an invalid configuration: {errors}")

        summary = self.generate_summary(json_content)
        _, latest_key = store_confirmed_json(
            bu=bu,
            platform=platform,
            category=category,
            session_id=self.session_id,
            json_content=json_content,
            s3_bucket=_S3_CONFIGS_BUCKET,
        )
        try:
            upsert_category_summary(bu, platform, category, summary)
        except Exception as exc:  # noqa: BLE001 - summary cache is best-effort
            logger.warning("[JsonConfigEditor] Failed to update summary in DynamoDB: %s", exc)
        return f"Saved to {latest_key}"

    def to_contract(self, json_content: str) -> ConfigurationContract:
        """Parse an edited JSON document into a typed contract (for reapply)."""
        return ConfigurationContract.model_validate_json_document(json_content)


__all__ = [
    "JsonConfigEditorService",
    "JsonConfigEditError",
    "get_config_json_with_source",
]
