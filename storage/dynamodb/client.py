# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - DynamoDB connection (metadata/version/audit backing). REUSABLE as-is.
# ============================================================================
from __future__ import annotations
import logging
import os
import re
import warnings
import boto3
import urllib3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
_REGION        = os.getenv("AIGW_REGION_NAME", "eu-west-3")
_DYNAMO_VERIFY = os.getenv("DYNAMO_DB_VERIFY", "False").lower() == "true"
_DYNAMO_CONNECT_TIMEOUT = float(os.getenv("DYNAMO_DB_CONNECT_TIMEOUT", "5"))
_DYNAMO_READ_TIMEOUT = float(os.getenv("DYNAMO_DB_READ_TIMEOUT", "10"))
_DYNAMO_MAX_ATTEMPTS = int(os.getenv("DYNAMO_DB_MAX_ATTEMPTS", "2"))

_DYNAMO_CONFIG = Config(
    connect_timeout=_DYNAMO_CONNECT_TIMEOUT,
    read_timeout=_DYNAMO_READ_TIMEOUT,
    retries={"max_attempts": _DYNAMO_MAX_ATTEMPTS, "mode": "standard"},
    tcp_keepalive=True,
)

# Suppress urllib3 InsecureRequestWarning when certificate verification is
# intentionally disabled (e.g. corporate proxy with self-signed cert).
# Only suppress when DYNAMO_DB_VERIFY is explicitly False.
if not _DYNAMO_VERIFY:
    warnings.filterwarnings(
        "ignore",
        category=urllib3.exceptions.InsecureRequestWarning,
    )
    logger.warning(
        "[DynamoDBClient] Certificate verification is DISABLED (DYNAMO_DB_VERIFY=False). "
        "Set DYNAMO_DB_VERIFY=True in production."
    )

# Allowlist: alphanumeric, underscore, hyphen, dot, @, space — max 128 chars
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-\.@ ]{1,128}$")

def sanitize_key(value: str, field: str = "value") -> str:
    """
    Validate that a user-supplied key component contains only safe characters.
    Raises ValueError on invalid input — prevents NoSQL injection via key interpolation.
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"Invalid {field}: must be a non-empty string")
    if not _SAFE_KEY_RE.match(value):
        raise ValueError(
            f"Invalid {field}: '{value}' contains disallowed characters. "
            "Only alphanumeric, underscore, hyphen, dot, @ and space are allowed."
        )
    return value.strip()

class _DynamoDBClient:
    """
    Thread-safe singleton wrapper around boto3 DynamoDB resource.
    Encapsulates connection and table cache — eliminates module-level globals.
    """

    def __init__(self) -> None:
        self._resource = None
        self._table_cache: dict = {}

    def get_resource(self):
        if self._resource is None:
            logger.info(
                "[DynamoDBClient] Initialising boto3 DynamoDB resource region=%s verify=%s",
                _REGION, _DYNAMO_VERIFY,
            )
            self._resource = boto3.resource(
                "dynamodb",
                region_name=_REGION,
                verify=_DYNAMO_VERIFY,
                config=_DYNAMO_CONFIG,
            )
            logger.info("[DynamoDBClient] boto3 DynamoDB resource ready region=%s", _REGION)
        return self._resource

    def get_table(self, table_name: str):
        if table_name not in self._table_cache:
            logger.info("[DynamoDBClient] Caching table reference table=%s", table_name)
            self._table_cache[table_name] = self.get_resource().Table(table_name)
            logger.info("[DynamoDBClient] Table reference cached table=%s", table_name)
        return self._table_cache[table_name]

_dynamo_client = _DynamoDBClient()
def get_table(table_name: str):
    return _dynamo_client.get_table(table_name)
