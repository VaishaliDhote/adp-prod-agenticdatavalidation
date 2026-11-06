# ============================================================================
# Deliverable 6 - Transformation IR & Transformation JSON Generation (Sprint 6).
# Public package exports.
# ============================================================================
"""
transformation_ir
=================
Transformation Intermediate Representation and exact transformation-JSON
generation (Deliverable 6 / Sprint 6).

This package turns Deliverable 5's "how does the input become the expected
output" inventory into the exact ``transformation-rules`` (20-key) and
``dataset-transformation-rules`` (11-key) JSON the existing API consumes,
deterministically and without inventing transformations:

* :mod:`transformation_ir.models`     - the Transformation IR (tasks 1-6).
* :mod:`transformation_ir.builder`    - inventory -> IR, rejecting unsupported (7-10).
* :mod:`transformation_ir.compiler`   - IR -> exact transformation JSON (11-19).
* :mod:`transformation_ir.validation` - validate the generated block (20-22).

Quick start::

    from transformation_analysis.analyzer import TransformationAnalyzer
    from transformation_ir import (
        TransformationIRBuilder,
        TransformationRuleCompiler,
        validate_transformation_block,
    )

    analysis = TransformationAnalyzer().analyze(canonical_model)   # Deliverable 5
    ir = TransformationIRBuilder().build(analysis)                 # tasks 7-10
    rows = TransformationRuleCompiler().compile(ir)                # tasks 11-19
    dataset_rows = TransformationRuleCompiler().compile_datasets(ir)
    report = validate_transformation_block(rows, dataset_rows)     # tasks 20-22
    assert report.is_valid
"""
from __future__ import annotations

from transformation_ir.builder import TransformationIRBuilder
from transformation_ir.compiler import TransformationRuleCompiler
from transformation_ir.models import (
    NULL_TARGET_FILE,
    ColumnTransformationIR,
    DatasetTransformationIR,
    RejectedTransformation,
    SourceRef,
    TargetRef,
    TransformationGenerationContext,
    TransformationIR,
    TransformationRuleIR,
)
from transformation_ir.validation import (
    BlockIssue,
    IssueCode,
    Severity,
    TransformationBlockReport,
    validate_transformation_block,
)

__all__ = [
    # models
    "TransformationGenerationContext",
    "SourceRef",
    "TargetRef",
    "TransformationRuleIR",
    "ColumnTransformationIR",
    "DatasetTransformationIR",
    "RejectedTransformation",
    "TransformationIR",
    "NULL_TARGET_FILE",
    # builder / compiler
    "TransformationIRBuilder",
    "TransformationRuleCompiler",
    # validation
    "validate_transformation_block",
    "TransformationBlockReport",
    "BlockIssue",
    "IssueCode",
    "Severity",
]
