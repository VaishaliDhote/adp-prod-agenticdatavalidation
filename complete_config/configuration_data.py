# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# The configuration-data block generator (tasks 3, 8, 9, 10).
#
# Builds the file/dataset-level HEADER of the configuration: the identity triple,
# the audit owner, the optional delivery metadata (appCode/source/targetLocation/
# targetPath/processType), and - derived deterministically from the Deliverable 1
# canonical model - the embedded-JSON engine rules ``filesInfo`` (how each input
# source is parsed) and ``inputRules`` (the dataset graph). Every reserved key
# the corpus never populates (processSteps, subDataCategory, outputRules,
# dataRules) is emitted as null so the exact 18-key shape is preserved without
# inventing content. Dataset ids reuse the canonical SHEET uuid so they are
# stable across runs (task 22) and resolvable by dataset-transformation-rules.
# ============================================================================
"""
complete_config.configuration_data
==================================
Deterministically build the ``configuration-data`` header (Sprint 7 tasks 3, 9).
"""
from __future__ import annotations

from typing import Optional

from canonical.enums import IoRole
from canonical.models import CanonicalAnalysisModel, CanonicalFile
from contract.blobs import FilesInfoEntry, InputRuleEntry, OutputFileRuleEntry
from contract.models import ConfigurationData
from complete_config.models import CompleteConfigContext

# Default header-row index for a well-formed tabular source (row 0). Matches the
# dominant value in the corpus; encoded once so intent is explicit.
_DEFAULT_HEADER_INDEX = 0
_DEFAULT_PROCESSING = "header"


class ConfigurationDataBuilder:
    """Build the exact ``configuration-data`` object from a canonical model."""

    def build(
        self,
        model: CanonicalAnalysisModel,
        context: CompleteConfigContext,
    ) -> ConfigurationData:
        """Combine header metadata + engine rules into ``configuration-data``.

        Args:
            model: The Deliverable 1 canonical analysis model.
            context: The identity/audit/delivery context (tasks 8, 9).

        Returns:
            A :class:`ConfigurationData` with the exact 18 keys; the embedded
            ``filesInfo`` / ``inputRules`` are typed blob models that serialize
            to the contract's JSON-string form.
        """
        files_info = self._build_files_info(model)
        input_rules = self._build_input_rules(model)
        output_file_rules = self._build_output_file_rules(model)

        return ConfigurationData(
            appCode=context.app_code,
            bu=context.bu,
            dataCategory=context.data_category,
            dataRules=None,
            filesInfo=files_info or None,
            id=context.config_id,
            inputRules=input_rules or None,
            outputFileRules=output_file_rules or None,
            outputRules=None,
            platform=context.platform,
            processSteps=None,
            processType=context.process_type,
            source=context.source,
            subDataCategory=None,
            targetLocation=context.target_location,
            targetPath=context.target_path,
            useremail=context.useremail,
            userrole=context.userrole,
        )

    # -- filesInfo (how each INPUT source is parsed) -----------------------
    def _build_files_info(
        self, model: CanonicalAnalysisModel
    ) -> list[FilesInfoEntry]:
        """One ``filesInfo`` entry per input sheet (Sprint 1 ingestion facts)."""
        entries: list[FilesInfoEntry] = []
        for file in model.input_files:
            file_type = self._source_type(file)
            for sheet in file.sheets:
                entries.append(
                    FilesInfoEntry(
                        sourceKey=sheet.name,
                        type=file_type,
                        processing=_DEFAULT_PROCESSING,
                        headerIndex=_DEFAULT_HEADER_INDEX,
                    )
                )
        return entries

    # -- inputRules (dataset graph) ----------------------------------------
    def _build_input_rules(
        self, model: CanonicalAnalysisModel
    ) -> list[InputRuleEntry]:
        """One ``inputRules`` dataset per input sheet; dataSetId == sheet uuid.

        Reusing the canonical sheet UUID as the ``dataSetId`` keeps generation
        deterministic (task 22) and lets dataset-level rules that target a
        dataset resolve their reference (Sprint 7 task 7).
        """
        entries: list[InputRuleEntry] = []
        index = 0
        for file in model.input_files:
            for sheet in file.sheets:
                index += 1
                entries.append(
                    InputRuleEntry(
                        sourceKey=sheet.name,
                        type="source",
                        dataSetId=sheet.uuid,
                        dataSetName=f"DS{index}",
                        headerIndex=_DEFAULT_HEADER_INDEX,
                        sequence=str(index - 1),
                        outputProcessType="Default",
                        references=[],
                    )
                )
        return entries

    # -- outputFileRules (how each OUTPUT file is built) -------------------
    def _build_output_file_rules(
        self, model: CanonicalAnalysisModel
    ) -> list[OutputFileRuleEntry]:
        """One minimal ``outputFileRules`` entry per output file.

        Emits the stable top-level shape (outputFileType + flat field list of
        ``{field: uuid, name}``); rich per-sheet templating is intentionally NOT
        invented here - only what the canonical model proves is emitted.
        """
        entries: list[OutputFileRuleEntry] = []
        for file in model.output_files:
            fields: list[dict[str, str]] = []
            for sheet in file.sheets:
                for column in sheet.columns:
                    fields.append({"field": column.uuid, "name": column.name})
            entries.append(
                OutputFileRuleEntry(
                    outputFileType=self._source_type(file),
                    fields=fields,
                )
            )
        return entries

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _source_type(file: CanonicalFile) -> Optional[str]:
        """Return the source/output file type token (the file format value)."""
        return file.file_format.value if file.file_format else None


__all__ = ["ConfigurationDataBuilder"]
