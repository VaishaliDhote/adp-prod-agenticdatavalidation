# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# The deterministic JSON assembler (tasks 1-16, 22).
#
# THE deterministic JSON compiler at the heart of Deliverable 7. It combines the
# five sections - column-relations (generated here from the canonical model),
# configuration-data (generated here), and the already-compiled
# dataset-transformation-rules / transformation-rules / validation-rules blocks
# from Deliverables 6 & 4 - into ONE exact ``ConfigurationContract``. The LLM
# never rewrites this JSON: identity is re-stamped for cross-section consistency
# (tasks 7, 21), every mandatory field is populated (task 8), dynamic metadata
# comes from ingestion (task 9), UUIDs are preserved/synthesised deterministically
# (tasks 10, 11), and the exact key names/casing/keyword casing/hierarchy are
# guaranteed structurally by constructing typed contract models (tasks 12-15).
# The same inputs always yield byte-identical JSON (tasks 16, 22).
# ============================================================================
"""
complete_config.assembler
=========================
Deterministically assemble a complete ``ConfigurationContract`` (Sprint 7 tasks 1-16, 22).

Usage::

    from complete_config import CompleteConfigAssembler, CompleteConfigContext

    contract = CompleteConfigAssembler().assemble(
        model=canonical_model,
        context=CompleteConfigContext(bu="ANZ", platform="Payline", data_category="Hire"),
        validation_rules=validation_rows,        # from Deliverable 4 compiler
        transformation_rules=transformation_rows,  # from Deliverable 6 compiler
        dataset_transformation_rules=dataset_rows,  # from Deliverable 6 compiler
    )
    document = contract.to_contract_dict()
    json_text = contract.to_contract_json(indent=2)
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from canonical.models import CanonicalAnalysisModel
from contract.models import (
    ColumnRelation,
    ConfigurationContract,
    ConfigurationData,
)
from complete_config.column_relations import ColumnRelationsBuilder
from complete_config.configuration_data import ConfigurationDataBuilder
from complete_config.models import CompleteConfigContext

logger = logging.getLogger(__name__)

# Identity-triple + audit keys re-stamped onto every combined row so all five
# sections share ONE identity (Sprint 7 tasks 7, 21). Mapped from the context.
_IDENTITY_KEYS = ("bu", "platform", "dataCategory", "useremail", "userrole")


class CompleteConfigAssembler:
    """Combine all five sections into one exact, deterministic contract."""

    def __init__(
        self,
        column_relations_builder: Optional[ColumnRelationsBuilder] = None,
        configuration_data_builder: Optional[ConfigurationDataBuilder] = None,
    ) -> None:
        self._columns = column_relations_builder or ColumnRelationsBuilder()
        self._config_data = configuration_data_builder or ConfigurationDataBuilder()

    # -- public entry point -------------------------------------------------
    def assemble(
        self,
        *,
        model: CanonicalAnalysisModel,
        context: CompleteConfigContext,
        validation_rules: Optional[list[dict[str, Any]]] = None,
        transformation_rules: Optional[list[dict[str, Any]]] = None,
        dataset_transformation_rules: Optional[list[dict[str, Any]]] = None,
    ) -> ConfigurationContract:
        """Assemble the complete configuration document (tasks 2-16, 22).

        Args:
            model: The Deliverable 1 canonical analysis model (source of
                column-relations + configuration-data).
            context: The single identity/audit/delivery context.
            validation_rules: Pre-compiled ``validation-rules`` rows (Deliverable
                4). ``None``/omitted -> empty section.
            transformation_rules: Pre-compiled ``transformation-rules`` rows
                (Deliverable 6). ``None``/omitted -> empty section.
            dataset_transformation_rules: Pre-compiled
                ``dataset-transformation-rules`` rows (Deliverable 6).

        Returns:
            A validated :class:`ConfigurationContract`. Constructed via typed
            contract models, so the emitted document always has the exact keys,
            casing, keyword casing and hierarchy (tasks 12-15).
        """
        # 2) combine column-relations (from the canonical model).
        column_rows = self._combine_column_relations(model, context)
        # 3) combine configuration-data (header + engine rules).
        config_data = self._combine_configuration_data(model, context)
        # 4/5/6) combine the pre-compiled rule blocks, re-stamping identity so
        # every section is consistent (tasks 7, 21).
        dataset_rows = self._combine_rows(dataset_transformation_rules, context)
        transform_rows = self._combine_rows(transformation_rules, context)
        validation_rows = self._combine_rows(validation_rules, context)

        # 16/22) build the exact, deterministic document and validate it into the
        # typed envelope (guarantees exact keys/casing/hierarchy structurally).
        document = self._build_document(
            column_rows=column_rows,
            config_data=config_data,
            dataset_rows=dataset_rows,
            transform_rows=transform_rows,
            validation_rows=validation_rows,
        )
        contract = ConfigurationContract.model_validate(document)
        logger.info(
            "[complete-config] assembled columns=%d validation=%d transformation=%d "
            "dataset=%d",
            len(column_rows),
            len(validation_rows),
            len(transform_rows),
            len(dataset_rows),
        )
        return contract

    # -- task 2: column-relations ------------------------------------------
    def _combine_column_relations(
        self, model: CanonicalAnalysisModel, context: CompleteConfigContext
    ) -> list[ColumnRelation]:
        """Combine source-to-target column relationships (task 2, 11)."""
        return self._columns.build(model, context)

    # -- task 3: configuration-data ----------------------------------------
    def _combine_configuration_data(
        self, model: CanonicalAnalysisModel, context: CompleteConfigContext
    ) -> ConfigurationData:
        """Combine configuration data + dynamic metadata (tasks 3, 9)."""
        return self._config_data.build(model, context)

    # -- tasks 4/5/6: combine + re-stamp identity on pre-compiled rows ------
    @staticmethod
    def _combine_rows(
        rows: Optional[list[dict[str, Any]]], context: CompleteConfigContext
    ) -> list[dict[str, Any]]:
        """Deep-copy each row and re-stamp the identity triple + audit (task 7).

        Re-stamping guarantees configuration consistency (task 21) even when the
        pre-compiled rows were produced with a different context: the assembler
        is the single authority for the final identity triple. Only keys that
        already exist on the row are overwritten (the contract shapes differ per
        section), so no foreign keys are introduced.
        """
        if not rows:
            return []
        identity = {
            "bu": context.bu,
            "platform": context.platform,
            "dataCategory": context.data_category,
            "useremail": context.useremail,
            "userrole": context.userrole,
        }
        combined: list[dict[str, Any]] = []
        for row in rows:
            new_row = copy.deepcopy(row)
            for key in _IDENTITY_KEYS:
                if key in new_row:
                    new_row[key] = identity[key]
            combined.append(new_row)
        return combined

    # -- tasks 16/22: deterministic document ------------------------------
    @staticmethod
    def _build_document(
        *,
        column_rows: list[ColumnRelation],
        config_data: ConfigurationData,
        dataset_rows: list[dict[str, Any]],
        transform_rows: list[dict[str, Any]],
        validation_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the exact contract document dict (kebab-case sections)."""
        return {
            "data": {
                "column-relations": [
                    c.model_dump(by_alias=True, mode="json") for c in column_rows
                ],
                "configuration-data": config_data.model_dump(
                    by_alias=True, mode="json"
                ),
                "dataset-transformation-rules": dataset_rows,
                "transformation-rules": transform_rows,
                "validation-rules": validation_rows,
            },
            "error": None,
            "message": None,
            "status": "success",
        }


__all__ = ["CompleteConfigAssembler"]
