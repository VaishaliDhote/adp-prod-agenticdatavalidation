# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure.
# Public package exports.
# ============================================================================
"""
contract
========
Exact JSON contract layer for the DVTS payroll data-transformation
configuration (Deliverable 2 / Sprint 2).

This package captures the existing API's configuration JSON *verbatim* so later
deliverables can compile and validate configurations deterministically:

* :mod:`contract.enums`       - closed keyword vocabularies (exact casing/typos).
* :mod:`contract.blobs`       - typed models for the embedded JSON-string fields.
* :mod:`contract.models`      - the exact section + envelope Pydantic models.
* :mod:`contract.json_schema` - JSON Schema generated from the models (task 22).
* :mod:`contract.validation`  - deterministic contract validation (task 23).

Quick start::

    from contract import ConfigurationContract, validate_contract

    model = ConfigurationContract.model_validate(raw_dict)   # parse
    document = model.to_contract_json(indent=2)              # emit exact JSON
    result = validate_contract(raw_dict)                     # validate
"""
from __future__ import annotations

from contract.enums import (
    AggregationOperation,
    ArithmeticOperator,
    CaseConversion,
    ConditionalConstraint,
    ContractDataType,
    DateOperationType,
    DatasetRuleKeyword,
    InputFileType,
    JoinRelationship,
    LogicalOperator,
    OutputFileType,
    ProcessType,
    StringCheckConstraint,
    TransformationOp,
    ValidationRuleType,
    YesNoFlag,
)
from contract.json_schema import (
    build_contract_json_schema,
    write_contract_json_schema,
)
from contract.models import (
    ColumnRelation,
    ConfigurationContract,
    ConfigurationData,
    ContractData,
    DatasetTransformationRule,
    TransformationRule,
    ValidationRule,
)
from contract.validation import (
    ContractIssue,
    ContractValidationResult,
    IssueCode,
    Severity,
    validate_contract,
)

__all__ = [
    # enums
    "AggregationOperation",
    "ArithmeticOperator",
    "CaseConversion",
    "ConditionalConstraint",
    "ContractDataType",
    "DateOperationType",
    "DatasetRuleKeyword",
    "InputFileType",
    "JoinRelationship",
    "LogicalOperator",
    "OutputFileType",
    "ProcessType",
    "StringCheckConstraint",
    "TransformationOp",
    "ValidationRuleType",
    "YesNoFlag",
    # models
    "ColumnRelation",
    "ConfigurationContract",
    "ConfigurationData",
    "ContractData",
    "DatasetTransformationRule",
    "TransformationRule",
    "ValidationRule",
    # schema
    "build_contract_json_schema",
    "write_contract_json_schema",
    # validation
    "ContractIssue",
    "ContractValidationResult",
    "IssueCode",
    "Severity",
    "validate_contract",
]
