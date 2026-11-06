# ============================================================================
# Deliverable 1 - Ingestion package exports.
# ============================================================================
"""
ingestion
=========
Sprint 1 file ingestion + canonical-model construction for the DVTS pipeline.

Public entry point::

    from ingestion import IngestionService
    model = IngestionService().ingest_session("<session_id>")
"""
from __future__ import annotations

from ingestion.canonical_builder import CanonicalBuilder
from ingestion.ingestion_service import IngestionService
from ingestion.parsers.base import (
    FileParseError,
    IngestionError,
    ParserNotImplementedError,
)
from ingestion.relationship_detector import RelationshipDetector

__all__ = [
    "IngestionService",
    "CanonicalBuilder",
    "RelationshipDetector",
    "IngestionError",
    "FileParseError",
    "ParserNotImplementedError",
]
