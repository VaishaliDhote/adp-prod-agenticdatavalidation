# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation (Sprint 6).
# The IR -> exact transformation JSON compiler (tasks 11-19).
#
# Reads ONLY the Transformation IR and emits the exact 20-key
# ``transformation-rules`` objects and 11-key ``dataset-transformation-rules``
# objects the existing API consumes. Fidelity is guaranteed structurally: every
# row is constructed as a contract.models.TransformationRule /
# DatasetTransformationRule instance, so the emitted dict always has the exact
# keys in the exact declared order, exact spelling (including the ``aggrigation``
# typo and the ``<null>`` sentinel), and exact operation keyword casing (e.g.
# ``Case_Conversion``). All ops for one target column collapse into a SINGLE row
# (the corpus stores one object per target column). Unsupported requirements
# never reach this layer - they were rejected by the builder (task 10).
# ============================================================================
"""
transformation_ir.compiler
===========================
Compile the Transformation IR into exact transformation JSON (tasks 11-19).

* Task 11 - map each column/dataset IR onto the exact contract object shape.
* Task 12 - generate one transformation-rule object per emittable target column.
* Task 13 - generate dataset-transformation-rule objects.
* Task 14 - populate UUID references (sourceColumnName, dependsOn, dataSetId).
* Task 15 - populate source columns (mapping + multi-column op payloads).
* Task 16 - populate target columns (targetColumnName, columnSequence, file).
* Task 17 - populate transformation parameters (blob payloads).
* Task 18 - preserve transformation ordering within dataTransformationRules.
* Task 19 - assemble the ordered transformation block (the arrays).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from contract.blobs import (
    DataTransformationConfigEntry,
    DatasetRuleBlob,
    DefaultValueBlob,
    SpecialCharRemovalEntry,
)
from contract.enums import ContractDataType
from contract.models import DatasetTransformationRule, TransformationRule
from transformation_analysis.enums import SupportedTransformation
from transformation_ir.models import (
    ColumnTransformationIR,
    DatasetTransformationIR,
    TransformationGenerationContext,
    TransformationIR,
    TransformationRuleIR,
)

logger = logging.getLogger(__name__)

# Transformation kinds whose operation lives inside a
# ``dataTransformationRules[].configurations`` object (order-sensitive, task 18).
_CONFIG_OP_KINDS: frozenset[SupportedTransformation] = frozenset(
    {
        SupportedTransformation.DATE_FORMAT,
        SupportedTransformation.TARGET_FORMAT,
        SupportedTransformation.ARITHMETIC,
        SupportedTransformation.LENGTH_FORMAT,
        SupportedTransformation.TRIM,
        SupportedTransformation.CASE_CONVERSION,
        SupportedTransformation.SPLIT,
    }
)


class TransformationRuleCompiler:
    """Compile a :class:`TransformationIR` into exact transformation JSON."""

    # -- public entry points ------------------------------------------------
    def compile_rules(self, ir: TransformationIR) -> list[TransformationRule]:
        """Compile the IR into typed :class:`TransformationRule` models (tasks 12-18).

        One model per emittable target column, in IR order. Constructing the
        typed model (rather than hand-building a dict) is what guarantees the
        exact 20-key shape, ordering, and keyword casing.
        """
        return [
            self._compile_column(column, ir.context)
            for column in ir.emittable_columns()
        ]

    def compile(self, ir: TransformationIR) -> list[dict[str, Any]]:
        """Compile the IR into the exact ``transformation-rules`` JSON array (task 19)."""
        return [
            rule.model_dump(by_alias=True, mode="json")
            for rule in self.compile_rules(ir)
        ]

    def compile_dataset_rules(
        self, ir: TransformationIR
    ) -> list[DatasetTransformationRule]:
        """Compile the dataset-level derived fields into typed models (task 13)."""
        return [
            self._compile_dataset_rule(d, ir.context) for d in ir.dataset_rules
        ]

    def compile_datasets(self, ir: TransformationIR) -> list[dict[str, Any]]:
        """Compile the dataset fields into the exact JSON array (tasks 13, 19)."""
        return [
            rule.model_dump(by_alias=True, mode="json")
            for rule in self.compile_dataset_rules(ir)
        ]

    # -- per-column compilation --------------------------------------------
    def _compile_column(
        self, column: ColumnTransformationIR, context: TransformationGenerationContext
    ) -> TransformationRule:
        """Collapse all of a column's ops into ONE ``TransformationRule`` (task 12)."""
        primary = column.primary_source

        fields: dict[str, Any] = {
            # Identity / audit (task 14).
            "bu": context.bu,
            "platform": context.platform,
            "dataCategory": context.data_category,
            "subDataCategory": None,           # reserved - always null
            "useremail": context.useremail,
            "userrole": context.userrole,
            # Mapping references (tasks 14, 15, 16).
            "sourceColumnName": primary.column_uuid if primary else "",
            "targetColumnName": column.target.column_name,
            "targetFileName": column.target_file_name,
            "columnSequence": column.target.position,
            # Type / format (task 17).
            "dataType": self._resolve_data_type(column),
            "dataFormat": column.data_format,
            # Transform on/off flag (Y/N) driven by value transforms.
            "transformationRequired": "Y" if column.transformation_required else None,
        }

        # Aggregate the ordered dataTransformationRules configurations (task 18).
        config_entry = self._build_config_entry(column)
        if config_entry is not None:
            fields["dataTransformationRules"] = [config_entry]

        # defaultValue families (constant / date / split / concat / conditional).
        default_value = self._build_default_value(column)
        if default_value is not None:
            fields["defaultValue"] = default_value

        # specialCharToBeRemoved.
        special_char = self._build_special_char(column)
        if special_char is not None:
            fields["specialCharToBeRemoved"] = special_char

        # LOV mapping.
        self._apply_lov(fields, column)

        # Cross-column dependencies (task 14 -> dependsOn).
        if column.depends_on:
            fields["dependsOn"] = list(column.depends_on)

        return TransformationRule.model_validate(fields)

    # -- dataType resolution -----------------------------------------------
    @staticmethod
    def _resolve_data_type(column: ColumnTransformationIR) -> Optional[ContractDataType]:
        """Resolve the emitted ``dataType`` (null => inherit from source).

        A TARGET_FORMAT op may pin a ``targetType``; otherwise the column's
        analysed target contract type is used. ``None`` is contract-valid and
        means "inherit from the source column".
        """
        target_format = column.find_rule(SupportedTransformation.TARGET_FORMAT)
        if target_format is not None:
            raw = target_format.parameters.get("targetType")
            coerced = TransformationRuleCompiler._coerce_data_type(raw)
            if coerced is not None:
                return coerced
        return column.data_type

    @staticmethod
    def _coerce_data_type(value: Any) -> Optional[ContractDataType]:
        """Coerce a raw value into a :class:`ContractDataType`, or ``None``."""
        if value is None:
            return None
        if isinstance(value, ContractDataType):
            return value
        try:
            return ContractDataType(str(value))
        except ValueError:
            return None

    # -- dataTransformationRules (config ops, task 18) ---------------------
    def _build_config_entry(
        self, column: ColumnTransformationIR
    ) -> Optional[DataTransformationConfigEntry]:
        """Build ONE ``dataTransformationRules`` entry with all config ops, in order.

        The corpus groups a column's operations under a single
        ``{dataSetId, configurations}`` entry; ``configurations`` holds one key
        per operation. Ops are applied in ``order`` so the emitted sequence is
        deterministic (task 18).
        """
        ops = [r for r in column.rules if r.kind in _CONFIG_OP_KINDS]
        if not ops:
            return None
        ops.sort(key=lambda r: r.order)

        configurations: dict[str, Any] = {}
        primary_uuid = column.primary_source.column_uuid if column.primary_source else None

        for op in ops:
            key, payload = self._config_payload(op, primary_uuid)
            if key is not None:
                configurations[key] = payload

        if not configurations:
            return None
        return DataTransformationConfigEntry(
            dataSetId=primary_uuid, configurations=configurations
        )

    def _config_payload(
        self, op: TransformationRuleIR, source_uuid: Optional[str]
    ) -> tuple[Optional[str], Any]:
        """Return the ``(configurations`` key, payload) for one config op (task 17)."""
        p = op.parameters
        kind = op.kind

        if kind is SupportedTransformation.DATE_FORMAT:
            return "date_format", {
                "source_date_format": p.get("source_date_format"),
                "target_date_format": p.get("target_date_format"),
            }

        if kind is SupportedTransformation.TARGET_FORMAT:
            return "target_format", {
                "targetType": p.get("targetType"),
                "targetFormat": p.get("targetFormat"),
                "targetDecimalAllowed": p.get("targetDecimalAllowed"),
            }

        if kind is SupportedTransformation.ARITHMETIC:
            return "arithmetic_operations", {
                "columns": p.get("columns", source_uuid),
                "operations": p.get("operations", []),
            }

        if kind is SupportedTransformation.LENGTH_FORMAT:
            payload: dict[str, Any] = {"target_length_size": p.get("target_length_size")}
            if p.get("target_filler") is not None:
                payload["target_filler"] = p.get("target_filler")
            return "length_format", payload

        if kind is SupportedTransformation.TRIM:
            return "trim", {
                "position": p.get("position", "both"),
                "columns": p.get("columns", source_uuid),
            }

        if kind is SupportedTransformation.CASE_CONVERSION:
            return "Case_Conversion", {"Case": p.get("Case")}

        if kind is SupportedTransformation.SPLIT:
            return "split_operations", {
                "operations": p.get("operations", []),
                "seperator": p.get("seperator"),
            }

        return None, None

    # -- defaultValue families ---------------------------------------------
    def _build_default_value(
        self, column: ColumnTransformationIR
    ) -> Optional[DefaultValueBlob]:
        """Build the ``defaultValue`` blob from any value-generation op (task 17)."""
        blob_fields: dict[str, Any] = {}

        for op in column.rules:
            p = op.parameters
            kind = op.kind

            if kind is SupportedTransformation.DEFAULT_VALUE:
                blob_fields["defaultValue"] = p.get("defaultValue", "")

            elif kind is SupportedTransformation.DATE_STAMP:
                blob_fields["date_operations"] = {
                    "type": p.get("type", "current_date"),
                    "format": p.get("format"),
                }

            elif kind is SupportedTransformation.FIELD_CONCAT:
                blob_fields["field_concat"] = {"operations": p.get("operations", [])}

            elif kind is SupportedTransformation.SPLIT and op.contract_field == "defaultValue":
                # SPLIT normally lives in dataTransformationRules; support the
                # defaultValue.split_operations family too when explicitly set.
                blob_fields["split_operations"] = {
                    "seperator": p.get("seperator"),
                    "operations": p.get("operations", []),
                }

            elif kind is SupportedTransformation.CONDITIONAL:
                if "defaultValue" not in blob_fields:
                    blob_fields["defaultValue"] = p.get("defaultValue")
                blob_fields["conditional"] = p.get("conditional", [])
                if p.get("else") is not None:
                    blob_fields["else"] = p.get("else")

        if not blob_fields:
            return None
        return DefaultValueBlob.model_validate(blob_fields)

    # -- specialCharToBeRemoved --------------------------------------------
    def _build_special_char(
        self, column: ColumnTransformationIR
    ) -> Optional[list[SpecialCharRemovalEntry]]:
        """Build the ``specialCharToBeRemoved`` array from a remove op (task 17)."""
        op = column.find_rule(SupportedTransformation.REMOVE_CHARACTERS)
        if op is None:
            return None
        chars = op.parameters.get("remove_characters")
        if chars is None:
            return None
        source_uuid = column.primary_source.column_uuid if column.primary_source else None
        return [
            SpecialCharRemovalEntry.model_validate(
                {
                    "dataSetId": source_uuid,
                    "configurations": {"remove_characters": chars},
                }
            )
        ]

    # -- lovCheckType -------------------------------------------------------
    @staticmethod
    def _apply_lov(fields: dict[str, Any], column: ColumnTransformationIR) -> None:
        """Populate LOV fields from a LOV mapping op (never invent a list name)."""
        op = column.find_rule(SupportedTransformation.LOV_MAPPING)
        if op is None:
            return
        lov_check_type = op.parameters.get("lovCheckType")
        if lov_check_type is not None:
            fields["lovCheckType"] = str(lov_check_type)
        fields["lovValidationRequired"] = op.parameters.get("lovValidationRequired") or "Y"

    # -- dataset-transformation-rules (task 13) ----------------------------
    def _compile_dataset_rule(
        self, dataset: DatasetTransformationIR, context: TransformationGenerationContext
    ) -> DatasetTransformationRule:
        """Compile one dataset-level derived field into a typed model (task 13)."""
        rule_blob = self._build_dataset_rule_blob(dataset)
        fields: dict[str, Any] = {
            "bu": context.bu,
            "platform": context.platform,
            "dataCategory": context.data_category,
            "dataSetId": dataset.dataset_uuid,
            "fieldName": dataset.field_name,
            "fieldType": dataset.field_type,
            "fieldUUID": dataset.field_uuid,
            "rule": rule_blob,
            "useremail": context.useremail,
            "userrole": context.userrole,
        }
        return DatasetTransformationRule.model_validate(fields)

    @staticmethod
    def _build_dataset_rule_blob(dataset: DatasetTransformationIR) -> DatasetRuleBlob:
        """Build the ``rule`` blob (aggrigation | date_operations) (task 13)."""
        p = dataset.parameters
        blob: dict[str, Any] = {}
        if dataset.kind is SupportedTransformation.DATASET_AGGREGATION:
            blob["aggrigation"] = {
                "field": p.get("field"),
                "operation": p.get("operation") or dataset.keyword or "cnt",
            }
        elif dataset.kind is SupportedTransformation.DATASET_DATE_STAMP:
            blob["date_operations"] = {
                "type": p.get("type", "current_date"),
                "format": p.get("format"),
            }
        return DatasetRuleBlob.model_validate(blob)


__all__ = ["TransformationRuleCompiler"]
