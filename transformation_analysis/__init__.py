# ============================================================================
# Deliverable 5 - Transformation Rule Identification (Sprint 5). Package exports.
# ============================================================================
"""
transformation_analysis
========================
Deliverable 5 - Transformation Rule Identification.

Analyzes the Deliverable 1 canonical model (input + expected-output files and
their source-to-target relationships) and determines WHICH supported API
transformations turn the known input into the known output, producing a
Transformation Rule Inventory + Transformation Analysis Output. It identifies
requirements only - execution remains the responsibility of the existing
Java/API engine. Unsupported/underivable cases are flagged, never invented.

Public surface::

    from transformation_analysis import (
        TransformationAnalyzer,             # the engine (tasks 1-20)
        TransformationAnalysisResult,       # top-level output (task 20)
        TransformationRuleInventory,        # inventory (task 20)
        ColumnTransformationProfile,        # per-target-column profile (tasks 4-13)
        TransformationRequirement,          # one identified transformation
        SheetTransformationMapping,         # output-from-input sheet mapping (task 3)
        SupportedTransformation,            # supported-transformation catalogue tokens
        TransformationCategory,             # direct/renamed/derived/... (tasks 5-9)
        SupportStatus,                      # supported / unsupported (tasks 15-16)
        all_specs,                          # catalogue introspection (task 14)
    )
"""
from __future__ import annotations

from transformation_analysis.analyzer import TransformationAnalyzer
from transformation_analysis.catalog import (
    SupportedTransformationSpec,
    all_specs,
    date_mask,
    get_spec,
    is_date_format,
    is_number_format,
    is_supported,
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

__all__ = [
    # engine
    "TransformationAnalyzer",
    # enums
    "SupportedTransformation",
    "TransformationCategory",
    "SupportStatus",
    "TransformationSignal",
    # catalogue
    "SupportedTransformationSpec",
    "all_specs",
    "get_spec",
    "is_supported",
    "map_data_type",
    "date_mask",
    "is_date_format",
    "is_number_format",
    # models
    "TransformationAnalysisResult",
    "TransformationRuleInventory",
    "ColumnTransformationProfile",
    "TransformationRequirement",
    "DatasetTransformationRequirement",
    "SheetTransformationMapping",
    "ColumnRef",
    "AddedColumn",
    "RemovedColumn",
    "UnsupportedTransformation",
    "TransformationAnalysisSummary",
]
