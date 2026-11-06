# ============================================================================
# Deliverable 1 - Deterministic source-to-target relationship detection.
# REUSABLE. Identifies input->output relationships at file, sheet, and column
# level (tasks 13-21) using name/position/data-overlap heuristics only (no LLM).
# Kept intentionally decoupled from transformation detection (Deliverable 5):
# this layer records *relationships*, not transformation logic.
# ============================================================================
"""
ingestion.relationship_detector
===============================
Deterministic input-to-output relationship identification.

Heuristics (in priority order), reused from the proven column-mapping strategy
in ``utils/transformations/columns_mappings.py``:

  1. Exact (case-insensitive) name match.
  2. Fuzzy name match via rapidfuzz (token/ratio), above a threshold.
  3. Position-based fallback for otherwise-unmatched target columns.

Outputs :class:`SourceTargetRelationship` records at three levels:
  * file   - which output file derives from which input file(s)  (task 13)
  * sheet  - which output sheet derives from which input sheet(s) (task 14)
  * column - direct/renamed/derived/added/removed column mappings (tasks 15-19)
"""
from __future__ import annotations

import logging
from typing import Optional

from rapidfuzz import fuzz

from canonical.enums import (
    ColumnChangeType,
    DerivationType,
    RelationshipType,
)
from canonical.models import (
    CanonicalColumn,
    CanonicalFile,
    CanonicalSheet,
    EntityRef,
    SourceTargetRelationship,
)

logger = logging.getLogger(__name__)

# Fuzzy name-match acceptance threshold (0-100).
_NAME_FUZZY_THRESHOLD = 82.0

# Data-overlap acceptance threshold (0-1) for detecting a renamed column when
# names differ but the underlying values are carried over unchanged.
_VALUE_OVERLAP_THRESHOLD = 0.6


def _normalize(name: str) -> str:
    """Normalize a column/sheet name for comparison."""
    return "".join(ch for ch in name.lower().strip() if ch.isalnum())


def _column_ref(file: CanonicalFile, sheet: CanonicalSheet, column: CanonicalColumn) -> EntityRef:
    """Build a fully-qualified :class:`EntityRef` for a column."""
    return EntityRef(
        file_uuid=file.uuid,
        file_name=file.name,
        sheet_uuid=sheet.uuid,
        sheet_name=sheet.name,
        column_uuid=column.uuid,
        column_name=column.name,
    )


def _sheet_ref(file: CanonicalFile, sheet: CanonicalSheet) -> EntityRef:
    """Build an :class:`EntityRef` for a sheet."""
    return EntityRef(
        file_uuid=file.uuid,
        file_name=file.name,
        sheet_uuid=sheet.uuid,
        sheet_name=sheet.name,
    )


def _file_ref(file: CanonicalFile) -> EntityRef:
    """Build an :class:`EntityRef` for a file."""
    return EntityRef(file_uuid=file.uuid, file_name=file.name)


