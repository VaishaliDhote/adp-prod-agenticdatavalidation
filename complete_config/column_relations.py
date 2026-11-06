# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# The column-relations block generator (tasks 2, 8, 10, 11, 12-15).
#
# Combines the source-to-target column catalogue from the Deliverable 1
# canonical model into the exact ``column-relations`` section. This section is
# the cross-section ANCHOR: every validation-rules.sourceColumn,
# transformation-rules.sourceColumnName and dependsOn reference resolves to a
# ``column-relations[].uuid`` emitted here. The canonical column UUIDs are
# preserved VERBATIM (task 11); a deterministic UUID is synthesised only for the
# rare column that has none (task 10). Nothing about the column is invented -
# name, position, detected type/format and null-statistics all come straight
# from ingestion.
# ============================================================================
"""
complete_config.column_relations
================================
Deterministically build the ``column-relations`` block (Sprint 7 tasks 2, 10-15).

The catalogue unions every INPUT column (positions first) with every OUTPUT
column not already present, deduplicated by UUID and ordered deterministically
(input files -> sheets -> column position, then output files). Input columns are
listed first because they carry the source UUIDs that the validation /
transformation blocks reference, guaranteeing those cross-references resolve
(Sprint 7 tasks 7, 19, 20).
"""
from __future__ import annotations

import uuid as _uuid_mod
from typing import Optional

from canonical.enums import DataType, DetectedFormat, IoRole
from canonical.models import (
    CanonicalAnalysisModel,
    CanonicalColumn,
    CanonicalFile,
    SourceTargetRelationship,
)
from contract.enums import ContractDataType, YesNoFlag
from contract.models import ColumnRelation
from complete_config.models import CompleteConfigContext

# Deterministic namespace for synthesising a stable UUID when a canonical column
# is (defensively) missing one. uuid5 keeps assembly reproducible (task 22).
_UUID_NAMESPACE = _uuid_mod.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


# Canonical logical type -> exact contract dataType (file 1 KEY 16 vocabulary).
_DATA_TYPE_MAP: dict[DataType, ContractDataType] = {
    DataType.STRING: ContractDataType.TEXT,
    DataType.INTEGER: ContractDataType.NUMBER,
    DataType.DECIMAL: ContractDataType.NUMBER,
    DataType.BOOLEAN: ContractDataType.TEXT,
    DataType.DATE: ContractDataType.DATE,
    DataType.DATETIME: ContractDataType.DATE,
    DataType.UNKNOWN: ContractDataType.TEXT,
}

# Detected value-format -> contract date/number mask backing the dataType.
# Only formats with an unambiguous mask are emitted; everything else stays null
# so the compiler never invents a format the source did not exhibit.
_FORMAT_MAP: dict[DetectedFormat, str] = {
    DetectedFormat.DATE_ISO: "yyyy-MM-dd",
    DetectedFormat.DATE_US: "MM/dd/yyyy",
    DetectedFormat.DATE_EU: "dd/MM/yyyy",
    DetectedFormat.DATETIME_ISO: "yyyy-MM-dd'T'HH:mm:ss",
}


