# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 6 - Transformation IR precursor (typed rule/column shapes). REUSABLE.
# Rework: formalize into the Transformation IR (rule representation, source/target
# references, parameters, dependencies - tasks 1-6); align with exact JSON contract.
# ============================================================================
"""
transformation_state.py
=======================
Typed state definitions for all transformation phases (1–9).

Each phase follows the same production-safe shape:
  {
    "preprocessing":   PreprocessingBlock,
    "execution_order": List[str],
    "error_handling":  ErrorHandlingBlock,
    "<phase_key>":     <PhaseBody>,
    "metadata":        PhaseMetadata,
  }

Column references inside rules may be either a plain string (column name)
or a rich ColumnObject produced by make_column_object().
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union, TypedDict


# ─────────────────────────────────────────────────────────────────────────────
# Shared low-level building blocks
# ─────────────────────────────────────────────────────────────────────────────

class ColumnObject(TypedDict, total=False):
    """Rich column descriptor produced by make_column_object()."""
    name: str
    aliases: List[str]
    dtype: str  # "string" | "float" | "int" | "boolean" | "datetime" | "category" | "unknown"


# A column reference may be a plain name string or a full ColumnObject.
ColumnRef = Union[str, ColumnObject]


class FormulaObject(TypedDict, total=False):
    """Safe formula struct produced by make_formula_object()."""
    expression: str
    inputs: List[str]
    output_dtype: str


class PreprocessingBlock(TypedDict, total=False):
    normalize_column_names: bool
    case_insensitive: bool
    trim_whitespace: bool
    auto_type_conversion: bool
    handle_nulls: bool


class ErrorHandlingBlock(TypedDict, total=False):
    on_missing_column: str   # "skip" | "raise" | "fill"
    on_type_error: str       # "coerce" | "raise" | "drop"
    on_formula_error: str    # "set_null" | "raise" | "skip"


class PhaseMetadata(TypedDict, total=False):
    phase: str
    intermediate_columns: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 – Structural Transformations
# ─────────────────────────────────────────────────────────────────────────────

class TransposeDetails(TypedDict, total=False):
    enabled: bool


class TransposeConfig(TypedDict, total=False):
    applied: bool
    details: TransposeDetails


class PivotRule(TypedDict, total=False):
    index: List[str]
    columns: List[str]
    values: List[str]
    aggregation: str        # "sum" | "mean" | "count" | "max" | "min" | ...
    execution_order: int


class PivotDetails(TypedDict, total=False):
    rules: List[PivotRule]


class PivotConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: PivotDetails


class UnpivotRule(TypedDict, total=False):
    id_columns: List[str]
    value_columns: List[str]
    var_name: str
    value_name: str
    execution_order: int


class UnpivotDetails(TypedDict, total=False):
    rules: List[UnpivotRule]


class UnpivotConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: UnpivotDetails


class HierarchyDetails(TypedDict, total=False):
    type: Literal["flatten", "create_hierarchical"]


class HierarchyConfig(TypedDict, total=False):
    applied: bool
    details: HierarchyDetails


class Phase1Body(TypedDict, total=False):
    # Mutually-exclusive active operation
    execution: Literal["pivot", "unpivot", "transpose", "none"]
    transpose: TransposeConfig
    pivot: PivotConfig
    unpivot: UnpivotConfig
    hierarchy_transform: HierarchyConfig


class Phase1State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_1_structure: Phase1Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 – Row Operations
# ─────────────────────────────────────────────────────────────────────────────

class FilterRule(TypedDict, total=False):
    column: ColumnRef
    operator: str   # "==" | "!=" | ">" | ">=" | "<" | "<=" | "in" | "not_in" | "contains"
    value: Any
    execution_order: int


class FilterDetails(TypedDict, total=False):
    rules: List[FilterRule]


class FilterConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: FilterDetails


class DedupRule(TypedDict, total=False):
    columns: List[ColumnRef]
    keep: Literal["first", "last", "none"]
    execution_order: int


class DedupDetails(TypedDict, total=False):
    rules: List[DedupRule]


class DedupConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: DedupDetails


class SortRule(TypedDict, total=False):
    column: ColumnRef
    order: Literal["asc", "desc"]
    execution_order: int


class SortDetails(TypedDict, total=False):
    rules: List[SortRule]


class SortConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: SortDetails


class SubtotalRule(TypedDict, total=False):
    group_by: ColumnRef
    operation: str   # "sum" | "mean" | "count" | "max" | "min"
    column: ColumnRef
    execution_order: int


class SubtotalDetails(TypedDict, total=False):
    rules: List[SubtotalRule]


class SubtotalConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: SubtotalDetails


class Phase2Body(TypedDict, total=False):
    filter: FilterConfig
    remove_duplicates: DedupConfig
    sort: SortConfig
    subtotal_rows: SubtotalConfig


class Phase2State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_2_rows: Phase2Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 – Column Operations
# ─────────────────────────────────────────────────────────────────────────────

class SelectDetails(TypedDict, total=False):
    columns: List[ColumnRef]


class SelectConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: SelectDetails


class RenameDetails(TypedDict, total=False):
    # key = original name, value = new name
    mappings: Dict[str, str]


class RenameConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: RenameDetails


class ReorderDetails(TypedDict, total=False):
    order: List[ColumnRef]


class ReorderConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ReorderDetails


class SplitRule(TypedDict, total=False):
    source: ColumnRef
    delimiter: str
    targets: List[str]
    max_splits: Optional[int]
    execution_order: int


class SplitDetails(TypedDict, total=False):
    rules: List[SplitRule]


class SplitConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: SplitDetails


class MergeRule(TypedDict, total=False):
    sources: List[ColumnRef]
    target: str
    separator: str
    execution_order: int


class MergeDetails(TypedDict, total=False):
    rules: List[MergeRule]


class MergeConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: MergeDetails


class Phase3Body(TypedDict, total=False):
    select_columns: SelectConfig
    rename_columns: RenameConfig
    reorder_columns: ReorderConfig
    split_columns: SplitConfig
    merge_columns: MergeConfig


class Phase3State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_3_columns: Phase3Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 – Enrichment
# ─────────────────────────────────────────────────────────────────────────────

class LookupRule(TypedDict, total=False):
    """Fully-defined lookup rule (VLOOKUP / XLOOKUP / INDEX-MATCH equivalent)."""
    left_key: ColumnRef
    right_key: ColumnRef
    source: str             # "same_sheet" | "<sheet_name>" | "<file_name>"
    join_type: Literal["left", "inner", "right", "outer"]
    lookup_type: Literal["VLOOKUP", "XLOOKUP", "INDEX_MATCH"]
    columns_added: List[str]
    confidence: Optional[float]
    execution_order: int


class LookupDetails(TypedDict, total=False):
    rules: List[LookupRule]


class LookupConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: LookupDetails


class CalculatedRule(TypedDict, total=False):
    column: ColumnRef
    formula: Union[str, FormulaObject]
    execution_order: int


class CalculatedDetails(TypedDict, total=False):
    rules: List[CalculatedRule]


class CalculatedConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: CalculatedDetails


class ConditionBranch(TypedDict, total=False):
    # Exactly one of these keys is present per branch
    if_: str      # renamed from "if" to avoid keyword clash; serialised as "if"
    elif_: str    # serialised as "elif"
    else_: bool   # serialised as "else" (value = True signals default branch)
    value: Any


class ConditionalRule(TypedDict, total=False):
    column: ColumnRef
    conditions: List[ConditionBranch]
    execution_order: int


class ConditionalDetails(TypedDict, total=False):
    rules: List[ConditionalRule]


class ConditionalConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ConditionalDetails


class TextOpRule(TypedDict, total=False):
    column: ColumnRef
    operation: str   # "upper" | "lower" | "title" | "trim" | "substring" | "replace" | ...
    start: Optional[int]        # for substring
    length: Optional[int]       # for substring
    find: Optional[str]         # for replace
    replace_with: Optional[str] # for replace
    execution_order: int


class TextOpDetails(TypedDict, total=False):
    rules: List[TextOpRule]


class TextOpConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: TextOpDetails


class DateOpRule(TypedDict, total=False):
    source: ColumnRef
    extract: Literal["year", "month", "day", "hour", "minute", "second",
                      "quarter", "week", "weekday", "date", "time"]
    target: str
    format: Optional[str]
    execution_order: int


class DateOpDetails(TypedDict, total=False):
    rules: List[DateOpRule]


class DateOpConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: DateOpDetails


class MathOpRule(TypedDict, total=False):
    column: ColumnRef
    operation: Union[str, FormulaObject]   # e.g. "revenue - cost" or formula object
    execution_order: int


class MathOpDetails(TypedDict, total=False):
    rules: List[MathOpRule]


class MathOpConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: MathOpDetails


class Phase4Body(TypedDict, total=False):
    lookup: LookupConfig
    calculated_columns: CalculatedConfig
    conditional_columns: ConditionalConfig
    text_operations: TextOpConfig
    date_operations: DateOpConfig
    math_operations: MathOpConfig


class Phase4State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_4_enrichment: Phase4Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 – Aggregation
# ─────────────────────────────────────────────────────────────────────────────

class GroupByRule(TypedDict, total=False):
    group_by: List[ColumnRef]
    aggregations: Dict[str, str]   # {"column_name": "sum" | "mean" | "count" | ...}
    execution_order: int


class GroupByDetails(TypedDict, total=False):
    already_aggregated: bool       # safety flag — prevents double-aggregation
    rules: List[GroupByRule]


class GroupByConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: GroupByDetails


class ConditionalAggRule(TypedDict, total=False):
    column: ColumnRef
    condition: str    # e.g. "region == 'US'"
    operation: str    # "sum" | "count" | "mean" | ...
    execution_order: int


class ConditionalAggDetails(TypedDict, total=False):
    rules: List[ConditionalAggRule]


class ConditionalAggConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ConditionalAggDetails


class RunningTotalRule(TypedDict, total=False):
    column: ColumnRef
    order_by: ColumnRef
    partition_by: Optional[List[ColumnRef]]
    execution_order: int


class RunningTotalDetails(TypedDict, total=False):
    rules: List[RunningTotalRule]


class RunningTotalConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: RunningTotalDetails


class RankingRule(TypedDict, total=False):
    column: ColumnRef
    order: Literal["asc", "desc"]
    method: Literal["dense", "min", "max", "average", "first"]
    partition_by: Optional[List[ColumnRef]]
    execution_order: int


class RankingDetails(TypedDict, total=False):
    rules: List[RankingRule]


class RankingConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: RankingDetails


class Phase5Body(TypedDict, total=False):
    groupby: GroupByConfig
    conditional_aggregation: ConditionalAggConfig
    running_total: RunningTotalConfig
    ranking: RankingConfig


class Phase5State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_5_aggregation: Phase5Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 – Data Cleaning
# ─────────────────────────────────────────────────────────────────────────────

class TrimCleanDetails(TypedDict, total=False):
    columns: List[ColumnRef]


class TrimCleanConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: TrimCleanDetails


class FindReplaceRule(TypedDict, total=False):
    column: ColumnRef
    find: str          # serialised as "from" in JSON output
    replace: str       # serialised as "to" in JSON output
    case_sensitive: bool
    execution_order: int


class FindReplaceDetails(TypedDict, total=False):
    rules: List[FindReplaceRule]


class FindReplaceConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: FindReplaceDetails


class TypeConversionRule(TypedDict, total=False):
    column: ColumnRef
    from_type: str  # serialised as "from" in JSON output
    to_type: str    # serialised as "to" in JSON output
    coerce_strategy: str   # e.g. "pd.to_numeric(errors='coerce')"
    on_error: Literal["coerce", "raise", "drop"]
    execution_order: int


class TypeConversionDetails(TypedDict, total=False):
    rules: List[TypeConversionRule]


class TypeConversionConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: TypeConversionDetails


class MissingValueRule(TypedDict, total=False):
    column: ColumnRef
    method: Literal["fill", "ffill", "bfill", "drop", "mean", "median", "mode", "zero"]
    value: Any    # used when method == "fill"
    execution_order: int


class MissingValueDetails(TypedDict, total=False):
    rules: List[MissingValueRule]


class MissingValueConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: MissingValueDetails


class Phase6Body(TypedDict, total=False):
    trim_clean: TrimCleanConfig
    find_replace: FindReplaceConfig
    type_conversion: TypeConversionConfig
    missing_values: MissingValueConfig


class Phase6State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_6_cleaning: Phase6Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7 – Advanced Logic
# ─────────────────────────────────────────────────────────────────────────────

class ArrayFilterBlock(TypedDict, total=False):
    conditions: List[str]


class ArraySortItem(TypedDict, total=False):
    column: ColumnRef
    order: Literal["asc", "desc"]


class ArraySortBlock(TypedDict, total=False):
    sort_by: List[ArraySortItem]


class ArrayUniqueBlock(TypedDict, total=False):
    unique_on: List[ColumnRef]


class ArrayFormulasDetails(TypedDict, total=False):
    functions: List[str]         # ["FILTER", "UNIQUE", "SORT", "XLOOKUP", ...]
    filter: ArrayFilterBlock
    unique: ArrayUniqueBlock
    sort: ArraySortBlock


class ArrayFormulasConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ArrayFormulasDetails


class IndirectReference(TypedDict, total=False):
    column: ColumnRef
    type: str   # "named_range" | "cell_ref" | "dynamic" | ...
    reference: Optional[str]
    execution_order: int


class IndirectReferenceDetails(TypedDict, total=False):
    columns: List[ColumnRef]
    references: List[IndirectReference]


class IndirectReferenceConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: IndirectReferenceDetails


class Phase7Body(TypedDict, total=False):
    array_formulas: ArrayFormulasConfig
    indirect_reference: IndirectReferenceConfig


class Phase7State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_7_advanced: Phase7Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8 – Formatting & UI
# ─────────────────────────────────────────────────────────────────────────────

class FormattingColumn(TypedDict, total=False):
    column: ColumnRef
    format: str               # "currency" | "percent" | "date" | "integer" | "float" | "text"
    sample_value: Any
    excel_format_code: Optional[str]
    execution_order: int


class FormattingDetails(TypedDict, total=False):
    columns: List[FormattingColumn]


class FormattingConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: FormattingDetails


class ConditionalFormattingRule(TypedDict, total=False):
    column: ColumnRef
    condition: str
    format: str    # "highlight" | "data_bar" | "icon_set" | "color_scale"
    execution_order: int


class ConditionalFormattingDetails(TypedDict, total=False):
    rules: List[ConditionalFormattingRule]


class ConditionalFormattingConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ConditionalFormattingDetails


class DataValidationRule(TypedDict, total=False):
    column: ColumnRef
    type: Literal["list", "range", "regex", "custom"]
    values: Optional[List[Any]]     # for type == "list"
    min_value: Optional[Any]        # for type == "range"
    max_value: Optional[Any]        # for type == "range"
    pattern: Optional[str]          # for type == "regex"
    execution_order: int


class DataValidationDetails(TypedDict, total=False):
    rules: List[DataValidationRule]


class DataValidationConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: DataValidationDetails


class CommentsDetails(TypedDict, total=False):
    columns: List[ColumnRef]


class CommentsConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: CommentsDetails


class ChartsDetails(TypedDict, total=False):
    type: Optional[Literal["bar", "column", "line", "pie", "scatter", "area"]]
    columns: List[ColumnRef]
    category_column: Optional[ColumnRef]
    value_columns: List[ColumnRef]
    title: Optional[str]


class ChartsConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ChartsDetails


class SparklinesDetails(TypedDict, total=False):
    rules: List[Dict[str, Any]]


class SparklinesConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: SparklinesDetails


class Phase8Body(TypedDict, total=False):
    formatting: FormattingConfig
    conditional_formatting: ConditionalFormattingConfig
    data_validation: DataValidationConfig
    comments: CommentsConfig
    charts: ChartsConfig
    sparklines: SparklinesConfig


class Phase8State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_8_formatting: Phase8Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Phase 9 – Finalization
# ─────────────────────────────────────────────────────────────────────────────

class FormulaToValueDetails(TypedDict, total=False):
    columns: List[ColumnRef]


class FormulaToValueConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: FormulaToValueDetails


class RemoveIntermediateDetails(TypedDict, total=False):
    columns: List[ColumnRef]


class RemoveIntermediateConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: RemoveIntermediateDetails


class FinalSaveDetails(TypedDict, total=False):
    format: Literal["xlsx", "csv", "parquet", "json"]
    path: Optional[str]
    sheet_name: Optional[str]


class FinalSaveConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: FinalSaveDetails


class ResequenceRule(TypedDict, total=False):
    column: ColumnRef
    start: int
    step: int
    execution_order: int


class ResequenceDetails(TypedDict, total=False):
    rules: List[ResequenceRule]


class ResequenceConfig(TypedDict, total=False):
    applied: bool
    execution_order: int
    details: ResequenceDetails


class Phase9Body(TypedDict, total=False):
    formula_to_value: FormulaToValueConfig
    remove_intermediate_columns: RemoveIntermediateConfig
    final_save: FinalSaveConfig
    resequence: ResequenceConfig


class Phase9State(TypedDict, total=False):
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock
    phase_9_final: Phase9Body
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Top-level TransformationState – combines all phases
# ─────────────────────────────────────────────────────────────────────────────

class TransformationState(TypedDict, total=False):
    """
    Full transformation state emitted by the transformation service.

    Each phase key is optional — only populated when the corresponding
    phase has been run.  The shared blocks (preprocessing, execution_order,
    error_handling) are set once at the top level so individual phase dicts
    can be merged into this state without duplication.
    """

    # Shared production-safety blocks (same values across all phases)
    preprocessing: PreprocessingBlock
    execution_order: List[str]
    error_handling: ErrorHandlingBlock

    # Phase results
    phase_1_structure: Phase1Body
    phase_2_rows: Phase2Body
    phase_3_columns: Phase3Body
    phase_4_enrichment: Phase4Body
    phase_5_aggregation: Phase5Body
    phase_6_cleaning: Phase6Body
    phase_7_advanced: Phase7Body
    phase_8_formatting: Phase8Body
    phase_9_final: Phase9Body

    # Top-level metadata (intermediate columns collected across all phases)
    metadata: PhaseMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Helper – merge individual phase outputs into a single TransformationState
# ─────────────────────────────────────────────────────────────────────────────

def merge_phase_outputs(*phase_outputs: Dict[str, Any]) -> TransformationState:
    """
    Merge one or more single-phase output dicts into a unified
    TransformationState.

    Each phase output has the shape:
        {
          "preprocessing":   {...},
          "execution_order": [...],
          "error_handling":  {...},
          "phase_N_<name>":  {...},
          "metadata":        {...},
        }

    The shared blocks are taken from the first phase output.
    Per-phase body dicts are merged under their respective keys.
    metadata.intermediate_columns is aggregated across all phases.

    Args:
        *phase_outputs: Variable positional dicts, one per phase.

    Returns:
        A single :class:`TransformationState` dict.
    """
    if not phase_outputs:
        return {}  # type: ignore[return-value]

    state: Dict[str, Any] = {
        "preprocessing":   phase_outputs[0].get("preprocessing", {}),
        "execution_order": phase_outputs[0].get("execution_order", []),
        "error_handling":  phase_outputs[0].get("error_handling", {}),
    }

    all_intermediate: List[str] = []

    for output in phase_outputs:
        for key, value in output.items():
            if key in ("preprocessing", "execution_order", "error_handling"):
                continue
            if key == "metadata":
                all_intermediate.extend(
                    output["metadata"].get("intermediate_columns", [])
                )
            else:
                # phase body key (e.g. "phase_1_structure")
                state[key] = value

    state["metadata"] = {
        "phase": "all",
        "intermediate_columns": sorted(set(all_intermediate)),
    }

    return state  # type: ignore[return-value]
