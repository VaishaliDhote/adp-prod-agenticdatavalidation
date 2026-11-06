# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - S3 connection/config (task 1). REUSABLE as-is.
# ============================================================================
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import boto3
import certifi
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_REGION = os.getenv("S3_REGION_NAME") or os.getenv("AIGW_REGION_NAME", "eu-west-3")


def _build_combined_ca_bundle(adp_cert: str) -> str:
    """
    Build (and cache) a CA bundle that contains BOTH the public root CAs
    (certifi) AND the internal ADP root CA.

    Presigned S3 PUTs go directly to *.s3.amazonaws.com, which presents a
    genuine Amazon certificate that chains to a public root — not to the ADP
    internal root. Using the ADP bundle alone therefore fails with
    "unable to get local issuer certificate". Concatenating the public roots
    with the ADP root lets verification succeed for both direct-to-AWS and
    ADP-proxied connections.
    """
    out = Path(__file__).resolve().parents[2] / ".certs" / "combined_ca_bundle.pem"
    public = Path(certifi.where()).read_text(encoding="utf-8")
    internal = Path(adp_cert).read_text(encoding="utf-8")
    combined = public.rstrip() + "\n" + internal.lstrip()
    # Only rewrite when the contents change (cheap startup, avoids churn).
    if not out.is_file() or out.read_text(encoding="utf-8") != combined:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(combined, encoding="utf-8")
        logger.info("[S3Client] Wrote combined CA bundle -> %s", out)
    return str(out)


def _resolve_s3_verify() -> str | bool:
    """
    Resolve the ``verify`` value for the boto3 S3 client.

    - ``S3_SSL_VERIFY=False`` -> disable verification (not recommended).
    - ``SSL_CERT_PATH`` set + file exists -> combined public + ADP bundle.
    - otherwise -> certifi public roots (works for direct-to-AWS).
    """
    if os.getenv("S3_SSL_VERIFY", "").lower() == "false":
        logger.warning("[S3Client] TLS verification DISABLED (S3_SSL_VERIFY=False).")
        return False
    adp_cert = os.getenv("SSL_CERT_PATH")
    if adp_cert and Path(adp_cert).is_file():
        return _build_combined_ca_bundle(adp_cert)
    logger.info("[S3Client] SSL_CERT_PATH not found; using certifi public roots.")
    return certifi.where()


_SSL: str | bool = _resolve_s3_verify()


# Allowlist for filenames — strips path traversal risk. Allows parentheses and
# square brackets so names like 'sozialversicherungsdaten(2).csv' are accepted.
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.()\[\] ]{1,255}$")


def sanitize_filename(filename: str) -> str:
    """
    Strip path components and validate filename characters.
    Prevents path traversal and XSS via S3 key construction.
    """
    name = Path(filename).name  # strip any directory prefix
    if not name or not _SAFE_FILENAME_RE.match(name):
        raise ValueError(
            f"Invalid filename: '{filename}'. "
            "Only alphanumeric, underscore, hyphen, dot, parentheses, "
            "square brackets and space are allowed."
        )
    return name


class _S3Client:
    """
    Thread-safe singleton wrapper around boto3 S3 client.
    Eliminates module-level global — safe for concurrent FastAPI requests.
    """

    def __init__(self) -> None:
        self._client = None
        self._lock = __import__("threading").Lock()

    def get(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = boto3.client(
                        "s3",
                        region_name=_REGION,
                        verify=_SSL,
                        config=Config(signature_version="s3v4"),
                    )
                    logger.debug("S3 client initialised (region=%s)", _REGION)
        return self._client


_s3_client = _S3Client()


def get_s3_client():
    return _s3_client.get()
