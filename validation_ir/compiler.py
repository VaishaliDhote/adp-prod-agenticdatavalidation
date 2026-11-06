# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation (Sprint 4).
# The IR -> exact validation-rules JSON compiler (tasks 10-15).
#
# Reads ONLY the Validation IR and emits the exact 30-key ``validation-rules``
# objects the existing API consumes. Fidelity is guaranteed structurally: every
# row is constructed as a contract.models.ValidationRule instance, so the emitted
# dict always has the exact 30 keys in the exact (alphabetical) order, exact
# spelling (including the ``isSkipValidaitons`` typo), and exact keyword casing.
# All supported rules for one column are collapsed into a SINGLE row (the corpus
# stores ~one object per validated column). Unsupported requirements never reach
# this layer - they were rejected by the builder (task 9).
# ============================================================================
"""
validation_ir.compiler
=======================
Compile the Validation IR into exact ``validation-rules`` JSON (tasks 10-15).

* Task 10 - map each column IR onto the exact contract object shape.
* Task 11 - generate one validation-rule object per emittable column.
* Task 12 - populate UUID references (sourceColumn, dependsOn).
* Task 13 - populate column references from the canonical UUID.
* Task 14 - populate parameters (limits, formats, flags, blobs).
* Task 15 - assemble the ordered validation block (the array).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from contract.enums import ContractDataType
from contract.models import ValidationRule
from validation_analysis.enums import SupportedValidation
from validation_ir.models import (
    ColumnValidationIR,
    ValidationGenerationContext,
    ValidationIR,
    ValidationRuleIR,
)

logger = logging.getLogger(__name__)

# dataType resolution precedence (task 10). The most specific supported type
# wins so a column detected as an e-mail is emitted as Email even if a generic
# Text data-type rule is also present. Order = strongest -> weakest.
_DATA_TYPE_PRECEDENCE: tuple[tuple[SupportedValidation, ContractDataType], ...] = (
    (SupportedValidation.EMAIL_FORMAT, ContractDataType.EMAIL),
    (SupportedValidation.DATE_FORMAT, ContractDataType.DATE),
    (SupportedValidation.REGEX_PATTERN, ContractDataType.REGEX),
    (SupportedValidation.NUMERIC_FORMAT, ContractDataType.NUMBER),
)


class ValidationRuleCompiler:
    """Compile a :class:`ValidationIR` into exact ``validation-rules`` JSON."""

    # -- public entry points ------------------------------------------------
    def compile_rules(self, ir: ValidationIR) -> list[ValidationRule]:
        """Compile the IR into typed :class:`ValidationRule` models (tasks 11-15).

        One model per emittable column, in the IR's column order. Constructing
        the typed model (rather than hand-building a dict) is what guarantees the
        exact 30-key shape, ordering, and keyword casing.
        """
        rules: list[ValidationRule] = []
        for column in ir.emittable_columns():
            rules.append(self._compile_column(column, ir.context))
        return rules

    def compile(self, ir: ValidationIR) -> list[dict[str, Any]]:
        """Compile the IR into the exact ``validation-rules`` JSON array (task 15).

        Each element is a plain ``dict`` with the exact 30 alphabetical keys and
        contract-faithful values (embedded blobs serialized to JSON strings),
        ready to drop into ``data["validation-rules"]``.
        """
        return [
            rule.model_dump(by_alias=True, mode="json")
            for rule in self.compile_rules(ir)
        ]

    # -- per-column compilation --------------------------------------------
    def _compile_column(
        self, column: ColumnValidationIR, context: ValidationGenerationContext
    ) -> ValidationRule:
        """Collapse all of a column's rules into ONE ``ValidationRule`` (task 11)."""
        # Start from the identity/audit + fixed defaults (tasks 12, 13).
        fields: dict[str, Any] = {
            "bu": context.bu,
            "platform": context.platform,
            "dataCategory": context.data_category,
            "useremail": context.useremail,
            "userrole": context.userrole,
            "validationRuleType": context.validation_rule_type,
            "isSkipValidaitons": context.is_skip_validations,
            # UUID references (task 12/13).
            "sourceColumn": column.source_column.column_uuid,
            "sourceColumnName": None,   # always null - use sourceColumn
            "subDataCategory": None,    # always null - reserved
            # dataType is required; resolved below (task 10).
            "dataType": self._resolve_data_type(column),
        }

        # Aggregate cross-column dependencies (task 5 -> dependsOn, task 12).
        deps = column.dependency_uuids()
        if deps:
            fields["dependsOn"] = deps

        # Apply each supported rule's parameters onto the row (task 14).
        for rule in column.rules:
            self._apply_rule(fields, rule)

        return ValidationRule.model_validate(fields)

    # -- dataType resolution (task 10) -------------------------------------
    def _resolve_data_type(self, column: ColumnValidationIR) -> ContractDataType:
        """Resolve the single emitted ``dataType`` for a column (task 10).

        Precedence: the most specific format-driven type wins
        (Email > Date > Regex > Number); otherwise the column's baseline
        ``dataType`` from analysis; finally ``Text`` as the least-constraining
        supported default (the contract requires a non-null dataType).
        """
        kinds = column.rule_kinds()
        for kind, data_type in _DATA_TYPE_PRECEDENCE:
            if kind in kinds:
                return data_type

        # Fall back to the DATA_TYPE rule's own keyword, then the column baseline.
        data_type_rule = column.find_rule(SupportedValidation.DATA_TYPE)
        if data_type_rule is not None:
            value = data_type_rule.parameters.get("dataType") or data_type_rule.keyword
            resolved = self._coerce_data_type(value)
            if resolved is not None:
                return resolved

        if column.data_type is not None:
            return column.data_type
        return ContractDataType.TEXT

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

    # -- per-rule field population (task 14) -------------------------------
    def _apply_rule(self, fields: dict[str, Any], rule: ValidationRuleIR) -> None:
        """Populate the row fields for a single supported rule (task 14).

        Dispatch is keyed on the validation kind so each rule writes only the
        exact contract field(s) it owns, using values from the IR parameters.
        """
        params = rule.parameters
        kind = rule.kind

        if kind is SupportedValidation.MANDATORY:
            # Prefer the analysis-supplied flag so an explicit "N" (optional
            # column) is preserved; the catalogue keyword "Y" is only the default.
            fields["isMandatory"] = params.get("isMandatory") or rule.keyword or "Y"

        elif kind is SupportedValidation.UNIQUE:
            fields["uniqueValueInColumn"] = (
                params.get("uniqueValueInColumn") or rule.keyword or "Y"
            )

        elif kind is SupportedValidation.REQUIRED_IN_ERROR_FILE:
            fields["columnRequiredInErrorFile"] = (
                rule.keyword or params.get("columnRequiredInErrorFile") or "Y"
            )

        elif kind is SupportedValidation.MIN_LENGTH:
            fields["minLengthAllowed"] = self._as_str(params.get("minLengthAllowed"))

        elif kind is SupportedValidation.MAX_LENGTH:
            fields["maxLengthAllowed"] = self._as_str(params.get("maxLengthAllowed"))

        elif kind is SupportedValidation.MIN_VALUE:
            fields["minValue"] = self._as_str(params.get("minValue"))

        elif kind is SupportedValidation.MAX_VALUE:
            fields["maxValue"] = self._as_str(params.get("maxValue"))

        elif kind in (
            SupportedValidation.DATE_FORMAT,
            SupportedValidation.NUMERIC_FORMAT,
            SupportedValidation.REGEX_PATTERN,
        ):
            # dataType handled by precedence; only the mask goes to dataFormat.
            data_format = params.get("dataFormat")
            if data_format is not None:
                fields["dataFormat"] = str(data_format)

        elif kind is SupportedValidation.EMAIL_FORMAT:
            # dataType=Email is set by precedence; nothing else to populate.
            pass

        elif kind is SupportedValidation.DATA_TYPE:
            # dataType is resolved centrally in _resolve_data_type.
            pass

        elif kind is SupportedValidation.LIST_OF_VALUES:
            # The API references a NAMED LOV catalogue, not inline values, so the
            # concrete allowedValues are intentionally NOT emitted (never invent
            # a list name). Emit the enforce flag, and the list name only if the
            # analysis actually supplied one.
            fields["lovValidationRequired"] = (
                params.get("lovValidationRequired") or "Y"
            )
            lov_check_type = params.get("lovCheckType")
            if lov_check_type is not None:
                fields["lovCheckType"] = str(lov_check_type)
            # A named dropdown/LOV often maps its raw code to a canonical value,
            # so carry the LLM-detected transform flag when supplied.
            transformation_required = params.get("transformationRequired")
            if transformation_required is not None:
                fields["transformationRequired"] = str(transformation_required)

        elif kind is SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED:
            payload = params.get("specialCharNotAllowed")
            if payload is not None:
                fields["specialCharNotAllowed"] = payload

        elif kind is SupportedValidation.STRING_CHECK:
            payload = params.get("stringCheckRule")
            if payload is not None:
                fields["stringCheckRule"] = payload

        elif kind is SupportedValidation.CONDITIONAL:
            payload = params.get("conditionalValidationRule")
            if payload is not None:
                fields["conditionalValidationRule"] = payload

    @staticmethod
    def _as_str(value: Any) -> Optional[str]:
        """Render a value as the STRING the contract stores, or ``None``.

        The contract stores numeric bounds/lengths as strings (e.g. ``"40"``);
        this preserves an already-string value verbatim and stringifies numbers.
        """
        if value is None:
            return None
        return str(value)


__all__ = ["ValidationRuleCompiler"]
