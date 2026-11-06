# ============================================================================
# Deliverable 7 - Complete JSON Configuration Generation (Sprint 7).
# The generation context + result models (tasks 1, 8, 9).
#
# Deliverable 7 combines the outputs of Deliverables 1-6 into ONE complete,
# deterministic ``ConfigurationContract`` document. The single source of
# identity + audit + file/delivery metadata for that assembly is the
# :class:`CompleteConfigContext`. It is intentionally the ONLY place a caller
# supplies free-form values (the identity triple, the owner, the delivery
# target); everything else is derived deterministically from the canonical
# model and the already-compiled validation/transformation blocks, so the same
# inputs always yield byte-identical JSON (task 22).
# ============================================================================
"""
complete_config.models
======================
Generation context and result bundle for complete-configuration assembly.

Model hierarchy::

    CompleteConfigContext        # task 1/8/9 - the single identity/metadata source
      -> validation_context()    # -> validation_ir.ValidationGenerationContext
      -> transformation_context() # -> transformation_ir.TransformationGenerationContext

    CompleteConfigResult         # task 16/22 - the assembled document + report
      ├── contract: ConfigurationContract
      ├── document: dict          # exact contract dict (kebab-case, blob strings)
      ├── json: str               # exact deterministic JSON document string
      └── report: CompleteConfigReport (from complete_config.validation)

Design contract
---------------
* **Deterministic** - the context carries no timestamps or random values; every
  identity/metadata field is caller-supplied or a fixed default, so assembly is
  reproducible (task 22).
* **Consistent** - :meth:`validation_context` and :meth:`transformation_context`
  project the SAME identity triple + audit onto the sub-block generators, which
  is what guarantees cross-section consistency (task 21).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from contract.enums import ValidationRuleType
from transformation_ir.models import TransformationGenerationContext
from validation_ir.models import ValidationGenerationContext


# ---------------------------------------------------------------------------
# Generation context (identity triple + audit + file/delivery metadata)
# ---------------------------------------------------------------------------
class CompleteConfigContext(BaseModel):
    """Identity / audit / delivery metadata for a complete configuration.

    This is the single source of the ``bu``/``platform``/``dataCategory``
    identity triple stamped onto EVERY section and row (column-relations,
    configuration-data, dataset-transformation-rules, transformation-rules and
    validation-rules), plus the audit owner and the optional file/delivery
    metadata that populate ``configuration-data`` (Sprint 7 tasks 3, 8, 9).

    Sensible defaults make standalone generation self-consistent and
    contract-valid; a real caller (the orchestrator) injects the true identity
    triple, the authenticated owner, and the delivery target.
    """

    model_config = ConfigDict(extra="forbid")

    # -- identity triple (denormalised onto every section/row) -------------
    bu: str = Field("DVTS", description="Business unit (identity triple)")
    platform: str = Field("GENERIC", description="Destination platform (identity triple)")
    data_category: str = Field(
        "GENERATED", description="Business use-case (identity triple)"
    )

    # -- audit ownership ----------------------------------------------------
    useremail: str = Field("dvts@adp.com", description="Audit owner email")
    userrole: str = Field("DVTS_USER", description="Audit owner role")

    # -- validation-specific default ---------------------------------------
    validation_rule_type: ValidationRuleType = Field(
        ValidationRuleType.CLIENT,
        description="validation-rules.validationRuleType ('client' or 'CAM')",
    )
    is_skip_validations: bool = Field(
        False, description="validation-rules.isSkipValidaitons master switch"
    )

    # -- configuration-data header metadata (task 9, all optional) ---------
    config_id: Optional[int] = Field(
        None, description="Backend configuration PK (server-generated; usually null)"
    )
    app_code: Optional[str] = Field(
        None, description="Hosting app/tenant ('ahub-*'); configuration-data.appCode"
    )
    source: Optional[str] = Field(
        None, description="Upstream capture tool (DataStudio/MyDC/RPA); configuration-data.source"
    )
    target_location: Optional[str] = Field(
        None, description="Delivery kind (SFG/SharePoint/SharedDrive); configuration-data.targetLocation"
    )
    target_path: Optional[str] = Field(
        None, description="Concrete output folder path; configuration-data.targetPath"
    )
    process_type: Optional[str] = Field(
        None, description="'chunks'/'in_memory' execution strategy; configuration-data.processType"
    )

    # -- projection helpers -------------------------------------------------
    def validation_context(self) -> ValidationGenerationContext:
        """Project this context onto the Deliverable 4 validation generator.

        Guarantees the generated ``validation-rules`` rows carry the SAME
        identity triple + audit + rule-type as every other section (task 21).
        """
        return ValidationGenerationContext(
            bu=self.bu,
            platform=self.platform,
            data_category=self.data_category,
            useremail=self.useremail,
            userrole=self.userrole,
            validation_rule_type=self.validation_rule_type,
            is_skip_validations=self.is_skip_validations,
        )

    def transformation_context(self) -> TransformationGenerationContext:
        """Project this context onto the Deliverable 6 transformation generator.

        Guarantees the generated ``transformation-rules`` /
        ``dataset-transformation-rules`` rows carry the SAME identity triple +
        audit as every other section (task 21).
        """
        return TransformationGenerationContext(
            bu=self.bu,
            platform=self.platform,
            data_category=self.data_category,
            useremail=self.useremail,
            userrole=self.userrole,
        )


__all__ = ["CompleteConfigContext"]
