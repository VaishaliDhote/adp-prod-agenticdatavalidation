# ============================================================================
# Deliverable 5 - Transformation Rule Identification engine (tasks 1-20).
# REUSABLE. Deterministic (NON-LLM) comparison of the canonical input vs the
# known/expected output that produces the Transformation Rule Inventory +
# Transformation Analysis Output. It IDENTIFIES the supported transformations
# needed to turn the input into the output; it never executes them. Consumed via
# services.transformation_analysis_service and the /transformation-analysis HTTP
# routes.
# ============================================================================
"""
transformation_analysis.analyzer
=================================
The Sprint 5 transformation-rule-identification engine.

:class:`TransformationAnalyzer` walks a
:class:`~canonical.models.CanonicalAnalysisModel` (the Deliverable 1 output,
which already carries deterministic source-to-target relationships) and, for
every OUTPUT column, compares it against its matched SOURCE column to derive the
transformations required to reproduce it, maps each onto the supported-API
catalogue, flags anything it cannot faithfully represent, and assembles the
:class:`~transformation_analysis.models.TransformationAnalysisResult`.

Workflow (Sprint 5 task 1)::

    canonical model (inputs + outputs + relationships)
        -> sheet mappings                 (task 3)
        -> per target column:
             classify direct/renamed/derived/transformed/multi (tasks 5-9)
             resolve source/target column links + uuids         (tasks 10-11,18-19)
             compare source vs target values/format/type/length (tasks 6-9,12)
             order the per-column transforms                    (task 13)
        -> compare vs supported catalogue  (tasks 14-15)
        -> flag unsupported                (task 16)
        -> capture metadata + params + uuid (tasks 12,17-19)
        -> transformation rule inventory    (task 20)

The engine is intentionally rule-based and reproducible; it NEVER invents a
transformation it cannot derive from the data (unsupported/underivable cases are
flagged for review, honouring the "reject unsupported" boundary shared with
Deliverable 6).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from canonical.enums import ColumnChangeType, DataType, DetectedFormat
from canonical.models import (
    CanonicalAnalysisModel,
    CanonicalColumn,
    EntityRef,
    SourceTargetRelationship,
)
from contract.enums import ContractDataType
from transformation_analysis.catalog import (
    date_mask,
    get_spec,
    is_date_format,
    is_number_format,
    map_data_type,
)
from transformation_analysis.enums import (
    SupportedTransformation,
    SupportStatus,
    TransformationCategory,
    TransformationSignal,
)
from transformation_analysis.models import (
    AddedColumn,
    ColumnRef,
    ColumnTransformationProfile,
    DatasetTransformationRequirement,
    RemovedColumn,
    SheetTransformationMapping,
    TransformationAnalysisResult,
    TransformationAnalysisSummary,
    TransformationRequirement,
    TransformationRuleInventory,
    UnsupportedTransformation,
)

logger = logging.getLogger(__name__)

# Max sample values inspected per column when sniffing case/whitespace.
_SAMPLE_LIMIT = 50


class TransformationAnalyzer:
    """Deterministic transformation-rule identification over the canonical model."""

    # -- public entry point -------------------------------------------------
    def analyze(
        self, model: CanonicalAnalysisModel
    ) -> TransformationAnalysisResult:
        """Run transformation rule identification over a canonical analysis model.

        Requires both input and output files plus the source-to-target
        relationships produced by Deliverable 1. When the model has no output
        files / no relationships (e.g. an input-only analysis), the result is an
        empty-but-valid inventory (there is nothing to transform *into*).

        Args:
            model: The Deliverable 1 canonical analysis model.

        Returns:
            The :class:`TransformationAnalysisResult` (sheet mappings, inventory,
            added/removed columns, unsupported list, and summary).
        """
        sheet_mappings = self._sheet_mappings(model)

        profiles: list[ColumnTransformationProfile] = []
        added: list[AddedColumn] = []
        removed: list[RemovedColumn] = []

        for rel in model.relationships:
            if rel.level != "column":
                continue
            change = rel.change_type

            if change is ColumnChangeType.REMOVED:
                removed.append(self._removed_column(rel))
                continue

            target_ref = rel.targets[0] if rel.targets else None
            if target_ref is None or not target_ref.column_uuid:
                continue  # malformed relationship - skip defensively

            if change is ColumnChangeType.ADDED or not rel.sources:
                profile, added_entry = self._derived_column(model, rel, target_ref)
                profiles.append(profile)
                added.append(added_entry)
                continue

            profiles.append(self._mapped_column(model, rel, target_ref))

        inventory = TransformationRuleInventory(columns=profiles)
        unsupported = self._collect_unsupported(profiles)
        summary = self._summarize(model, profiles, sheet_mappings, added, removed)

        logger.info(
            "[transformation-analysis] targets=%d requirements=%d supported=%d "
            "unsupported=%d added=%d removed=%d",
            summary.target_column_count,
            summary.requirement_count,
            summary.supported_count,
            summary.unsupported_count,
            summary.added_column_count,
            summary.removed_column_count,
        )

        return TransformationAnalysisResult(
            session_id=model.session_id,
            sheet_mappings=sheet_mappings,
            inventory=inventory,
            added_columns=added,
            removed_columns=removed,
            unsupported=unsupported,
            summary=summary,
        )

    # -- sheet mappings (task 3) -------------------------------------------
    def _sheet_mappings(
        self, model: CanonicalAnalysisModel
    ) -> list[SheetTransformationMapping]:
        """Build output-from-input sheet mappings from the relationships (task 3)."""
        mappings: list[SheetTransformationMapping] = []
        for rel in model.relationships:
            if rel.level != "sheet" or not rel.targets:
                continue
            target = rel.targets[0]
            mappings.append(
                SheetTransformationMapping(
                    target_sheet_uuid=target.sheet_uuid or "",
                    target_sheet_name=target.sheet_name or "",
                    target_file_name=target.file_name or "",
                    source_sheet_uuids=[s.sheet_uuid or "" for s in rel.sources],
                    source_sheet_names=[s.sheet_name or "" for s in rel.sources],
                    derivation_type=(
                        rel.derivation_type.value if rel.derivation_type else None
                    ),
                )
            )
        return mappings

    # -- removed columns ----------------------------------------------------
    def _removed_column(self, rel: SourceTargetRelationship) -> RemovedColumn:
        """Record an input column dropped from the output (no transform row)."""
        src = rel.sources[0]
        return RemovedColumn(
            source_column=self._ref_from_entity(src),
            reason=rel.explanation or "Input column is not present in the output.",
        )

    # -- derived / added columns (task 7) -----------------------------------
    def _derived_column(
        self,
        model: CanonicalAnalysisModel,
        rel: SourceTargetRelationship,
        target_ref: EntityRef,
    ) -> tuple[ColumnTransformationProfile, AddedColumn]:
        """Handle an output column with no matching input source (derived/added).

        The transformation KINDS that could produce such a column
        (default_value, date_stamp, conditional, field_concat, ...) are all
        supported by the API, but WHICH one and its parameters cannot be derived
        deterministically from a single output column with no source. Per the
        "never invent a rule" boundary, we classify the column as DERIVED and
        flag a single UNSUPPORTED requirement for human/LLM specification
        (Sprint 5 tasks 7 + 16), rather than fabricating a value rule.
        """
        target_col = model.find_column(target_ref.column_uuid)
        target = self._ref_from_entity(target_ref, target_col)
        target_type = self._contract_type(target_col)

        reason = (
            rel.explanation
            or f"Output column '{target.column_name}' has no matching input column."
        )
        requirement = TransformationRequirement(
            kind=SupportedTransformation.DEFAULT_VALUE,
            status=SupportStatus.UNSUPPORTED,
            signal=TransformationSignal.STRUCTURE,
            contract_field=get_spec(SupportedTransformation.DEFAULT_VALUE).contract_field,
            parameters={},
            order=0,
            confidence=0.0,
            rationale=reason,
            unsupported_reason=(
                "Derived/added output column has no source column; its generation "
                "rule (constant, generated value, concat, or conditional) cannot be "
                "inferred deterministically and must be specified for review."
            ),
        )
        profile = ColumnTransformationProfile(
            target_column=target,
            source_columns=[],
            category=TransformationCategory.DERIVED,
            source_data_type=None,
            target_data_type=target_type,
            transformation_required=True,
            requirements=[requirement],
        )
        return profile, AddedColumn(target_column=target, reason=reason)

    # -- mapped columns (direct / renamed / transformed) --------------------
    def _mapped_column(
        self,
        model: CanonicalAnalysisModel,
        rel: SourceTargetRelationship,
        target_ref: EntityRef,
    ) -> ColumnTransformationProfile:
        """Analyze a 1:1 source->target column mapping into a profile (tasks 5-13)."""
        source_ref = rel.sources[0]
        source_col = model.find_column(source_ref.column_uuid)
        target_col = model.find_column(target_ref.column_uuid)

        source = self._ref_from_entity(source_ref, source_col)
        target = self._ref_from_entity(target_ref, target_col)
        source_type = self._contract_type(source_col)
        target_type = self._contract_type(target_col)

        requirements: list[TransformationRequirement] = []

        # 1) Base mapping - every target column links to its source (task 10, DIRECT_MAPPING).
        requirements.append(
            self._supported(
                SupportedTransformation.DIRECT_MAPPING,
                TransformationSignal.RELATIONSHIP,
                parameters={"sourceColumnName": source.column_uuid},
                order=0,
                confidence=rel.confidence or 0.9,
                rationale=(
                    f"Target column '{target.column_name}' maps from source column "
                    f"'{source.column_name}'."
                ),
            )
        )

        # 2) Rename - target name differs from source name (task 6).
        names_differ = (source_col.name if source_col else source.column_name) != (
            target_col.name if target_col else target.column_name
        )
        if names_differ:
            requirements.append(
                self._supported(
                    SupportedTransformation.RENAME,
                    TransformationSignal.NAME,
                    parameters={"targetColumnName": target.column_name},
                    order=0,
                    confidence=0.9,
                    rationale=(
                        f"Target name '{target.column_name}' differs from source name "
                        f"'{source.column_name}'."
                    ),
                )
            )

        # 3) Value transforms (tasks 8-9, 12), detected in a fixed deterministic
        #    order so the emitted transform sequence is reproducible (task 13).
        value_transforms = self._detect_value_transforms(
            source_col, target_col, source_type, target_type
        )
        order = 1
        for req in value_transforms:
            req.order = order
            order += 1
            requirements.append(req)

        category = self._categorize(names_differ, value_transforms)

        return ColumnTransformationProfile(
            target_column=target,
            source_columns=[source],
            category=category,
            source_data_type=source_type,
            target_data_type=target_type,
            transformation_required=bool(value_transforms),
            requirements=requirements,
        )

    # -- value-transform detection (tasks 8-9, 12) --------------------------
    def _detect_value_transforms(
        self,
        source: Optional[CanonicalColumn],
        target: Optional[CanonicalColumn],
        source_type: Optional[ContractDataType],
        target_type: Optional[ContractDataType],
    ) -> list[TransformationRequirement]:
        """Compare source vs target columns to derive value transforms.

        Fixed detection order (deterministic): trim -> case -> date_format ->
        target_format -> length_format. Only transforms with a clear, faithful
        signal are proposed; ambiguous ones (split/concat/arithmetic/conditional/
        LOV/remove-characters) are intentionally NOT inferred from a 1:1 column
        comparison (they need multi-column or value-mapping evidence) and are
        left for explicit specification rather than invented.
        """
        if source is None or target is None:
            return []

        out: list[TransformationRequirement] = []

        # -- trim (whitespace shift) ---------------------------------------
        if self._needs_trim(source, target):
            out.append(
                self._supported(
                    SupportedTransformation.TRIM,
                    TransformationSignal.WHITESPACE,
                    parameters={"position": "both", "columns": source.uuid},
                    confidence=0.6,
                    rationale="Source values carry leading/trailing whitespace absent in the target.",
                )
            )

        # -- case conversion ------------------------------------------------
        case_value = self._needs_case_conversion(source, target)
        if case_value is not None:
            out.append(
                self._supported(
                    SupportedTransformation.CASE_CONVERSION,
                    TransformationSignal.CASE,
                    parameters={"Case": case_value},
                    confidence=0.7,
                    rationale=f"Target values are uniformly {case_value}; source is not.",
                )
            )

        # -- date reformat --------------------------------------------------
        if (
            is_date_format(source.detected_format)
            and is_date_format(target.detected_format)
            and source.detected_format != target.detected_format
        ):
            out.append(
                self._supported(
                    SupportedTransformation.DATE_FORMAT,
                    TransformationSignal.FORMAT,
                    parameters={
                        "source_date_format": date_mask(source.detected_format),
                        "target_date_format": date_mask(target.detected_format),
                    },
                    confidence=0.8,
                    rationale=(
                        f"Date format shifts from '{source.detected_format.value}' to "
                        f"'{target.detected_format.value}'."
                    ),
                )
            )

        # -- target type/number format -------------------------------------
        if self._needs_target_format(source, target, source_type, target_type):
            params: dict[str, Any] = {}
            if target_type is not None:
                params["targetType"] = target_type.value
            out.append(
                self._supported(
                    SupportedTransformation.TARGET_FORMAT,
                    TransformationSignal.DATA_TYPE,
                    parameters=params,
                    confidence=0.6,
                    rationale=(
                        f"Target type/format differs from source "
                        f"('{self._type_name(source_type)}' -> '{self._type_name(target_type)}')."
                    ),
                )
            )

        # -- fixed-length pad/truncate -------------------------------------
        length_size = self._needs_length_format(source, target)
        if length_size is not None:
            out.append(
                self._supported(
                    SupportedTransformation.LENGTH_FORMAT,
                    TransformationSignal.LENGTH,
                    parameters={"target_length_size": length_size},
                    confidence=0.5,
                    rationale=(
                        f"Target values are a uniform fixed length of {length_size} "
                        f"characters differing from the source."
                    ),
                )
            )

        return out

    # -- individual detection heuristics ------------------------------------
    @staticmethod
    def _needs_trim(source: CanonicalColumn, target: CanonicalColumn) -> bool:
        """True when source samples carry surrounding whitespace the target lacks."""
        src_ws = any(
            isinstance(v, str) and v != v.strip() and v.strip() != ""
            for v in source.sample_values[:_SAMPLE_LIMIT]
        )
        if not src_ws:
            return False
        tgt_ws = any(
            isinstance(v, str) and v != v.strip()
            for v in target.sample_values[:_SAMPLE_LIMIT]
        )
        return not tgt_ws

    @staticmethod
    def _needs_case_conversion(
        source: CanonicalColumn, target: CanonicalColumn
    ) -> Optional[str]:
        """Return 'UpperCase'/'LowerCase' when the target enforces a uniform case."""
        tgt = [v for v in target.sample_values[:_SAMPLE_LIMIT] if isinstance(v, str) and v.strip()]
        src = [v for v in source.sample_values[:_SAMPLE_LIMIT] if isinstance(v, str) and v.strip()]
        if not tgt or not src:
            return None

        def has_alpha(values: list[str]) -> bool:
            return any(any(ch.isalpha() for ch in v) for v in values)

        if not has_alpha(tgt) or not has_alpha(src):
            return None

        tgt_all_upper = all(v == v.upper() for v in tgt)
        tgt_all_lower = all(v == v.lower() for v in tgt)
        src_all_upper = all(v == v.upper() for v in src)
        src_all_lower = all(v == v.lower() for v in src)

        if tgt_all_upper and not src_all_upper:
            return "UpperCase"
        if tgt_all_lower and not src_all_lower:
            return "LowerCase"
        return None

    @staticmethod
    def _needs_target_format(
        source: CanonicalColumn,
        target: CanonicalColumn,
        source_type: Optional[ContractDataType],
        target_type: Optional[ContractDataType],
    ) -> bool:
        """True when a target type/number format re-render is required."""
        # Logical contract-type change (e.g. Text -> Number).
        if source_type is not None and target_type is not None and source_type != target_type:
            return True
        # Same type but a numeric-format shift (e.g. currency -> plain decimal).
        if (
            is_number_format(source.detected_format)
            and is_number_format(target.detected_format)
            and source.detected_format != target.detected_format
        ):
            return True
        return False

    @staticmethod
    def _needs_length_format(
        source: CanonicalColumn, target: CanonicalColumn
    ) -> Optional[str]:
        """Return the fixed target length (as a string) when padding/truncation applies."""
        t = target.stats
        s = source.stats
        # Target must be a uniform fixed width...
        if t.min_length is None or t.max_length is None:
            return None
        if t.min_length != t.max_length or t.max_length <= 0:
            return None
        # ...that the source does not already satisfy uniformly.
        if s.min_length == t.min_length and s.max_length == t.max_length:
            return None
        return str(t.max_length)

    # -- categorization (tasks 5-9) ----------------------------------------
    @staticmethod
    def _categorize(
        names_differ: bool, value_transforms: list[TransformationRequirement]
    ) -> TransformationCategory:
        """Classify the overall column relationship (tasks 5-9)."""
        n = len(value_transforms)
        if n == 0:
            return (
                TransformationCategory.RENAMED
                if names_differ
                else TransformationCategory.DIRECT
            )
        if n == 1:
            return TransformationCategory.TRANSFORMED
        return TransformationCategory.MULTI_TRANSFORM

    # -- helpers ------------------------------------------------------------
    def _supported(
        self,
        kind: SupportedTransformation,
        signal: TransformationSignal,
        *,
        parameters: dict[str, Any],
        confidence: float,
        rationale: str,
        order: int = 0,
    ) -> TransformationRequirement:
        """Construct a SUPPORTED requirement wired to its catalogue spec."""
        spec = get_spec(kind)
        return TransformationRequirement(
            kind=kind,
            status=SupportStatus.SUPPORTED,
            signal=signal,
            contract_field=spec.contract_field,
            config_op=spec.config_op,
            parameters=parameters,
            order=order,
            confidence=confidence,
            rationale=rationale,
        )

    @staticmethod
    def _ref_from_entity(
        entity: EntityRef, column: Optional[CanonicalColumn] = None
    ) -> ColumnRef:
        """Build a :class:`ColumnRef` from an :class:`EntityRef` (+ canonical column)."""
        return ColumnRef(
            column_uuid=entity.column_uuid or (column.uuid if column else ""),
            column_name=entity.column_name or (column.name if column else ""),
            position=column.position if column else 0,
            sheet_name=entity.sheet_name or "",
            file_name=entity.file_name or "",
        )

    @staticmethod
    def _contract_type(column: Optional[CanonicalColumn]) -> Optional[ContractDataType]:
        """Resolve a column's contract data type, or ``None``."""
        if column is None:
            return None
        return map_data_type(column.data_type)

    @staticmethod
    def _type_name(t: Optional[ContractDataType]) -> str:
        """Readable type name for rationales."""
        return t.value if t is not None else "unknown"

    # -- roll-ups (tasks 16, 20) -------------------------------------------
    def _collect_unsupported(
        self, profiles: list[ColumnTransformationProfile]
    ) -> list[UnsupportedTransformation]:
        """Surface every unsupported requirement at the top level (task 16)."""
        out: list[UnsupportedTransformation] = []
        for profile in profiles:
            for req in profile.unsupported_requirements():
                out.append(
                    UnsupportedTransformation(
                        target_column_uuid=profile.target_column.column_uuid,
                        target_column_name=profile.target_column.column_name,
                        kind=req.kind,
                        reason=req.unsupported_reason or "Unsupported transformation.",
                    )
                )
        return out

    def _summarize(
        self,
        model: CanonicalAnalysisModel,
        profiles: list[ColumnTransformationProfile],
        sheet_mappings: list[SheetTransformationMapping],
        added: list[AddedColumn],
        removed: list[RemovedColumn],
    ) -> TransformationAnalysisSummary:
        """Compute roll-up counts for the analysis output (task 20)."""
        by_kind: dict[str, int] = {}
        supported = unsupported = 0
        cat_counts = {c: 0 for c in TransformationCategory}

        for profile in profiles:
            cat_counts[profile.category] += 1
            for req in profile.requirements:
                by_kind[req.kind.value] = by_kind.get(req.kind.value, 0) + 1
                if req.status is SupportStatus.SUPPORTED:
                    supported += 1
                else:
                    unsupported += 1

        return TransformationAnalysisSummary(
            input_file_count=len(model.input_files),
            output_file_count=len(model.output_files),
            sheet_mapping_count=len(sheet_mappings),
            target_column_count=len(profiles),
            requirement_count=supported + unsupported,
            supported_count=supported,
            unsupported_count=unsupported,
            direct_count=cat_counts[TransformationCategory.DIRECT],
            renamed_count=cat_counts[TransformationCategory.RENAMED],
            derived_count=cat_counts[TransformationCategory.DERIVED],
            transformed_count=cat_counts[TransformationCategory.TRANSFORMED],
            multi_transform_count=cat_counts[TransformationCategory.MULTI_TRANSFORM],
            added_column_count=len(added),
            removed_column_count=len(removed),
            requirements_by_kind=dict(sorted(by_kind.items())),
        )


__all__ = ["TransformationAnalyzer"]
