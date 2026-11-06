# ============================================================================
# Deliverable 3 - Validation Rule Identification engine (tasks 1-25). REUSABLE.
# Deterministic (NON-LLM) analysis of the canonical input model that produces
# the Validation Rule Inventory + Validation Analysis Output. It IDENTIFIES the
# supported validations a future input file must satisfy; it never enforces
# them. Consumed via services.validation_analysis_service and the
# /validation-analysis HTTP routes.
# ============================================================================
"""
validation_analysis.analyzer
============================
The Sprint 3 validation-rule-identification engine.

:class:`ValidationAnalyzer` walks a :class:`~canonical.models.CanonicalAnalysisModel`
(the Deliverable 1 output) and, for every INPUT column, deterministically
derives the validation requirements a future input file must satisfy, maps each
onto the supported-API catalogue, flags anything unsupported, and assembles the
:class:`~validation_analysis.models.ValidationAnalysisResult`.

Workflow (Sprint 3 task 1)::

    canonical model (inputs)
        -> analyze structure        (tasks 2-5)
        -> per column:
             nullability / mandatory (tasks 6-9)
             data type               (task 10)
             formats                 (tasks 11-14)
             cardinality / LOV / uniq(tasks 14-15)
             length bounds           (task 16)
             value range             (task 12)
             patterns                (task 17)
             other supported checks  (task 18)
        -> compare vs catalogue      (task 19)
        -> mark supported / flag     (tasks 20-21)
        -> inventory + params + uuid (tasks 22-24)
        -> analysis output           (task 25)

The engine is intentionally rule-based and reproducible; all thresholds are
module-level constants so behaviour is explainable and tunable.
"""
from __future__ import annotations

import logging
import json
import re
from typing import Any, Optional

from canonical.enums import DataType, DetectedFormat, IoRole
from canonical.models import (
    CanonicalAnalysisModel,
    CanonicalColumn,
    CanonicalFile,
    CanonicalSheet,
)
from contract.enums import ContractDataType
from validation_analysis.catalog import (
    get_spec,
    map_data_type,
    map_format,
)
from validation_analysis.enums import (
    RequirementSource,
    SupportedValidation,
    SupportStatus,
)
from validation_analysis.models import (
    AnalysisSummary,
    ColumnValidationProfile,
    ExpectedColumn,
    ExpectedStructure,
    UnsupportedRequirement,
    ValidationAnalysisResult,
    ValidationRequirement,
    ValidationRuleInventory,
)

logger = logging.getLogger(__name__)

# -- Tunable, explainable thresholds ----------------------------------------
# Maximum distinct values for a column to be treated as a categorical / LOV
# candidate (dropdown validation, tasks 14).
_LOV_MAX_DISTINCT = 20
# A categorical column must also have meaningfully more rows than distinct
# values, so a tiny sample of all-distinct rows is not mistaken for a LOV.
_LOV_MIN_ROWS = 3
# Minimum rows before a fully-distinct column is proposed as UNIQUE (task 15).
_UNIQUE_MIN_ROWS = 5
# Numeric parsing: strip these before attempting float() conversion.
_NUMERIC_STRIP = re.compile(r"[,$£€%\s]")