class RelationshipDetector:
    """Deterministically detect input-to-output relationships."""

    def detect(
        self,
        input_files: list[CanonicalFile],
        output_files: list[CanonicalFile],
    ) -> list[SourceTargetRelationship]:
        """Return relationships across file, sheet, and column levels."""
        relationships: list[SourceTargetRelationship] = []

        for out_file in output_files:
            best_in_file = self._best_matching_file(out_file, input_files)
            if best_in_file is not None:
                relationships.append(self._file_relationship(best_in_file, out_file))

            for out_sheet in out_file.sheets:
                source = self._best_matching_sheet(out_sheet, input_files)
                if source is None:
                    continue
                in_file, in_sheet = source
                relationships.append(self._sheet_relationship(in_file, in_sheet, out_file, out_sheet))
                relationships.extend(
                    self._column_relationships(in_file, in_sheet, out_file, out_sheet)
                )

        logger.info("Detected %d source-to-target relationship(s)", len(relationships))
        return relationships

    # -- file level (task 13) ----------------------------------------------
    def _best_matching_file(
        self, out_file: CanonicalFile, input_files: list[CanonicalFile]
    ) -> Optional[CanonicalFile]:
        """Pick the input file whose columns best overlap the output file."""
        out_cols = self._all_column_names(out_file)
        best: Optional[CanonicalFile] = None
        best_score = 0.0
        for in_file in input_files:
            in_cols = self._all_column_names(in_file)
            score = self._overlap_ratio(in_cols, out_cols)
            if score > best_score:
                best_score, best = score, in_file
        return best

    def _file_relationship(
        self, in_file: CanonicalFile, out_file: CanonicalFile
    ) -> SourceTargetRelationship:
        return SourceTargetRelationship(
            level="file",
            sources=[_file_ref(in_file)],
            targets=[_file_ref(out_file)],
            relationship_type=RelationshipType.ONE_TO_ONE,
            confidence=0.6,
            explanation=f"Output file '{out_file.name}' best matches input file '{in_file.name}'.",
        )

    # -- sheet level (task 14) ---------------------------------------------
    def _best_matching_sheet(
        self, out_sheet: CanonicalSheet, input_files: list[CanonicalFile]
    ) -> Optional[tuple[CanonicalFile, CanonicalSheet]]:
        """Pick the input sheet with the highest column overlap."""
        out_cols = out_sheet.column_names()
        best: Optional[tuple[CanonicalFile, CanonicalSheet]] = None
        best_score = 0.0
        for in_file in input_files:
            for in_sheet in in_file.sheets:
                score = self._overlap_ratio(in_sheet.column_names(), out_cols)
                if score > best_score:
                    best_score, best = score, (in_file, in_sheet)
        return best

    def _sheet_relationship(
        self,
        in_file: CanonicalFile,
        in_sheet: CanonicalSheet,
        out_file: CanonicalFile,
        out_sheet: CanonicalSheet,
    ) -> SourceTargetRelationship:
        return SourceTargetRelationship(
            level="sheet",
            sources=[_sheet_ref(in_file, in_sheet)],
            targets=[_sheet_ref(out_file, out_sheet)],
            relationship_type=RelationshipType.ONE_TO_ONE,
            derivation_type=DerivationType.SINGLE_SOURCE,
            confidence=0.6,
            explanation=(
                f"Output sheet '{out_sheet.name}' best matches input sheet "
                f"'{in_sheet.name}' of '{in_file.name}'."
            ),
        )

    # -- column level (tasks 15-19) ----------------------------------------
    def _column_relationships(
        self,
        in_file: CanonicalFile,
        in_sheet: CanonicalSheet,
        out_file: CanonicalFile,
        out_sheet: CanonicalSheet,
    ) -> list[SourceTargetRelationship]:
        """Classify each output column as direct/renamed/added, and inputs as removed."""
        results: list[SourceTargetRelationship] = []
        in_by_norm = {_normalize(c.name): c for c in in_sheet.columns}
        matched_input_uuids: set[str] = set()

        for out_col in out_sheet.columns:
            norm = _normalize(out_col.name)

            # 1) exact (normalized) name match -> direct
            direct = in_by_norm.get(norm)
            if direct is not None:
                matched_input_uuids.add(direct.uuid)
                change = (
                    ColumnChangeType.DIRECT
                    if direct.name == out_col.name
                    else ColumnChangeType.RENAMED
                )
                results.append(
                    self._column_relationship(
                        in_file, in_sheet, direct, out_file, out_sheet, out_col, change, 0.95
                    )
                )
                continue

            # 2) fuzzy name match -> renamed
            fuzzy = self._fuzzy_match(out_col, in_sheet.columns, matched_input_uuids)
            if fuzzy is not None:
                match_col, score = fuzzy
                matched_input_uuids.add(match_col.uuid)
                results.append(
                    self._column_relationship(
                        in_file, in_sheet, match_col, out_file, out_sheet, out_col,
                        ColumnChangeType.RENAMED, round(score / 100.0, 3),
                    )
                )
                continue

            # 2.5) data-driven match -> renamed (names differ, values carried over)
            value_match = self._value_match(out_col, in_sheet.columns, matched_input_uuids)
            if value_match is not None:
                match_col, score = value_match
                matched_input_uuids.add(match_col.uuid)
                results.append(
                    self._column_relationship(
                        in_file, in_sheet, match_col, out_file, out_sheet, out_col,
                        ColumnChangeType.RENAMED, score,
                    )
                )
                continue

            # 3) no source -> added in output (task 17)
            results.append(
                SourceTargetRelationship(
                    level="column",
                    sources=[],
                    targets=[_column_ref(out_file, out_sheet, out_col)],
                    change_type=ColumnChangeType.ADDED,
                    confidence=0.5,
                    explanation=f"Output column '{out_col.name}' has no matching input column.",
                )
            )

        # 4) unmatched input columns -> removed from output (task 18)
        for in_col in in_sheet.columns:
            if in_col.uuid not in matched_input_uuids:
                results.append(
                    SourceTargetRelationship(
                        level="column",
                        sources=[_column_ref(in_file, in_sheet, in_col)],
                        targets=[],
                        change_type=ColumnChangeType.REMOVED,
                        confidence=0.5,
                        explanation=f"Input column '{in_col.name}' is not present in the output.",
                    )
                )
        return results

    def _column_relationship(
        self,
        in_file: CanonicalFile,
        in_sheet: CanonicalSheet,
        in_col: CanonicalColumn,
        out_file: CanonicalFile,
        out_sheet: CanonicalSheet,
        out_col: CanonicalColumn,
        change_type: ColumnChangeType,
        confidence: float,
    ) -> SourceTargetRelationship:
        return SourceTargetRelationship(
            level="column",
            sources=[_column_ref(in_file, in_sheet, in_col)],
            targets=[_column_ref(out_file, out_sheet, out_col)],
            relationship_type=RelationshipType.ONE_TO_ONE,
            change_type=change_type,
            confidence=confidence,
            explanation=(
                f"Output column '{out_col.name}' maps from input column "
                f"'{in_col.name}' ({change_type.value})."
            ),
        )

    def _fuzzy_match(
        self,
        out_col: CanonicalColumn,
        in_cols: list[CanonicalColumn],
        already_matched: set[str],
    ) -> Optional[tuple[CanonicalColumn, float]]:
        """Return the best fuzzy-matched input column above the threshold."""
        best: Optional[CanonicalColumn] = None
        best_score = 0.0
        for in_col in in_cols:
            if in_col.uuid in already_matched:
                continue
            score = fuzz.token_sort_ratio(out_col.name.lower(), in_col.name.lower())
            if score > best_score:
                best_score, best = score, in_col
        if best is not None and best_score >= _NAME_FUZZY_THRESHOLD:
            return best, best_score
        return None

    def _value_match(
        self,
        out_col: CanonicalColumn,
        in_cols: list[CanonicalColumn],
        already_matched: set[str],
    ) -> Optional[tuple[CanonicalColumn, float]]:
        """Return the best unmatched input column whose values overlap the output.

        Detects a renamed column when the header changed but the underlying data
        was carried over. Uses the captured ``sample_values`` as a representative
        signature (deterministic, no LLM), requiring overlap above
        :data:`_VALUE_OVERLAP_THRESHOLD`.
        """
        out_values = out_col.sample_values or []
        if not out_values:
            return None
        best: Optional[CanonicalColumn] = None
        best_score = 0.0
        for in_col in in_cols:
            if in_col.uuid in already_matched:
                continue
            score = self._value_overlap(out_values, in_col.sample_values or [])
            if score > best_score:
                best_score, best = score, in_col
        if best is not None and best_score >= _VALUE_OVERLAP_THRESHOLD:
            return best, round(best_score, 3)
        return None

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _all_column_names(file: CanonicalFile) -> list[str]:
        return [c.name for sheet in file.sheets for c in sheet.columns]

    @staticmethod
    def _overlap_ratio(a: list[str], b: list[str]) -> float:
        """Jaccard-like overlap of normalized name sets."""
        set_a = {_normalize(x) for x in a}
        set_b = {_normalize(x) for x in b}
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    @staticmethod
    def _value_overlap(a: list, b: list) -> float:
        """Jaccard overlap of two value samples (stringified, null-safe)."""
        set_a = {str(x) for x in a if x is not None}
        set_b = {str(x) for x in b if x is not None}
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)
