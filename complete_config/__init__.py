# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# Public package exports.
# ============================================================================
"""
complete_config
===============
Complete JSON configuration generation (Deliverable 7 / Sprint 7).

This package combines the outputs of Deliverables 1-6 into ONE complete,
deterministic ``ConfigurationContract`` document that exactly follows the
existing API's JSON contract. The LLM reasons over IR only; this deterministic
compiler assembles the 1000+ line JSON so it is never freely rewritten:

* :mod:`complete_config.models`             - the identity/metadata context (tasks 1, 8, 9).
* :mod:`complete_config.column_relations`   - build column-relations (tasks 2, 10-15).
* :mod:`complete_config.configuration_data` - build configuration-data (tasks 3, 9).
* :mod:`complete_config.assembler`          - combine all 5 sections (tasks 4-16, 22).
* :mod:`complete_config.validation`         - validate the complete doc (tasks 17-21).

Quick start::

    from complete_config import (
        CompleteConfigAssembler,
        CompleteConfigContext,
        validate_complete_config,
    )

    contract = CompleteConfigAssembler().assemble(
        model=canonical_model,
        context=CompleteConfigContext(bu="ANZ", platform="Payline", data_category="Hire"),
        validation_rules=validation_rows,          # Deliverable 4
        transformation_rules=transformation_rows,    # Deliverable 6
        dataset_transformation_rules=dataset_rows,   # Deliverable 6
    )
    report = validate_complete_config(contract)       # tasks 17-21
    assert report.is_valid
    document_json = contract.to_contract_json(indent=2)  # deterministic (task 22)
"""
from __future__ import annotations

from complete_config.assembler import CompleteConfigAssembler
from complete_config.column_relations import ColumnRelationsBuilder
from complete_config.configuration_data import ConfigurationDataBuilder
from complete_config.models import CompleteConfigContext
from complete_config.validation import (
    CompleteConfigIssue,
    CompleteConfigReport,
    IssueCode,
    Severity,
    validate_complete_config,
)

__all__ = [
    # context
    "CompleteConfigContext",
    # builders / assembler
    "ColumnRelationsBuilder",
    "ConfigurationDataBuilder",
    "CompleteConfigAssembler",
    # validation
    "validate_complete_config",
    "CompleteConfigReport",
    "CompleteConfigIssue",
    "IssueCode",
    "Severity",
]