class ValidationAnalyzer:
    """Deterministic validation-rule identification over the canonical model."""

    # -- public entry point -------------------------------------------------
    def analyze(
        self, model: CanonicalAnalysisModel
    ) -> ValidationAnalysisResult:
        """Run validation rule identification over a canonical analysis model.

        Only INPUT files are analyzed - Sprint 3 asks "what must a future INPUT
        file satisfy?". Output files describe the target and are handled by the
        transformation deliverables.

        Args:
            model: The Deliverable 1 canonical analysis model.

        Returns:
            The :class:`ValidationAnalysisResult` (expected structure, inventory,
            unsupported list, and summary).
        """
        expected_structures: list[ExpectedStructure] = []
        profiles: list[ColumnValidationProfile] = []

        input_files = model.input_files or [
            f for f in model.all_files() if f.io_role is IoRole.INPUT
        ]

        for file in input_files:
            for sheet in file.sheets:
                expected_structures.append(self._expected_structure(file, sheet))
                for column in sheet.columns:
                    profiles.append(self._analyze_column(file, sheet, column))

        inventory = ValidationRuleInventory(columns=profiles)
        unsupported = self._collect_unsupported(profiles)
        summary = self._summarize(input_files, profiles)

        return ValidationAnalysisResult(
            session_id=model.session_id,
            expected_structures=expected_structures,
            inventory=inventory,
            unsupported=unsupported,
            summary=summary,
        )

    # -- structure analysis (tasks 2-5) -------------------------------------
    def _expected_structure(
        self, file: CanonicalFile, sheet: CanonicalSheet
    ) -> ExpectedStructure:
        """Derive the expected structure for one sheet (tasks 3-5)."""
        expected_columns = [
            ExpectedColumn(
                name=col.name,
                position=col.position,
                source_column_uuid=col.uuid,
            )
            # Preserve original column order (task 5).
            for col in sorted(sheet.columns, key=lambda c: c.position)
        ]
        return ExpectedStructure(
            file_name=file.name,
            file_uuid=file.uuid,
            sheet_name=sheet.name,
            sheet_uuid=sheet.uuid,
            expected_column_count=len(sheet.columns),  # task 4
            expected_columns=expected_columns,          # tasks 3, 5
        )

    # -- per-column analysis (tasks 6-24) -----------------------------------
    def _analyze_column(
        self,
        file: CanonicalFile,
        sheet: CanonicalSheet,
        column: CanonicalColumn,
    ) -> ColumnValidationProfile:
        """Analyze a single column into a validation profile (tasks 6-24)."""
        stats = column.stats
        row_count = max(sheet.row_count, stats.non_null_count + stats.null_count)
        is_populated = stats.non_null_count > 0
        is_mandatory = is_populated and stats.null_count == 0  # tasks 6, 8
        is_nullable = stats.null_count > 0                     # task 9

        contract_type = map_data_type(column.data_type)       # task 10

        profile = ColumnValidationProfile(
            source_column_uuid=column.uuid,                   # task 24
            column_name=column.name,
            position=column.position,
            file_name=file.name,
            sheet_name=sheet.name,
            data_type=contract_type,
            is_mandatory=is_mandatory,
            is_nullable=is_nullable,
        )

        reqs: list[ValidationRequirement] = []

        # -- mandatory (tasks 6, 8) -----------------------------------------
        # Emit an EXPLICIT flag either way: "Y" when every observed value is
        # non-empty, "N" when at least one null was observed. The ground-truth
        # configs carry an explicit "N" (not a bare null) for observed-optional
        # columns, so the deterministic output mirrors that unambiguously.
        if is_populated:
            if is_mandatory:
                confidence = 1.0 if (row_count and stats.non_null_count == row_count) else 0.7
                reqs.append(
                    self._supported(
                        SupportedValidation.MANDATORY,
                        RequirementSource.NULLABILITY,
                        parameters={"isMandatory": "Y"},
                        confidence=confidence,
                        rationale=(
                            f"All {stats.non_null_count} observed values are non-empty "
                            f"(0 nulls) -> column is mandatory."
                        ),
                    )
                )
            else:
                reqs.append(
                    self._supported(
                        SupportedValidation.MANDATORY,
                        RequirementSource.NULLABILITY,
                        parameters={"isMandatory": "N"},
                        confidence=0.8,
                        rationale=(
                            f"{stats.null_count} of "
                            f"{stats.non_null_count + stats.null_count} observed values "
                            f"are empty -> column is optional (not mandatory)."
                        ),
                    )
                )

        # -- data type (task 10) --------------------------------------------
        reqs.append(self._data_type_requirement(column, contract_type))

        # -- format-driven type/format (tasks 11-14, 17) --------------------
        fmt_req = self._format_requirement(column, contract_type)
        if fmt_req is not None:
            reqs.append(fmt_req)

        # -- length bounds (task 16) ----------------------------------------
        reqs.extend(self._length_requirements(column, contract_type))

        # -- numeric value range (task 12) ----------------------------------
        reqs.extend(self._value_range_requirements(column, contract_type))

        # -- list-of-values / dropdown (task 14) ----------------------------
        lov = self._lov_requirement(column, contract_type, row_count)
        if lov is not None:
            reqs.append(lov)

        # -- structural regex (whole-column pattern) ------------------------
        # Only when the column is NOT an enumerable LOV: a controlled list is a
        # better description than a regex for low-cardinality columns.
        if lov is None:
            pattern = self._pattern_requirement(column, contract_type)
            if pattern is not None:
                reqs.append(pattern)

        # -- uniqueness / duplicate constraint (task 15) --------------------
        uniq = self._unique_requirement(column, stats, row_count)
        if uniq is not None:
            reqs.append(uniq)

        profile.requirements = reqs
        return profile

    # -- individual requirement builders ------------------------------------
    def _data_type_requirement(
        self, column: CanonicalColumn, contract_type: Optional[ContractDataType]
    ) -> ValidationRequirement:
        """Build the dataType requirement, or flag UNKNOWN as unsupported (task 21)."""
        if contract_type is None:
            return ValidationRequirement(
                kind=SupportedValidation.DATA_TYPE,
                status=SupportStatus.UNSUPPORTED,
                source=RequirementSource.DATA_TYPE,
                contract_field=get_spec(SupportedValidation.DATA_TYPE).contract_field,
                parameters={},
                confidence=0.0,
                rationale=(
                    f"Canonical data type '{column.data_type.value}' has no supported "
                    f"contract dataType."
                ),
                unsupported_reason=(
                    "Could not infer a supported dataType (Text/Number/Date/Email/"
                    "Dropdown/Regex) for this column."
                ),
            )
        return self._supported(
            SupportedValidation.DATA_TYPE,
            RequirementSource.DATA_TYPE,
            parameters={"dataType": contract_type.value},
            confidence=0.9,
            rationale=(
                f"Detected logical type '{column.data_type.value}' -> contract "
                f"dataType '{contract_type.value}'."
            ),
        )

    def _format_requirement(
        self, column: CanonicalColumn, contract_type: Optional[ContractDataType]
    ) -> Optional[ValidationRequirement]:
        """Build a format/pattern requirement from the detected format (tasks 11-14, 17)."""
        detected = column.detected_format
        if detected in (DetectedFormat.NONE, DetectedFormat.UNKNOWN):
            return None

        mapping = map_format(detected)
        if mapping is None:
            return None

        params: dict[str, Any] = {"dataType": mapping.data_type.value}
        if mapping.data_format is not None:
            params["dataFormat"] = mapping.data_format
        elif mapping.validation is SupportedValidation.NUMERIC_FORMAT:
            # No fixed mask in the catalogue: derive one from the whole-column
            # numeric precision (e.g. 'xxxxx.xx'). Only when decimals are present
            # so pure-integer columns are not over-annotated.
            numeric_mask = self._numeric_format_blob(column.stats)
            if numeric_mask is not None:
                params["dataFormat"] = numeric_mask
        source = (
            RequirementSource.PATTERN
            if mapping.validation is SupportedValidation.REGEX_PATTERN
            else RequirementSource.FORMAT
        )

        if not mapping.supported:
            return ValidationRequirement(
                kind=mapping.validation,
                status=SupportStatus.UNSUPPORTED,
                source=source,
                contract_field=get_spec(mapping.validation).contract_field,
                parameters=params,
                confidence=0.0,
                rationale=f"Detected format '{detected.value}'.",
                unsupported_reason=mapping.reason,
            )

        return self._supported(
            mapping.validation,
            source,
            parameters=params,
            confidence=0.85,
            rationale=(
                f"Detected value-format '{detected.value}' -> {mapping.validation.value}"
                + (f" (dataFormat '{mapping.data_format}')" if mapping.data_format else "")
                + "."
            ),
        )

    def _length_requirements(
        self, column: CanonicalColumn, contract_type: Optional[ContractDataType]
    ) -> list[ValidationRequirement]:
        """Build min/max length requirements for text-like columns (task 16)."""
        spec_types = get_spec(SupportedValidation.MAX_LENGTH).applies_to
        if contract_type not in spec_types:
            return []

        stats = column.stats
        out: list[ValidationRequirement] = []

        if stats.max_length is not None and stats.max_length > 0:
            out.append(
                self._supported(
                    SupportedValidation.MAX_LENGTH,
                    RequirementSource.LENGTH,
                    # contract stores lengths as STRING integers (file 5).
                    parameters={"maxLengthAllowed": str(stats.max_length)},
                    confidence=0.8,
                    rationale=f"Longest observed value is {stats.max_length} characters.",
                )
            )
        if stats.min_length is not None and stats.min_length > 0:
            out.append(
                self._supported(
                    SupportedValidation.MIN_LENGTH,
                    RequirementSource.LENGTH,
                    parameters={"minLengthAllowed": str(stats.min_length)},
                    confidence=0.6,
                    rationale=f"Shortest observed value is {stats.min_length} characters.",
                )
            )
        return out

    def _value_range_requirements(
        self, column: CanonicalColumn, contract_type: Optional[ContractDataType]
    ) -> list[ValidationRequirement]:
        """Build numeric min/max value requirements (task 12).

        Prefers the whole-column numeric bounds captured during ingestion so the
        emitted minValue/maxValue are reliable; falls back to the small sample
        only when those bounds are unavailable.
        """
        if contract_type is not ContractDataType.NUMBER:
            return []

        stats = column.stats
        if stats.min_numeric is not None and stats.max_numeric is not None:
            lo, hi = stats.min_numeric, stats.max_numeric
            confidence = 0.8
        else:
            numbers = self._numeric_samples(column.sample_values)
            if len(numbers) < 2:
                return []
            lo, hi = min(numbers), max(numbers)
            confidence = 0.5

        out = [
            self._supported(
                SupportedValidation.MIN_VALUE,
                RequirementSource.VALUE_RANGE,
                parameters={"minValue": self._num_str(lo)},
                confidence=confidence,
                rationale=f"Smallest observed numeric value is {self._num_str(lo)}.",
            ),
            self._supported(
                SupportedValidation.MAX_VALUE,
                RequirementSource.VALUE_RANGE,
                parameters={"maxValue": self._num_str(hi)},
                confidence=confidence,
                rationale=f"Largest observed numeric value is {self._num_str(hi)}.",
            ),
        ]
        return out

    def _lov_requirement(
        self,
        column: CanonicalColumn,
        contract_type: Optional[ContractDataType],
        row_count: int,
    ) -> Optional[ValidationRequirement]:
        """Propose a list-of-values / dropdown constraint for categoricals (task 14)."""
        stats = column.stats
        distinct = stats.unique_count
        if contract_type not in (ContractDataType.TEXT, ContractDataType.DROPDOWN):
            return None
        if distinct <= 0 or distinct > _LOV_MAX_DISTINCT:
            return None
        # Needs meaningfully more rows than distinct values to look categorical.
        if stats.non_null_count < _LOV_MIN_ROWS or distinct >= stats.non_null_count:
            return None

        # Prefer the COMPLETE distinct value set captured during ingestion for
        # low-cardinality columns; fall back to the 5-row sample when absent.
        source_values = stats.distinct_values or column.sample_values
        allowed = self._distinct_sample_values(source_values)
        confidence = 0.6 if distinct <= 5 else 0.4
        return self._supported(
            SupportedValidation.LIST_OF_VALUES,
            RequirementSource.CARDINALITY,
            parameters={
                "lovValidationRequired": "Y",
                "allowedValues": allowed,
            },
            confidence=confidence,
            rationale=(
                f"{distinct} distinct values across {stats.non_null_count} rows "
                f"-> categorical (list-of-values) candidate."
            ),
        )

    def _pattern_requirement(
        self, column: CanonicalColumn, contract_type: Optional[ContractDataType]
    ) -> Optional[ValidationRequirement]:
        """Emit a Regex dataType + dataFormat from the whole-column pattern.

        The pattern is inferred at ingestion from EVERY value in the column
        (:attr:`ColumnStats.inferred_pattern`). It only applies to Text columns
        with no higher-level detected format; dates/emails/uuids/numbers are
        already represented by their own rules.
        """
        if contract_type is not ContractDataType.TEXT:
            return None
        pattern = column.stats.inferred_pattern
        if not pattern:
            return None
        return self._supported(
            SupportedValidation.REGEX_PATTERN,
            RequirementSource.PATTERN,
            parameters={
                "dataType": ContractDataType.REGEX.value,
                "dataFormat": pattern,
            },
            confidence=0.7,
            rationale=(
                f"Every value in the column conforms to the structural pattern "
                f"'{pattern}' -> Regex validation."
            ),
        )

    @staticmethod
    def _numeric_format_blob(stats: Any) -> Optional[str]:
        """Build the numeric ``dataFormat`` mask blob from whole-column precision.

        Mirrors the corpus shape
        ``{\"maxDecimalAllowed\":null,\"minDecimalAllowed\":null,\"format\":\"xxxxx.xx\"}``.
        Returns ``None`` for pure-integer columns (no fractional part) so they
        are not over-annotated.
        """
        int_digits = stats.max_integer_digits
        dec_digits = stats.max_decimal_digits
        if not int_digits or not dec_digits or dec_digits <= 0:
            return None
        mask = "x" * int_digits + "." + "x" * dec_digits
        return json.dumps(
            {"maxDecimalAllowed": None, "minDecimalAllowed": None, "format": mask},
            separators=(",", ":"),
        )

    def _unique_requirement(
        self, column: CanonicalColumn, stats: Any, row_count: int
    ) -> Optional[ValidationRequirement]:
        """Propose a uniqueness constraint for fully-distinct columns (task 15).

        Emits an EXPLICIT flag: "Y" when a column is fully populated and fully
        distinct (a key candidate), "N" when duplicate values were observed.
        Mirrors the ground-truth configs, which carry an explicit
        ``uniqueValueInColumn`` rather than a bare null.
        """
        if stats.non_null_count <= 0:
            return None

        # Duplicates observed -> column is NOT unique (hard, full-column fact).
        if stats.unique_count < stats.non_null_count:
            return self._supported(
                SupportedValidation.UNIQUE,
                RequirementSource.CARDINALITY,
                parameters={"uniqueValueInColumn": "N"},
                confidence=0.8,
                rationale=(
                    f"Only {stats.unique_count} distinct of {stats.non_null_count} "
                    f"non-null values -> duplicates exist (not unique)."
                ),
            )

        # Fully distinct but too few rows to assert a uniqueness CONSTRAINT.
        if stats.non_null_count < _UNIQUE_MIN_ROWS:
            return None
        # Fully distinct AND fully populated -> likely a key column.
        if stats.null_count != 0:
            return None

        name = column.name.lower()
        key_hint = any(tok in name for tok in ("id", "uuid", "key", "code", "number", "email"))
        confidence = 0.6 if key_hint else 0.35
        return self._supported(
            SupportedValidation.UNIQUE,
            RequirementSource.CARDINALITY,
            parameters={"uniqueValueInColumn": "Y"},
            confidence=confidence,
            rationale=(
                f"All {stats.non_null_count} values are distinct and non-null"
                + (" and the name suggests a key" if key_hint else "")
                + " -> uniqueness candidate."
            ),
        )

    # -- helpers ------------------------------------------------------------
    def _supported(
        self,
        kind: SupportedValidation,
        source: RequirementSource,
        *,
        parameters: dict[str, Any],
        confidence: float,
        rationale: str,
    ) -> ValidationRequirement:
        """Construct a SUPPORTED requirement wired to its catalogue spec."""
        spec = get_spec(kind)
        return ValidationRequirement(
            kind=kind,
            status=SupportStatus.SUPPORTED,
            source=source,
            contract_field=spec.contract_field,
            parameters=parameters,
            confidence=confidence,
            rationale=rationale,
        )

    @staticmethod
    def _numeric_samples(values: list[Any]) -> list[float]:
        """Parse sample values into floats, stripping currency/percent/commas."""
        out: list[float] = []
        for v in values:
            if v is None:
                continue
            token = _NUMERIC_STRIP.sub("", str(v))
            if token in ("", "-", "+", "."):
                continue
            try:
                out.append(float(token))
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _num_str(value: float) -> str:
        """Render a numeric bound the way the contract stores it (string).

        Integers are emitted without a trailing ``.0`` to match the corpus
        (values like ``"0"``, ``"999"``).
        """
        if value == int(value):
            return str(int(value))
        return repr(value)

    @staticmethod
    def _distinct_sample_values(values: list[Any], limit: int = _LOV_MAX_DISTINCT) -> list[str]:
        """Return de-duplicated, order-preserving stringified sample values."""
        seen: dict[str, None] = {}
        for v in values:
            if v is None:
                continue
            s = str(v).strip()
            if s and s not in seen:
                seen[s] = None
            if len(seen) >= limit:
                break
        return list(seen.keys())

    # -- roll-ups (tasks 21, 25) -------------------------------------------
    def _collect_unsupported(
        self, profiles: list[ColumnValidationProfile]
    ) -> list[UnsupportedRequirement]:
        """Surface every unsupported requirement at the top level (task 21)."""
        out: list[UnsupportedRequirement] = []
        for profile in profiles:
            for req in profile.unsupported_requirements():
                out.append(
                    UnsupportedRequirement(
                        source_column_uuid=profile.source_column_uuid,
                        column_name=profile.column_name,
                        kind=req.kind,
                        reason=req.unsupported_reason or "Unsupported validation.",
                    )
                )
        return out

    def _summarize(
        self,
        input_files: list[CanonicalFile],
        profiles: list[ColumnValidationProfile],
    ) -> AnalysisSummary:
        """Compute roll-up counts for the analysis output (task 25)."""
        by_kind: dict[str, int] = {}
        supported = unsupported = 0
        for profile in profiles:
            for req in profile.requirements:
                by_kind[req.kind.value] = by_kind.get(req.kind.value, 0) + 1
                if req.status is SupportStatus.SUPPORTED:
                    supported += 1
                else:
                    unsupported += 1

        sheet_count = sum(len(f.sheets) for f in input_files)
        return AnalysisSummary(
            input_file_count=len(input_files),
            sheet_count=sheet_count,
            column_count=len(profiles),
            requirement_count=supported + unsupported,
            supported_count=supported,
            unsupported_count=unsupported,
            mandatory_column_count=sum(1 for p in profiles if p.is_mandatory),
            nullable_column_count=sum(1 for p in profiles if p.is_nullable),
            requirements_by_kind=dict(sorted(by_kind.items())),
        )


__all__ = ["ValidationAnalyzer"]
