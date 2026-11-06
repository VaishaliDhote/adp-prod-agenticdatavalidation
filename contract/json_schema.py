# ============================================================================
# Deliverable 2 - Exact JSON Contract & Configuration Structure (task 22).
# JSON Schema for the contract, generated from the Pydantic v2 models so the
# schema and the models can never drift apart.
# ============================================================================
"""
contract.json_schema
====================
Generate and persist a JSON Schema (Draft 2020-12) for the configuration
contract from :class:`contract.models.ConfigurationContract`.

Because the schema is derived from the models, it automatically reflects the
exact key names, casing, section order, required/optional/nullable fields, and
enum keyword vocabularies defined in :mod:`contract.models`.

Note on embedded JSON-string fields
------------------------------------
The contract's "nested JSON-string" fields (e.g. ``outputFileRules``,
``dataTransformationRules``) are serialized as JSON *strings*, so at the outer
contract level the schema types them as ``string``/``null`` - which is exactly
what a raw contract document contains. The inner structure of those blobs is
modeled and validated separately by :mod:`contract.blobs` and the deterministic
validator in :mod:`contract.validation`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contract.models import ConfigurationContract

#: Location of the persisted schema file (written by :func:`write_contract_json_schema`).
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
SCHEMA_PATH = SCHEMA_DIR / "contract_schema.json"

#: JSON Schema dialect the generated schema targets.
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def build_contract_json_schema() -> dict[str, Any]:
    """Build the JSON Schema for the complete configuration contract.

    Returns:
        A JSON-Schema-compatible ``dict`` describing
        :class:`~contract.models.ConfigurationContract`, using the field aliases
        (kebab-case section names) as property keys.
    """

    schema = ConfigurationContract.model_json_schema(by_alias=True)
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema.setdefault("title", "DVTS Configuration Contract")
    schema.setdefault(
        "description",
        "Exact JSON contract for the DVTS payroll data-transformation "
        "configuration (Deliverable 2).",
    )
    return schema


def write_contract_json_schema(path: Path | str | None = None) -> Path:
    """Write the generated JSON Schema to disk.

    Args:
        path: Optional destination path. Defaults to :data:`SCHEMA_PATH`.

    Returns:
        The path the schema was written to.
    """

    target = Path(path) if path is not None else SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_contract_json_schema(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


__all__ = [
    "SCHEMA_DIR",
    "SCHEMA_PATH",
    "JSON_SCHEMA_DIALECT",
    "build_contract_json_schema",
    "write_contract_json_schema",
]
