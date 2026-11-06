# ============================================================================
# Deliverable 3 - Validation Rule Identification (Sprint 3). Package exports.
# ============================================================================
"""
validation_analysis
====================
Deliverable 3 - Validation Rule Identification.

Analyzes the Deliverable 1 canonical input model and determines WHICH supported
API validation rules a future input file must satisfy, producing a Validation
Rule Inventory + Validation Analysis Output. It identifies requirements only -
enforcement remains the responsibility of the existing Java/API engine.

Public surface::

    from validation_analysis import (
        ValidationAnalyzer,            # the engine
        ValidationAnalysisResult,      # top-level output (task 25)
        ValidationRuleInventory,       # inventory (task 22)
        ColumnValidationProfile,       # per-column requirements (task 24)
        ValidationRequirement,         # one identified requirement
        ExpectedStructure,             # expected input structure (tasks 2-5)
        SupportedValidation,           # supported-validation catalogue tokens
        SupportStatus,                 # supported / unsupported (tasks 20-21)
        all_specs,                     # catalogue introspection (task 19)
    )
"""
from __future__ import annotations

from validation_analysis.analyzer import ValidationAnalyzer
from validation_analysis.catalog import (
    FormatMapping,
    SupportedValidationSpec,
    all_specs,
    get_spec,
    is_supported,
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

__all__ = [
    # engine
    "ValidationAnalyzer",
    # enums
    "SupportedValidation",
    "SupportStatus",
    "RequirementSource",
    # catalogue
    "SupportedValidationSpec",
    "FormatMapping",
    "all_specs",
    "get_spec",
    "is_supported",
    "map_data_type",
    "map_format",
    # models
    "ValidationAnalysisResult",
    "ValidationRuleInventory",
    "ColumnValidationProfile",
    "ValidationRequirement",
    "ExpectedStructure",
    "ExpectedColumn",
    "UnsupportedRequirement",
    "AnalysisSummary",
]
