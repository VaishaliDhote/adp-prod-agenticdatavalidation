# ============================================================================
# Deliverable 1 - Canonical Data Model. Public package exports.
# ============================================================================
"""
canonical
=========
Canonical input/output analysis data model for the DVTS pipeline (Sprint 1).

Import the public model and vocabulary types from this package root::

    from canonical import CanonicalAnalysisModel, CanonicalFile, FileFormat
"""
from __future__ import annotations

from canonical.enums import (
    ColumnChangeType,
    DataType,
    DerivationType,
    DetectedFormat,
    FileFormat,
    IoRole,
    RelationshipType,
)
from canonical.models import (
    CanonicalAnalysisModel,
    CanonicalColumn,
    CanonicalFile,
    CanonicalSheet,
    ColumnStats,
    EntityRef,
    SourceTargetRelationship,
)

__all__ = [
    # enums
    "ColumnChangeType",
    "DataType",
    "DerivationType",
    "DetectedFormat",
    "FileFormat",
    "IoRole",
    "RelationshipType",
    # models
    "CanonicalAnalysisModel",
    "CanonicalColumn",
    "CanonicalFile",
    "CanonicalSheet",
    "ColumnStats",
    "EntityRef",
    "SourceTargetRelationship",
]
