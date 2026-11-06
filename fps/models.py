# ============================================================================
# Deliverable 8/10 - FPS integration: connection config + wire models.
#
# All models are deterministic and JSON-serialisable so a run can be returned
# verbatim over HTTP and asserted in tests. Connection settings come from
# ``FPS_*`` environment variables (staging uses ``app-code`` auth only; the
# audit endpoint uses a ``jwttoken`` header).
# ============================================================================
"""fps.models - configuration and wire models for the FPS integration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Connection configuration (task 2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FpsConfig:
    """Connection settings for the three FPS staging services.

    Staging authenticates with ``app-code`` alone on the shared-file and
    file-processing hosts; the audit host uses a ``jwttoken`` header.
    """

    shared_file_base_url: str
    process_base_url: str
    audit_base_url: str
    app_code: str = "ahub-fileprocessing"
    source: str = "DataStudio"
    jwt_token: str = ""
    audit_esid: str = ""
    audit_team_id: str = ""
    origin: str = "https://adp-datastudio-stg.esi.adp.com"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    verify_ssl: bool = True

    # Endpoint paths (kept configurable but rarely change).
    upload_path: str = "/ahub/v1/shared-file/upload-file-by-location"
    process_path: str = "/ahub/fileprocessing/v2/shared-files/process"
    request_by_path: str = "/ahub/fileprocessing/v2/request/by"
    audit_path: str = "/audits"

    @property
    def configured(self) -> bool:
        """True when the real FPS hosts are configured (else offline)."""
        return bool(
            self.shared_file_base_url.strip()
            and self.process_base_url.strip()
        )

    @classmethod
    def from_env(cls) -> "FpsConfig":
        """Build config from ``FPS_*`` environment variables."""

        def _f(name: str, default: float) -> float:
            raw = os.getenv(name)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _i(name: str, default: int) -> int:
            raw = os.getenv(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        verify_raw = os.getenv("FPS_VERIFY_SSL")
        verify = True if verify_raw is None else verify_raw.strip().lower() not in {"0", "false", "no"}

        return cls(
            shared_file_base_url=(os.getenv("FPS_SHARED_FILE_BASE_URL") or "").strip().rstrip("/"),
            process_base_url=(os.getenv("FPS_PROCESS_BASE_URL") or "").strip().rstrip("/"),
            audit_base_url=(os.getenv("FPS_AUDIT_BASE_URL") or "").strip().rstrip("/"),
            app_code=(os.getenv("FPS_APP_CODE") or "ahub-fileprocessing").strip(),
            source=(os.getenv("FPS_SOURCE") or "DataStudio").strip(),
            jwt_token=(os.getenv("FPS_JWT_TOKEN") or "").strip(),
            audit_esid=(os.getenv("FPS_AUDIT_ESID") or "").strip(),
            audit_team_id=(os.getenv("FPS_AUDIT_TEAM_ID") or "").strip(),
            origin=(os.getenv("FPS_ORIGIN") or "https://adp-datastudio-stg.esi.adp.com").strip(),
            timeout_seconds=_f("FPS_TIMEOUT_SECONDS", 60.0),
            max_retries=_i("FPS_MAX_RETRIES", 2),
            verify_ssl=verify,
        )


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------
@dataclass
class FpsDocumentRef:
    """One file that participates in a process request.

    ``location`` is the fully-qualified SMB path the backend reads from
    (``<fileLocation><fileName>``); ``source_key`` labels the file's role.
    """

    source_key: str
    file_name: str
    location: str

    def as_process_document(self) -> dict[str, str]:
        return {"sourceKey": self.source_key, "location": self.location}


@dataclass
class FpsUploadResult:
    """Outcome of a single ``upload-file-by-location`` call."""

    file_name: str
    source_key: str
    file_location: str          # the SMB directory (ends with '/')
    location: str               # full SMB path to the file
    success: bool
    status_code: int
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_ref(self) -> FpsDocumentRef:
        return FpsDocumentRef(
            source_key=self.source_key,
            file_name=self.file_name,
            location=self.location,
        )


@dataclass
class FpsProcessResult:
    """Outcome of the ``/v2/shared-files/process`` trigger."""

    success: bool
    status_code: int
    unique_id: str
    request_id: Optional[str] = None
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
