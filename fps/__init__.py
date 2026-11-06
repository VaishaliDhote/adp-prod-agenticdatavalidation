# ============================================================================
# Deliverable 8/10 - Real ADP File Processing Service (FPS) integration.
#
# The production execution path used by DataStudio:
#   1. upload each input file to the SMB share
#        POST shared.file.handler.../ahub/v1/shared-file/upload-file-by-location
#   2. trigger validation + transformation
#        POST fps.ahub.../ahub/fileprocessing/v2/shared-files/process
#   3. poll / list request status
#        POST fps.ahub.../ahub/fileprocessing/v2/request/by
#   4. record an audit trail entry
#        POST api.adpds.../audits            (jwttoken header)
#
# This package is transport-only: config + models + path derivation + HTTP
# client. Orchestration (collect user files -> upload -> process -> persist)
# lives in ``services.fps_execution_service``.
# ============================================================================
"""ADP File Processing Service (FPS) integration package."""
from __future__ import annotations

from fps.client import FpsApiError, FpsClient
from fps.models import (
    FpsConfig,
    FpsDocumentRef,
    FpsProcessResult,
    FpsUploadResult,
)
from fps.paths import (
    EntityPaths,
    derive_source_key,
    generate_unique_id,
    resolve_entity_paths,
)

__all__ = [
    "FpsApiError",
    "FpsClient",
    "FpsConfig",
    "FpsDocumentRef",
    "FpsProcessResult",
    "FpsUploadResult",
    "EntityPaths",
    "derive_source_key",
    "generate_unique_id",
    "resolve_entity_paths",
]
