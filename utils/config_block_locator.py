# ============================================================================
# Deliverable 9 - Targeted-editing CORE (JSON): deterministic UUID block location
# and single-row splice (tasks 13-17, 21-22).
#
# Given the exact ConfigurationContract JSON document, this module resolves the
# affected column by UUID (or by column name via the column-relations catalog),
# locates the single rule row that must change in the correct section, and
# splices exactly one row back while preserving every other block byte-for-byte.
#
# It operates on the RAW contract dict (kebab-case section keys, verbatim blob
# strings) so intentional-typo keys (isSkipValidaitons, aggrigation, delimeter),
# string-typed numerics, the "<null>" sentinel, and nested JSON-string blobs all
# round-trip unchanged. Contract validation is performed separately by the caller
# via ``contract.validation.validate_contract``.
# ============================================================================
"""
utils.config_block_locator
==========================
Locate and splice a single contract row by its stable UUID anchor.

Section anchors (the field that carries the column/row UUID):

  * ``column-relations``             -> ``uuid``            (the column anchor)
  * ``validation-rules``             -> ``sourceColumn``    (validated column uuid)
  * ``transformation-rules``         -> ``sourceColumnName``(SOURCE column uuid;
                                        disambiguate by ``targetColumnName`` /
                                        ``columnSequence`` when a source column
                                        fans out to multiple target columns)
  * ``dataset-transformation-rules`` -> ``fieldUUID``       (derived field uuid)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# Kebab-case section names, in their fixed contract order.
SECTION_COLUMN_RELATIONS = "column-relations"
SECTION_CONFIGURATION_DATA = "configuration-data"
SECTION_DATASET_TRANSFORMATION_RULES = "dataset-transformation-rules"
SECTION_TRANSFORMATION_RULES = "transformation-rules"
SECTION_VALIDATION_RULES = "validation-rules"

# section -> the row field that carries the UUID anchor.
_ANCHOR_FIELD: dict[str, str] = {
    SECTION_COLUMN_RELATIONS: "uuid",
    SECTION_VALIDATION_RULES: "sourceColumn",
    SECTION_TRANSFORMATION_RULES: "sourceColumnName",
    SECTION_DATASET_TRANSFORMATION_RULES: "fieldUUID",
}

_ARRAY_SECTIONS = (
    SECTION_COLUMN_RELATIONS,
    SECTION_DATASET_TRANSFORMATION_RULES,
    SECTION_TRANSFORMATION_RULES,
    SECTION_VALIDATION_RULES,
)


class ConfigBlockLocatorError(ValueError):
    """Raised when the document is not a parseable contract envelope."""


@dataclass(frozen=True)
class ColumnInfo:
    """A resolved column from the ``column-relations`` catalog."""

    uuid: str
    column_name: str
    position: Optional[int] = None
    data_type: Optional[str] = None
    fmt: Optional[str] = None
    required: Optional[str] = None
    source_key: Optional[str] = None


@dataclass
class LocatedRow:
    """A single located contract row (a live reference into the document)."""

    section: str
    anchor_field: str
    anchor_value: str
    index: int
    row: dict[str, Any]


def anchor_field_for(section: str) -> str:
    """Return the UUID anchor field name for a section (raises on unknown)."""
    try:
        return _ANCHOR_FIELD[section]
    except KeyError as exc:  # pragma: no cover - programmer error
        raise ConfigBlockLocatorError(f"Unknown/locatable section: {section!r}") from exc


class ConfigBlockLocator:
    """Index a contract document and locate/splice rows by UUID."""

    def __init__(self, document: dict[str, Any]) -> None:
        if not isinstance(document, dict) or "data" not in document or not isinstance(document["data"], dict):
            raise ConfigBlockLocatorError("Document is not a contract envelope with a 'data' object")
        self._document = document
        self._data: dict[str, Any] = document["data"]
        self._column_index: dict[str, ColumnInfo] = {}
        self._name_index: dict[str, list[str]] = {}
        self._reindex_columns()

    # -- construction -------------------------------------------------------
    @classmethod
    def from_json(cls, payload: "str | bytes | dict[str, Any]") -> "ConfigBlockLocator":
        """Build a locator from a JSON string/bytes or an already-parsed dict."""
        if isinstance(payload, dict):
            return cls(payload)
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise ConfigBlockLocatorError(f"Unsupported payload type: {type(payload)!r}")
        try:
            document = json.loads(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ConfigBlockLocatorError(f"Config JSON is not valid JSON: {exc}") from exc
        return cls(document)

    # -- column catalog -----------------------------------------------------
    def _reindex_columns(self) -> None:
        self._column_index.clear()
        self._name_index.clear()
        for raw in self._section_rows(SECTION_COLUMN_RELATIONS):
            uuid = raw.get("uuid")
            name = raw.get("columnName")
            if not uuid or not isinstance(uuid, str):
                continue
            info = ColumnInfo(
                uuid=uuid,
                column_name=str(name) if name is not None else "",
                position=raw.get("position"),
                data_type=raw.get("dataType"),
                fmt=raw.get("format"),
                required=raw.get("required"),
                source_key=raw.get("sourceKey"),
            )
            self._column_index[uuid] = info
            if info.column_name:
                self._name_index.setdefault(info.column_name.strip().lower(), []).append(uuid)

    def columns(self) -> list[ColumnInfo]:
        """All columns from the ``column-relations`` catalog, in stored order."""
        return list(self._column_index.values())

    def get_column(self, uuid: str) -> Optional[ColumnInfo]:
        """Return the :class:`ColumnInfo` for a column uuid, or None."""
        return self._column_index.get(uuid)

    def resolve_column(self, name_or_uuid: str) -> list[ColumnInfo]:
        """Resolve a user token (a column UUID or a column NAME) to columns.

        Returns 0 matches (unknown), 1 match (unambiguous), or N>1 matches
        (ambiguous name — the caller must ask the user to disambiguate).
        """
        if not name_or_uuid:
            return []
        token = name_or_uuid.strip()
        # Exact UUID hit.
        if token in self._column_index:
            return [self._column_index[token]]
        # Case-insensitive column-name hit.
        uuids = self._name_index.get(token.lower(), [])
        return [self._column_index[u] for u in uuids]

    # -- row location -------------------------------------------------------
    def _section_rows(self, section: str) -> list[dict[str, Any]]:
        rows = self._data.get(section)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise ConfigBlockLocatorError(f"Section {section!r} is not an array")
        return rows

    def find_column_relation(self, column_uuid: str) -> Optional[LocatedRow]:
        return self._find_single(SECTION_COLUMN_RELATIONS, column_uuid)

    def find_validation_rule(self, column_uuid: str) -> Optional[LocatedRow]:
        return self._find_single(SECTION_VALIDATION_RULES, column_uuid)

    def find_dataset_rule(self, field_uuid: str) -> Optional[LocatedRow]:
        return self._find_single(SECTION_DATASET_TRANSFORMATION_RULES, field_uuid)

    def find_transformation_rules(
        self,
        source_uuid: str,
        *,
        target_column_name: Optional[str] = None,
        column_sequence: Optional[int] = None,
    ) -> list[LocatedRow]:
        """Locate transformation rows for a source column uuid.

        A single source column can fan out to multiple target columns, so this
        returns ALL matching rows. Narrow with ``target_column_name`` and/or
        ``column_sequence`` when the caller knows the specific target.
        """
        field = anchor_field_for(SECTION_TRANSFORMATION_RULES)
        located: list[LocatedRow] = []
        for idx, row in enumerate(self._section_rows(SECTION_TRANSFORMATION_RULES)):
            if row.get(field) != source_uuid:
                continue
            if target_column_name is not None and row.get("targetColumnName") != target_column_name:
                continue
            if column_sequence is not None and row.get("columnSequence") != column_sequence:
                continue
            located.append(LocatedRow(SECTION_TRANSFORMATION_RULES, field, source_uuid, idx, row))
        return located

    def _find_single(self, section: str, anchor_value: str) -> Optional[LocatedRow]:
        field = anchor_field_for(section)
        for idx, row in enumerate(self._section_rows(section)):
            if row.get(field) == anchor_value:
                return LocatedRow(section, field, anchor_value, idx, row)
        return None

    # -- splice -------------------------------------------------------------
    def splice_row(self, section: str, anchor_value: str, new_row: dict[str, Any]) -> str:
        """Replace (or append) exactly one row identified by its UUID anchor.

        Returns ``"replaced"`` or ``"appended"``. All other rows and every other
        section are left untouched. Re-indexes the column catalog if the edited
        section was ``column-relations``.
        """
        if section not in _ARRAY_SECTIONS:
            raise ConfigBlockLocatorError(f"Section {section!r} is not a spliceable array section")
        field = anchor_field_for(section)
        if new_row.get(field) != anchor_value:
            raise ConfigBlockLocatorError(
                f"new_row[{field!r}]={new_row.get(field)!r} does not match anchor {anchor_value!r}"
            )
        rows = self._data.setdefault(section, [])
        if not isinstance(rows, list):
            raise ConfigBlockLocatorError(f"Section {section!r} is not an array")
        for idx, row in enumerate(rows):
            if row.get(field) == anchor_value:
                rows[idx] = new_row
                result = "replaced"
                break
        else:
            rows.append(new_row)
            result = "appended"
        if section == SECTION_COLUMN_RELATIONS:
            self._reindex_columns()
        return result

    def remove_row(self, section: str, anchor_value: str) -> bool:
        """Remove exactly one row by its UUID anchor. Returns True if removed."""
        if section not in _ARRAY_SECTIONS:
            raise ConfigBlockLocatorError(f"Section {section!r} is not a spliceable array section")
        field = anchor_field_for(section)
        rows = self._data.get(section)
        if not isinstance(rows, list):
            return False
        for idx, row in enumerate(rows):
            if row.get(field) == anchor_value:
                del rows[idx]
                if section == SECTION_COLUMN_RELATIONS:
                    self._reindex_columns()
                return True
        return False

    # -- output -------------------------------------------------------------
    @property
    def document(self) -> dict[str, Any]:
        """The live contract envelope dict (mutations are reflected here)."""
        return self._document

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Serialize the (possibly edited) document back to a JSON string."""
        return json.dumps(self._document, ensure_ascii=False, indent=indent)


__all__ = [
    "ConfigBlockLocator",
    "ConfigBlockLocatorError",
    "ColumnInfo",
    "LocatedRow",
    "anchor_field_for",
    "SECTION_COLUMN_RELATIONS",
    "SECTION_CONFIGURATION_DATA",
    "SECTION_DATASET_TRANSFORMATION_RULES",
    "SECTION_TRANSFORMATION_RULES",
    "SECTION_VALIDATION_RULES",
]
