# ============================================================================
# Deliverable 4 - Validation IR & Validation JSON Generation (Sprint 4).
# Public package exports.
# ============================================================================
"""
validation_ir
=============
Validation Intermediate Representation and exact validation-JSON generation
(Deliverable 4 / Sprint 4).

This package turns Deliverable 3's "what must be validated" inventory into the
exact ``validation-rules`` JSON the existing API consumes, deterministically and
without inventing rules:

* :mod:`validation_ir.models`     - the Validation IR (tasks 1-5).
* :mod:`validation_ir.builder`    - inventory -> IR, rejecting unsupported (6-9).
* :mod:`validation_ir.compiler`   - IR -> exact 30-key validation JSON (10-15).
* :mod:`validation_ir.validation` - validate the generated block (16-19).

Quick start::

    from validation_analysis.analyzer import ValidationAnalyzer
    from validation_ir import (
        ValidationIRBuilder,
        ValidationRuleCompiler,
        validate_validation_block,
    )

    analysis = ValidationAnalyzer().analyze(canonical_model)   # Deliverable 3
    ir = ValidationIRBuilder().build(analysis)                 # tasks 6-9
    rows = ValidationRuleCompiler().compile(ir)                # tasks 10-15
    report = validate_validation_block(rows)                   # tasks 16-19
    assert report.is_valid
"""
from __future__ import annotations

from validation_ir.builder import ValidationIRBuilder
from validation_ir.compiler import ValidationRuleCompiler
from validation_ir.models import (
    ColumnReference,
    ColumnValidationIR,
    RejectedValidation,
    ValidationGenerationContext,
    ValidationIR,
    ValidationRuleIR,
)
from validation_ir.validation import (
    BlockIssue,
    IssueCode,
    Severity,
    ValidationBlockReport,
    validate_validation_block,
)

__all__ = [
    # models
    "ValidationGenerationContext",
    "ColumnReference",
    "ValidationRuleIR",
    "ColumnValidationIR",
    "RejectedValidation",
    "ValidationIR",
    # builder / compiler
    "ValidationIRBuilder",
    "ValidationRuleCompiler",
    # validation
    "validate_validation_block",
    "ValidationBlockReport",
    "BlockIssue",
    "IssueCode",
    "Severity",
]
