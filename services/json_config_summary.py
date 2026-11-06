# ============================================================================
# Deliverable 9 - JSON review & summary (tasks 2-8).
# Deterministically render a user-friendly summary of the exact contract JSON,
# grouped by source file -> sheet (sourceKey) -> column, with each column's
# validation summary, transformation summary, and source->target relationship.
#
# This is a DETERMINISTIC walker (no LLM): the same document always yields the
# same summary, and it never invents rules the JSON does not contain.
# ============================================================================
"""
services.json_config_summary
============================
Build a grouped, human-readable summary of a configuration contract document.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from utils.config_block_locator import (
    ConfigBlockLocator,
    ConfigBlockLocatorError,
    SECTION_CONFIGURATION_DATA,
    SECTION_TRANSFORMATION_RULES,
    SECTION_VALIDATION_RULES,
)

# Human labels for the validation fields that carry a constraint.
_VALIDATION_LABELS: list[tuple[str, str]] = [
    ("isMandatory", "Mandatory"),
    ("uniqueValueInColumn", "Unique"),
    ("minLengthAllowed", "Min length"),
    ("maxLengthAllowed", "Max length"),
    ("minValue", "Min value"),
    ("maxValue", "Max value"),
    ("dataFormat", "Format"),
    ("lovValidationRequired", "LOV enforced"),
    ("lovCheckType", "LOV list"),
    ("columnRequiredInErrorFile", "Required in error file"),
    ("errorMessageInfo", "Custom error"),
]
_VALIDATION_BLOBS: list[tuple[str, str]] = [
    ("specialCharNotAllowed", "Special-char rule"),
    ("stringCheckRule", "String check"),
    ("conditionalValidationRule", "Conditional rule"),
    ("dataExclusionRules", "Data exclusions"),
]


@dataclass
class ColumnSummary:
    uuid: str
    name: str
    position: Optional[int]
    data_type: Optional[str]
    source_key: Optional[str]
    validations: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    target_names: list[str] = field(default_factory=list)


@dataclass
class SheetSummary:
    source_key: str
    columns: list[ColumnSummary] = field(default_factory=list)


@dataclass
class FileSummary:
    file_type: str
    name: str
    sheets: list[SheetSummary] = field(default_factory=list)


class JsonConfigSummaryBuilder:
    """Deterministically summarize a contract document, grouped for review."""

    def build_markdown(self, document: "str | dict[str, Any]") -> str:
        """Return a Markdown summary grouped by file -> sheet -> column."""
        try:
            locator = ConfigBlockLocator.from_json(document)
        except ConfigBlockLocatorError as exc:
            return f"_Could not summarize configuration: {exc}_"

        data = locator.document.get("data", {})
        config_data = data.get(SECTION_CONFIGURATION_DATA, {}) or {}
        validations = self._index_validations(data.get(SECTION_VALIDATION_RULES, []) or [])
        transforms = self._index_transforms(data.get(SECTION_TRANSFORMATION_RULES, []) or [])

        # Group columns by their sourceKey (a stand-in for input file/sheet).
        source_labels = self._source_key_labels(config_data)
        groups: dict[str, list[ColumnSummary]] = {}
        for col in locator.columns():
            v = validations.get(col.uuid, [])
            t, targets = transforms.get(col.uuid, ([], []))
            summary = ColumnSummary(
                uuid=col.uuid,
                name=col.column_name,
                position=col.position,
                data_type=col.data_type,
                source_key=col.source_key,
                validations=v,
                transformations=t,
                target_names=targets,
            )
            key = col.source_key or "(derived / output columns)"
            groups.setdefault(key, []).append(summary)

        lines: list[str] = []
        bu = config_data.get("bu", "")
        platform = config_data.get("platform", "")
        category = config_data.get("dataCategory", "")
        lines.append(f"# Configuration Summary — {bu} / {platform} / {category}")
        lines.append("")
        total_cols = len(locator.columns())
        lines.append(
            f"**{total_cols}** columns, **{sum(len(v) for v in validations.values())}** "
            f"validated columns, **{len(transforms)}** transformed columns."
        )
        lines.append("")

        for source_key in sorted(groups.keys()):
            label = source_labels.get(source_key, source_key)
            lines.append(f"## 📄 {label}")
            lines.append("")
            for col in sorted(groups[source_key], key=lambda c: (c.position or 0, c.name)):
                header = f"### {col.name}"
                meta = []
                if col.data_type:
                    meta.append(f"type: {col.data_type}")
                if col.position is not None:
                    meta.append(f"position: {col.position}")
                if meta:
                    header += f"  ({', '.join(meta)})"
                lines.append(header)
                lines.append(f"- UUID: `{col.uuid}`")
                if col.target_names:
                    lines.append(f"- ➡️ Maps to: {', '.join(col.target_names)}")
                if col.validations:
                    lines.append(f"- ✅ Validations: {'; '.join(col.validations)}")
                else:
                    lines.append("- ✅ Validations: none")
                if col.transformations:
                    lines.append(f"- 🔧 Transformations: {'; '.join(col.transformations)}")
                lines.append("")

        return "\n".join(lines).strip()

    # -- indexing -----------------------------------------------------------
    def _index_validations(self, rows: list[dict[str, Any]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in rows:
            uuid = row.get("sourceColumn")
            if not uuid:
                continue
            out[uuid] = self._describe_validation(row)
        return out

    def _describe_validation(self, row: dict[str, Any]) -> list[str]:
        parts: list[str] = []
        dt = row.get("dataType")
        if dt:
            parts.append(f"Type {dt}")
        for field_name, label in _VALIDATION_LABELS:
            val = row.get(field_name)
            if val in (None, "", "N"):
                continue
            if val == "Y":
                parts.append(label)
            else:
                parts.append(f"{label}={val}")
        for field_name, label in _VALIDATION_BLOBS:
            if self._blob_present(row.get(field_name)):
                parts.append(label)
        return parts

    def _index_transforms(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, tuple[list[str], list[str]]]:
        out: dict[str, tuple[list[str], list[str]]] = {}
        for row in rows:
            uuid = row.get("sourceColumnName")
            if not uuid:
                continue
            descr, targets = out.get(uuid, ([], []))
            target = row.get("targetColumnName")
            if target:
                targets.append(str(target))
            descr.extend(self._describe_transform(row))
            out[uuid] = (descr, targets)
        return out

    def _describe_transform(self, row: dict[str, Any]) -> list[str]:
        parts: list[str] = []
        if row.get("transformationRequired") == "Y":
            parts.append("transform applied")
        if self._blob_present(row.get("defaultValue")):
            parts.append("default/derived value")
        if self._blob_present(row.get("dataTransformationRules")):
            parts.append("column operations")
        if self._blob_present(row.get("specialCharToBeRemoved")):
            parts.append("special-char removal")
        if row.get("lovValidationRequired") == "Y":
            parts.append("LOV mapping")
        dt = row.get("dataType")
        if dt:
            parts.append(f"→ type {dt}")
        return parts

    @staticmethod
    def _blob_present(value: Any) -> bool:
        if value in (None, "", "[]", "<null>"):
            return False
        if isinstance(value, str):
            stripped = value.strip()
            if stripped in ("", "[]", "{}", "<null>"):
                return False
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return bool(stripped)
            return bool(parsed)
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

    @staticmethod
    def _source_key_labels(config_data: dict[str, Any]) -> dict[str, str]:
        """Map each sourceKey to a friendly 'file (type)' label via filesInfo."""
        labels: dict[str, str] = {}
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
            key = entry.get("sourceKey")
            if not key:
                continue
            ftype = entry.get("type") or ""
            labels[key] = f"{key} ({ftype})" if ftype else str(key)
        return labels


__all__ = ["JsonConfigSummaryBuilder", "ColumnSummary", "SheetSummary", "FileSummary"]