class ColumnRelationsBuilder:
    """Build the exact ``column-relations`` block from a canonical model."""

    def build(
        self,
        model: CanonicalAnalysisModel,
        context: CompleteConfigContext,
    ) -> list[ColumnRelation]:
        """Combine the canonical column catalogue into ``column-relations``.

        Args:
            model: The Deliverable 1 canonical analysis model.
            context: The identity/audit context stamped onto every row.

        Returns:
            The ordered list of :class:`ColumnRelation` rows (task 15). Order is
            deterministic: input columns first (in file/sheet/position order),
            then any output-only columns, each assigned a 1-based ``position``.
        """
        # Map every output column UUID -> the input column NAME it derives from,
        # so an output column can carry a faithful sourceKey (tasks 15, 16).
        source_name_by_target = self._source_name_by_target(model)

        rows: list[ColumnRelation] = []
        seen: set[str] = set()
        position = 0

        for file in self._ordered_files(model):
            is_input = file.io_role is IoRole.INPUT
            for sheet in file.sheets:
                for column in sheet.columns:
                    col_uuid = self._resolve_uuid(column, sheet_name=sheet.name)
                    if col_uuid in seen:
                        continue
                    seen.add(col_uuid)
                    position += 1

                    if is_input:
                        source_key: Optional[str] = column.name
                    else:
                        source_key = source_name_by_target.get(column.uuid)

                    rows.append(
                        self._build_row(
                            column=column,
                            col_uuid=col_uuid,
                            position=position,
                            source_key=source_key,
                            context=context,
                        )
                    )
        return rows

    # -- per-row construction ----------------------------------------------
    def _build_row(
        self,
        *,
        column: CanonicalColumn,
        col_uuid: str,
        position: int,
        source_key: Optional[str],
        context: CompleteConfigContext,
    ) -> ColumnRelation:
        """Construct one exact ``column-relations`` row (tasks 12-15)."""
        data_type = self._resolve_data_type(column)
        fmt = self._resolve_format(column)
        required = self._resolve_required(column)

        return ColumnRelation(
            id=None,  # server-generated PK; never invented (task 8)
            bu=context.bu,
            platform=context.platform,
            dataCategory=context.data_category,
            sourceKey=source_key,
            columnName=column.name,
            position=position,
            aliasName=column.name,  # corpus: aliasName defaults to columnName
            uuid=col_uuid,  # preserve canonical UUID (task 11)
            required=required.value,
            columnRequiredInErrorFile=None,
            useremail=context.useremail,
            userrole=context.userrole,
            path=None,
            dataExclusionRules=None,
            dataType=data_type,
            format=fmt,
        )

    # -- deterministic ordering --------------------------------------------
    @staticmethod
    def _ordered_files(model: CanonicalAnalysisModel) -> list[CanonicalFile]:
        """Inputs first (source UUIDs), then outputs (added/derived columns)."""
        return [*model.input_files, *model.output_files]

    # -- UUID preservation / synthesis (tasks 10, 11) ----------------------
    @staticmethod
    def _resolve_uuid(column: CanonicalColumn, *, sheet_name: str) -> str:
        """Preserve the canonical UUID; synthesise a stable one only if absent."""
        if column.uuid:
            return column.uuid
        seed = f"{sheet_name}::{column.name}::{column.position}"
        return _uuid_mod.uuid5(_UUID_NAMESPACE, seed).hex

    # -- type / format / required resolution -------------------------------
    @staticmethod
    def _resolve_data_type(column: CanonicalColumn) -> ContractDataType:
        """Resolve the exact contract ``dataType`` (email format wins)."""
        if column.detected_format is DetectedFormat.EMAIL:
            return ContractDataType.EMAIL
        return _DATA_TYPE_MAP.get(column.data_type, ContractDataType.TEXT)

    @staticmethod
    def _resolve_format(column: CanonicalColumn) -> Optional[str]:
        """Resolve the ``format`` mask backing a date dataType, else null."""
        return _FORMAT_MAP.get(column.detected_format)

    @staticmethod
    def _resolve_required(column: CanonicalColumn) -> YesNoFlag:
        """Derive ``required`` from observed null-statistics (deterministic).

        A column is marked required ('Y') only when it was observed with at
        least one value and NO nulls; otherwise optional ('N'). This never
        over-constrains a column the data did not prove mandatory.
        """
        stats = column.stats
        if stats.non_null_count > 0 and stats.null_count == 0:
            return YesNoFlag.YES
        return YesNoFlag.NO

    # -- output-column sourceKey resolution --------------------------------
    @staticmethod
    def _source_name_by_target(
        model: CanonicalAnalysisModel,
    ) -> dict[str, str]:
        """Map output-column UUID -> its first source-column NAME (for sourceKey)."""
        mapping: dict[str, str] = {}
        for rel in model.relationships:
            if rel.level != "column":
                continue
            source_name = ColumnRelationsBuilder._first_source_name(rel)
            if source_name is None:
                continue
            for target in rel.targets:
                if target.column_uuid and target.column_uuid not in mapping:
                    mapping[target.column_uuid] = source_name
        return mapping

    @staticmethod
    def _first_source_name(rel: SourceTargetRelationship) -> Optional[str]:
        """Return the first non-empty source column name of a relationship."""
        for src in rel.sources:
            if src.column_name:
                return src.column_name
        return None


__all__ = ["ColumnRelationsBuilder"]
