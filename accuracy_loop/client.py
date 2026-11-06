# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# External backend HTTP client (tasks 1, 2, 5, 6, 7, 8).
#
# Integrates the existing DVTS backend by COMPOSING an execution from the
# available ``/ahub/ds/config/*`` endpoints (there is no single execute
# endpoint). Sends each configuration block, forwards the caller's Bearer JWT,
# parses the standard ``{success, data, message, errorCode}`` envelope, and maps
# failures into row-level rejections so the rest of the loop is transport-agnostic.
# ============================================================================
"""
accuracy_loop.client
====================
Synchronous HTTP client implementing :class:`ExecutionBackend`.

Design notes
------------
* **Synchronous** by design - the loop is CPU/IO-light and the HTTP routes
  offload it with ``run_in_threadpool`` (same pattern as the Deliverable 7
  routes), so we avoid threading an event loop through the controller.
* **Resilient** - transient transport errors are retried
  (:attr:`ExecutionApiConfig.max_retries`); a definitive failure becomes a
  :class:`BlockPushResult` with ``success=False`` rather than an exception, so
  one failing block never aborts the whole push.
* **Row-aware** - when the backend returns a recognisable per-row rejection
  array it is mapped to :class:`RowRejection`; otherwise the whole block is
  reported as a single config-level rejection carrying the backend ``errorCode``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from accuracy_loop.models import (
    BlockPushResult,
    ExecutionApiConfig,
    MismatchKind,
    PushOperation,
    RowRejection,
)

logger = logging.getLogger(__name__)

# Backend rejection kind hints -> our MismatchKind.
_KIND_HINTS: dict[str, MismatchKind] = {
    "validation": MismatchKind.VALIDATION,
    "transformation": MismatchKind.TRANSFORMATION,
    "mapping": MismatchKind.MAPPING,
    "reference": MismatchKind.MAPPING,
    "config": MismatchKind.CONFIG,
}


class ExecutionApiClient:
    """Push composed configuration blocks to the existing backend."""

    def __init__(
        self,
        config: Optional[ExecutionApiConfig] = None,
        *,
        auth_token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._config = config or ExecutionApiConfig.from_env()
        self._auth_token = auth_token
        self._client = client  # injectable for tests

    # -- ExecutionBackend ---------------------------------------------------
    def push(self, operations: list[PushOperation]) -> list[BlockPushResult]:
        """Send every operation sequentially and return one result each."""
        owns_client = self._client is None
        client = self._client or httpx.Client(
            base_url=self._config.base_url or "",
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
        )
        try:
            return [self._send(client, op) for op in operations]
        finally:
            if owns_client:
                client.close()

    # -- internals ----------------------------------------------------------
    def _send(self, client: httpx.Client, op: PushOperation) -> BlockPushResult:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        last_exc: Optional[Exception] = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = client.request(
                    op.method, op.path, params=op.params, json=op.payload, headers=headers,
                )
                return self._from_response(op, response)
            except httpx.HTTPError as exc:  # transport-level failure
                last_exc = exc
                if attempt < self._config.max_retries:
                    time.sleep(min(0.5 * (attempt + 1), 2.0))
                    continue
        return self._transport_failure(op, last_exc)

    def _from_response(self, op: PushOperation, response: httpx.Response) -> BlockPushResult:
        try:
            envelope: dict[str, Any] = response.json()
        except ValueError:
            envelope = {}
        success = bool(envelope.get("success", response.is_success))
        error_code = envelope.get("errorCode")
        message = envelope.get("message") or envelope.get("error")

        if success:
            return BlockPushResult(
                block=op.block, action=op.action, method=op.method, path=op.path,
                success=True, status_code=response.status_code, item_count=op.item_count,
                accepted_count=op.item_count, error_code=None,
                message=message or "accepted", rejections=[],
                raw=self._trim(envelope),
            )

        rejections = self._extract_rejections(op, envelope)
        if not rejections and op.item_count:
            # No per-row detail: attribute the whole block to a config-level fault.
            rejections = [
                RowRejection(
                    index=0, uuid=None, kind=MismatchKind.CONFIG,
                    code=str(error_code or "BLOCK_REJECTED"), field=None,
                    expected=None, actual=None,
                    message=str(message or "backend rejected the block"),
                )
            ]
        return BlockPushResult(
            block=op.block, action=op.action, method=op.method, path=op.path,
            success=False, status_code=response.status_code, item_count=op.item_count,
            accepted_count=max(op.item_count - len(rejections), 0),
            error_code=str(error_code) if error_code else "BLOCK_REJECTED",
            message=str(message) if message else "backend rejected the block",
            rejections=rejections, raw=self._trim(envelope),
        )

    def _extract_rejections(
        self, op: PushOperation, envelope: dict[str, Any]
    ) -> list[RowRejection]:
        """Map any recognisable per-row rejection array from the envelope."""
        data = envelope.get("data")
        candidates: Any = None
        if isinstance(data, dict):
            candidates = data.get("rejections") or data.get("errors") or data.get("failed")
        elif isinstance(data, list):
            candidates = data
        if not isinstance(candidates, list):
            return []

        rejections: list[RowRejection] = []
        for i, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            kind = self._infer_kind(op, item)
            rejections.append(
                RowRejection(
                    index=int(item.get("index", i)),
                    uuid=item.get("uuid") or item.get("sourceColumn") or item.get("sourceColumnName"),
                    kind=kind,
                    code=str(item.get("errorCode") or item.get("code") or "ROW_REJECTED"),
                    field=item.get("field"),
                    expected=_str_or_none(item.get("expected")),
                    actual=_str_or_none(item.get("actual")),
                    message=str(item.get("message") or item.get("error") or "row rejected"),
                )
            )
        return rejections

    @staticmethod
    def _infer_kind(op: PushOperation, item: dict[str, Any]) -> MismatchKind:
        hint = str(item.get("kind") or item.get("type") or "").lower()
        for token, kind in _KIND_HINTS.items():
            if token in hint:
                return kind
        # Fall back to the block's natural kind.
        if op.block.value == "validation-rules":
            return MismatchKind.VALIDATION
        if op.block.value in ("transformation-rules", "dataset-transformation-rules"):
            return MismatchKind.TRANSFORMATION
        return MismatchKind.CONFIG

    def _transport_failure(self, op: PushOperation, exc: Optional[Exception]) -> BlockPushResult:
        logger.warning("[accuracy-loop] transport failure for %s %s: %s", op.method, op.path, exc)
        rej = RowRejection(
            index=0, uuid=None, kind=MismatchKind.CONFIG, code="TRANSPORT_ERROR",
            field=None, expected=None, actual=None,
            message=f"could not reach backend: {exc}",
        )
        return BlockPushResult(
            block=op.block, action=op.action, method=op.method, path=op.path,
            success=False, status_code=0, item_count=op.item_count,
            accepted_count=0, error_code="TRANSPORT_ERROR",
            message=str(exc), rejections=[rej] if op.item_count else [],
            raw={},
        )

    @staticmethod
    def _trim(envelope: dict[str, Any]) -> dict[str, Any]:
        """Keep only the envelope scalars to avoid echoing large payloads."""
        return {
            k: envelope.get(k)
            for k in ("success", "message", "error", "errorCode")
            if k in envelope
        }


def _str_or_none(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = ["ExecutionApiClient"]
