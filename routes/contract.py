# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure.
# HTTP routes exposing the contract layer over the live API so Deliverable 2
# can be verified against the running server (mirrors Deliverable 1's
# /ingest_session route). REUSABLE.
# ============================================================================
"""Contract routes: ``/contract/*``.

Exposes the Deliverable 2 exact-JSON-contract capabilities over HTTP:

* ``POST /contract/validate`` - deterministic contract validation of a posted
  configuration document (structure, types, keyword vocabularies, malformed
  embedded JSON blobs, cross-reference resolution).
* ``POST /contract/parse``    - parse + re-emit a document through the contract
  models, returning the exact contract JSON (for round-trip fidelity checks).
* ``GET  /contract/schema``   - the JSON Schema generated from the models.
* ``GET  /contract/metadata`` - the contract's structural metadata (section
  order, per-section key order, keyword vocabularies) used to verify that every
  Sprint 2 task is covered.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from contract.enums import (
    ContractDataType,
    DatasetRuleKeyword,
    InputFileType,
    JoinRelationship,
    OutputFileType,
    ProcessType,
    StringCheckConstraint,
    TransformationOp,
    ValidationRuleType,
)
from contract.json_schema import JSON_SCHEMA_DIALECT, build_contract_json_schema
from contract.models import (
    ColumnRelation,
    ConfigurationContract,
    ConfigurationData,
    DatasetTransformationRule,
    TransformationRule,
    ValidationRule,
)
from contract.validation import validate_contract
from core.errors import ErrorCode, build_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contract", tags=["contract"])


def _section_key_order(model: type) -> list[str]:
    """Return the exact stored key order (aliases) for a section model."""

    return [
        (field.alias or name)
        for name, field in model.model_fields.items()
    ]


@router.post("/validate")
async def contract_validate(payload: dict[str, Any]):
    """Validate a posted configuration document against the exact JSON contract.

    Body:
        The full configuration document (the ``{data, error, message, status}``
        envelope), as JSON.

    Returns:
        ``{status, is_valid, error_count, warning_count, issues[]}``.
    """

    try:
        result = validate_contract(payload)
    except Exception as e:  # noqa: BLE001 - normalize to the unified error shape
        logger.exception("[contract_validate] Unexpected validation failure")
        raise HTTPException(
            status_code=500,
            detail=build_error(ErrorCode.CONTRACT_VALIDATION_ERROR, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "is_valid": result.is_valid,
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "issues": [issue.model_dump(mode="json") for issue in result.issues],
        }
    )


@router.post("/parse")
async def contract_parse(payload: dict[str, Any]):
    """Parse a document through the contract models and re-emit exact JSON.

    Used to verify round-trip fidelity (exact key order, string-typed numerics,
    preserved sentinels/typos, blob (de)serialization) against the live API.
    """

    try:
        model = ConfigurationContract.model_validate(payload)
    except Exception as e:  # noqa: BLE001 - contract parse failure
        logger.warning("[contract_parse] Document failed contract parse: %s", e)
        raise HTTPException(
            status_code=422,
            detail=build_error(ErrorCode.CONTRACT_VALIDATION_ERROR, str(e)),
        )

    return JSONResponse(
        content={
            "status": "success",
            "contract": model.to_contract_dict(),
        }
    )


@router.get("/schema")
async def contract_schema():
    """Return the JSON Schema (Draft 2020-12) generated from the models."""

    return JSONResponse(content=build_contract_json_schema())


@router.get("/metadata")
async def contract_metadata():
    """Return the contract's structural metadata for task-coverage verification.

    Exposes the top-level envelope keys, the fixed 5-section order, the exact
    per-section key order (including the alphabetical validation-rules order and
    preserved typo keys), and the closed keyword vocabularies.
    """

    return JSONResponse(
        content={
            "status": "success",
            "schema_dialect": JSON_SCHEMA_DIALECT,
            "envelope_keys": ["data", "error", "message", "status"],
            "section_order": [
                "column-relations",
                "configuration-data",
                "dataset-transformation-rules",
                "transformation-rules",
                "validation-rules",
            ],
            "section_key_order": {
                "column-relations": _section_key_order(ColumnRelation),
                "configuration-data": _section_key_order(ConfigurationData),
                "dataset-transformation-rules": _section_key_order(DatasetTransformationRule),
                "transformation-rules": _section_key_order(TransformationRule),
                "validation-rules": _section_key_order(ValidationRule),
            },
            "keyword_vocabularies": {
                "dataType": [e.value for e in ContractDataType],
                "validationRuleType": [e.value for e in ValidationRuleType],
                "outputFileType": [e.value for e in OutputFileType],
                "inputFileType": [e.value for e in InputFileType],
                "joinRelationship": [e.value for e in JoinRelationship],
                "processType": [e.value for e in ProcessType],
                "transformationOps": [e.value for e in TransformationOp],
                "stringCheckConstraint": [e.value for e in StringCheckConstraint],
                "datasetRuleKeyword": [e.value for e in DatasetRuleKeyword],
            },
            # Nested "JSON-string" fields (Sprint 2 task 14) per section.
            "nested_json_string_fields": {
                "column-relations": ["dataExclusionRules"],
                "configuration-data": [
                    "dataRules",
                    "filesInfo",
                    "inputRules",
                    "outputFileRules",
                    "outputRules",
                ],
                "dataset-transformation-rules": ["rule"],
                "transformation-rules": [
                    "defaultValue",
                    "dataTransformationRules",
                    "specialCharToBeRemoved",
                    "dependsOn",
                ],
                "validation-rules": [
                    "conditionalValidationRule",
                    "dataExclusionRules",
                    "dataTransformationRules",
                    "dependsOn",
                    "specialCharNotAllowed",
                    "specialCharToBeRemoved",
                    "stringCheckRule",
                ],
            },
            # UUID / link fields (Sprint 2 task 13).
            "uuid_fields": {
                "column-relations": ["uuid"],
                "dataset-transformation-rules": ["dataSetId", "fieldUUID"],
                "transformation-rules": ["sourceColumnName"],
                "validation-rules": ["sourceColumn"],
            },
        }
    )
