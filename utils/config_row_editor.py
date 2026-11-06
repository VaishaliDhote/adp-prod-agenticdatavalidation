# ============================================================================
# Deliverable 9 - Targeted-editing CORE (JSON): deterministic single-row editor
# (tasks 19-22).
#
# Applies an approved, structured change to exactly one located contract row and
# returns the new row. Each editable field corresponds 1:1 to a compiled IR
# parameter (see validation_ir/compiler.py and transformation_ir/compiler.py),
# so mutating it is equivalent to "update the IR for the affected rule" - but it
# is done deterministically, without re-ingesting the session or re-running the
# LLM. Fidelity is guaranteed two ways:
#   * only allow-listed, safe fields may be changed (identity/audit/UUID anchors
#     and server surrogate ids are immutable);
#   * the resulting row is re-validated through the exact typed contract model
#     (ValidationRule / TransformationRule / ColumnRelation / DatasetTransformation
#     Rule), which re-emits the exact key set/order/casing and round-trips every
#     untouched blob string verbatim.
# ============================================================================
"""
utils.config_row_editor
========================
Apply structured, allow-listed field edits to a single contract row.

The editor is intentionally conservative: it supports the deterministic,
high-frequency edits users ask for in targeted editing (toggle mandatory/unique,
change a length/value bound, change a date/number format, rename a target
column, change output ordering, toggle transformation, edit a special-char/LOV
setting) and rejects anything it cannot express as a supported contract field.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from contract.models import (
    ColumnRelation,
    DatasetTransformationRule,
    TransformationRule,
    ValidationRule,
)
from utils.config_block_locator import (
    SECTION_COLUMN_RELATIONS,
    SECTION_DATASET_TRANSFORMATION_RULES,
    SECTION_TRANSFORMATION_RULES,
    SECTION_VALIDATION_RULES,
)


class RowEditError(ValueError):
    """Raised when a requested row edit is invalid or unsupported."""


# ---------------------------------------------------------------------------
# Editable-field policy
# ---------------------------------------------------------------------------
# Per section: the set of fields a targeted edit may change. Everything else
# (identity triple, audit, UUID anchors, server ``id``) is immutable. Values map
# to the semantic type used for light coercion/validation:
#   "flag"   -> 'Y' / 'N' (nullable)
#   "str"    -> free string (nullable)
#   "numstr" -> numeric stored as STRING (contract stores bounds as strings)
#   "int"    -> integer (nullable)
#   "type"   -> ContractDataType enum value
#   "blob"   -> embedded JSON-string field (object/array or JSON string)
_EDITABLE_FIELDS: dict[str, dict[str, str]] = {
    SECTION_VALIDATION_RULES: {
        "isMandatory": "flag",
        "uniqueValueInColumn": "flag",
        "columnRequiredInErrorFile": "flag",
        "minLengthAllowed": "numstr",
        "maxLengthAllowed": "numstr",
        "minValue": "numstr",
        "maxValue": "numstr",
        "dataType": "type",
        "dataFormat": "str",
        "lovValidationRequired": "flag",
        "lovCheckType": "str",
        "isSkipValidaitons": "bool",  # intentional typo key preserved
        "errorMessageInfo": "str",
        "specialCharNotAllowed": "blob",
        "specialCharToBeRemoved": "blob",
        "stringCheckRule": "blob",
        "conditionalValidationRule": "blob",
        "dataExclusionRules": "blob",
    },
    SECTION_TRANSFORMATION_RULES: {
        "targetColumnName": "str",
        "columnSequence": "int",
        "dataType": "type",
        "dataFormat": "str",
        "transformationRequired": "flag",
        "targetFileName": "str",
        "lovCheckType": "str",
        "lovValidationRequired": "flag",
        "defaultValue": "blob",
        "dataTransformationRules": "blob",
        "specialCharToBeRemoved": "blob",
    },
    SECTION_COLUMN_RELATIONS: {
        "columnName": "str",
        "aliasName": "str",
        "position": "int",
        "required": "flag",
        "columnRequiredInErrorFile": "flag",
        "dataType": "type",
        "format": "str",
        "path": "str",
        "dataExclusionRules": "blob",
    },
    SECTION_DATASET_TRANSFORMATION_RULES: {
        "fieldName": "str",
        "fieldType": "str",
        "rule": "blob",
    },
}

# Fields that must never change (defensive: also enforced by the allow-list).
_IMMUTABLE_FIELDS = frozenset(
    {
        "id", "bu", "platform", "dataCategory", "useremail", "userrole",
        "uuid", "sourceColumn", "sourceColumnName", "fieldUUID", "dataSetId",
    }
)

_FLAG_VALUES = {"Y", "N"}

# section -> typed contract model used to re-validate the edited row.
_ROW_MODEL = {
    SECTION_VALIDATION_RULES: ValidationRule,
    SECTION_TRANSFORMATION_RULES: TransformationRule,
    SECTION_COLUMN_RELATIONS: ColumnRelation,
    SECTION_DATASET_TRANSFORMATION_RULES: DatasetTransformationRule,
}


@dataclass
class FieldEdit:
    """One field-level change to a contract row.

    Attributes:
        field: The exact contract field name to change (must be editable).
        value: The new value. ``None`` clears the field (where nullable).
        op: ``"set"`` (default) or ``"clear"`` (equivalent to value=None).
    """

    field: str
    value: Any = None
    op: str = "set"


@dataclass
class RowEdit:
    """A structured, approved edit targeting one row in one section."""

    section: str
    anchor_value: str
    edits: list[FieldEdit] = field(default_factory=list)


@dataclass
class RowEditResult:
    """Outcome of applying a :class:`RowEdit` to a located row."""

    new_row: dict[str, Any]
    changed_fields: list[str]
    before: dict[str, Any]
    after: dict[str, Any]


def editable_fields(section: str) -> dict[str, str]:
    """Return the editable field -> semantic-type map for a section."""
    return dict(_EDITABLE_FIELDS.get(section, {}))


class ConfigRowEditor:
    """Apply structured field edits to a single located contract row."""

    def apply(self, section: str, row: dict[str, Any], edit: RowEdit) -> RowEditResult:
        """Apply ``edit`` to ``row`` and return the re-validated new row.

        Args:
            section: The contract section the row belongs to.
            row: The located row dict (not mutated; a copy is edited).
            edit: The structured change to apply.

        Raises:
            RowEditError: on an unknown/immutable field, a bad value, or if the
                edited row no longer validates against the typed contract model.
        """
        allowed = _EDITABLE_FIELDS.get(section)
        if allowed is None:
            raise RowEditError(f"Section {section!r} does not support field edits")
        model = _ROW_MODEL[section]

        working = dict(row)  # shallow copy; blob strings are immutable str values
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        changed: list[str] = []

        for fe in edit.edits:
            name = fe.field
            if name in _IMMUTABLE_FIELDS or name not in allowed:
                raise RowEditError(
                    f"Field {name!r} is not editable on section {section!r}"
                )
            semantic = allowed[name]
            new_value = None if fe.op == "clear" else self._coerce(name, fe.value, semantic)
            old_value = working.get(name)
            if old_value == new_value:
                continue
            before[name] = old_value
            working[name] = new_value
            after[name] = new_value
            changed.append(name)

        if not changed:
            # No-op edit: return the row unchanged (idempotent).
            return RowEditResult(new_row=dict(row), changed_fields=[], before={}, after={})

        # Re-validate through the exact typed contract model. This guarantees the
        # edited row keeps the exact key set/order/casing and that every untouched
        # blob string round-trips verbatim; it also catches invalid enum/type edits.
        try:
            typed = model.model_validate(working)
        except Exception as exc:  # noqa: BLE001 - normalize to a domain error
            raise RowEditError(f"Edited row is not contract-valid: {exc}") from exc
        new_row = typed.model_dump(by_alias=True, mode="json")

        return RowEditResult(
            new_row=new_row,
            changed_fields=changed,
            before=before,
            after=after,
        )

    # -- value coercion -----------------------------------------------------
    @staticmethod
    def _coerce(name: str, value: Any, semantic: str) -> Any:
        if value is None:
            return None
        if semantic == "flag":
            token = str(value).strip().upper()
            if token in {"YES", "TRUE", "1"}:
                token = "Y"
            elif token in {"NO", "FALSE", "0"}:
                token = "N"
            if token not in _FLAG_VALUES:
                raise RowEditError(f"Field {name!r} expects 'Y' or 'N', got {value!r}")
            return token
        if semantic == "bool":
            if isinstance(value, bool):
                return value
            token = str(value).strip().lower()
            if token in {"true", "1", "yes"}:
                return True
            if token in {"false", "0", "no"}:
                return False
            raise RowEditError(f"Field {name!r} expects a boolean, got {value!r}")
        if semantic == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise RowEditError(f"Field {name!r} expects an integer, got {value!r}")
        if semantic == "numstr":
            # The contract stores numeric bounds/lengths as STRINGS ("40").
            if isinstance(value, bool):
                raise RowEditError(f"Field {name!r} expects a number, got {value!r}")
            if isinstance(value, (int, float)):
                return str(value)
            token = str(value).strip()
            try:
                float(token)
            except ValueError:
                raise RowEditError(f"Field {name!r} expects a numeric string, got {value!r}")
            return token
        if semantic == "type":
            return str(value)  # ContractDataType validated by the model
        if semantic == "blob":
            # Accept an object/array (serialized by the model) or a JSON string.
            if isinstance(value, str):
                stripped = value.strip()
                if stripped in {"", "<null>"}:
                    return value
                try:
                    return json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    raise RowEditError(
                        f"Field {name!r} expects a JSON object/array or a JSON string"
                    )
            return value
        # "str" and anything else: store as-is (string).
        return value


__all__ = [
    "ConfigRowEditor",
    "RowEdit",
    "FieldEdit",
    "RowEditResult",
    "RowEditError",
    "editable_fields",
]
