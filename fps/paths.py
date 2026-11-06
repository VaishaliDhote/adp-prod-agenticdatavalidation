# ============================================================================
# Deliverable 8/10 - FPS path derivation (per business entity) + identifiers.
#
# The SMB upload directory and the output ``saveFileLocation`` are derived per
# business entity (bu / platform / category). A JSON override map may be
# supplied via ``FPS_ENTITY_PATHS``; otherwise the base templates from
# ``FPS_UPLOAD_SMB_BASE`` / ``FPS_OUTPUT_BASE`` are used with the entity folded
# into the output path. Each execution gets a fresh short ``uniqueId`` (the
# same shape DataStudio uses, e.g. ``mukvxolp``).
# ============================================================================
"""fps.paths - resolve per-entity SMB locations and run identifiers."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import string
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Base templates taken from the DataStudio staging flow.
_DEFAULT_UPLOAD_SMB_BASE = "smb://fs01.ad.esi.adp.com/adpfiles/ESD/BluePrism/CCS/DEV/Upload/"
_DEFAULT_OUTPUT_BASE = "/adpfiles/ESD/BluePrism/CCS/UAT/{bu}/{platform}/Output/test/"

_ALPHABET = string.ascii_lowercase
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class EntityPaths:
    """Resolved SMB locations for one business entity + run.

    ``upload_dir`` ends with '/' and already includes the run's ``unique_id``,
    so a document location is simply ``upload_dir + file_name``.
    """

    upload_dir: str
    save_location: str

    def location_for(self, file_name: str) -> str:
        return f"{self.upload_dir}{file_name}"


def generate_unique_id(length: int = 8) -> str:
    """Return a short lowercase run id (matches the DataStudio shape)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def derive_source_key(file_name: str) -> str:
    """Derive a default ``sourceKey`` from a filename stem.

    ``arbeitnehmerstammdaten(1).csv`` -> ``Arbeitnehmerstammdaten``.
    """
    stem = os.path.splitext(os.path.basename(file_name))[0]
    stem = re.sub(r"\(\d+\)", "", stem)          # drop browser-style "(1)" suffixes
    stem = _SAFE_SEGMENT.sub(" ", stem).strip()
    if not stem:
        return "Document"
    # Title-case each word but keep existing internal capitalisation of acronyms.
    return " ".join(w[:1].upper() + w[1:] for w in stem.split())


def _entity_key(bu: str, platform: str, category: str) -> str:
    return f"{bu.strip()}/{platform.strip()}/{category.strip()}"


def _sanitize_path_segment(value: str) -> str:
    return _SAFE_SEGMENT.sub(" ", value).strip() or "Unknown"


def _load_overrides() -> dict[str, dict[str, str]]:
    raw = os.getenv("FPS_ENTITY_PATHS")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except (ValueError, TypeError) as exc:
        logger.warning("[fps] ignoring invalid FPS_ENTITY_PATHS: %s", exc)
    return {}


def resolve_entity_paths(
    bu: str,
    platform: str,
    category: str,
    unique_id: str,
) -> EntityPaths:
    """Resolve the upload directory and output location for an entity + run.

    Resolution order per field:
      1. an exact ``bu/platform/category`` entry in ``FPS_ENTITY_PATHS``
      2. the ``FPS_UPLOAD_SMB_BASE`` / ``FPS_OUTPUT_BASE`` templates
      3. the built-in DataStudio staging defaults
    """
    overrides = _load_overrides().get(_entity_key(bu, platform, category), {})

    upload_base = (
        overrides.get("upload_base")
        or os.getenv("FPS_UPLOAD_SMB_BASE")
        or _DEFAULT_UPLOAD_SMB_BASE
    ).strip()
    if not upload_base.endswith("/"):
        upload_base += "/"

    output_template = (
        overrides.get("save_location")
        or os.getenv("FPS_OUTPUT_BASE")
        or _DEFAULT_OUTPUT_BASE
    ).strip()

    save_location = output_template.format(
        bu=_sanitize_path_segment(bu),
        platform=_sanitize_path_segment(platform),
        category=_sanitize_path_segment(category),
    )

    upload_dir = f"{upload_base}{unique_id}/"
    logger.info(
        "[fps] resolved entity paths bu=%s platform=%s category=%s -> upload_dir=%s save=%s",
        bu, platform, category, upload_dir, save_location,
    )
    return EntityPaths(upload_dir=upload_dir, save_location=save_location)
