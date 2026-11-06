# ============================================================================
# Deliverable 9 - Reapply modified JSON through the existing API & display output
# (tasks 25, 26).
#
# After a targeted edit is applied and the full contract JSON reassembled, this
# service pushes the (possibly changed) blocks to the existing DVTS backend by
# COMPOSING the ``/ahub/ds/config/*`` calls (there is no single execute
# endpoint), collects per-row rejections, and — when the operator has dropped a
# produced output file — compares it against the known expected output. It reuses
# the Deliverable 8 client/composer/simulator so behaviour is identical to the
# accuracy loop, and degrades gracefully (offline simulator) when no backend URL
# is configured.
# ============================================================================
"""
services.config_reapply_service
===============================
Reapply an edited configuration contract through the existing execution API and
return a structured, display-ready result (push outcome + optional output diff).
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from accuracy_loop.client import ExecutionApiClient
from accuracy_loop.composer import ConfigPushComposer
from accuracy_loop.models import BlockPushResult, ExecutionApiConfig, ExecutionBackend
from accuracy_loop.output_comparison import (
    CompareOptions,
    OutputFileError,
    OutputFileNotFound,
    OutputLocations,
    compare_output_files,
)
from accuracy_loop.simulator import ExecutionSimulator
from contract.models import ConfigurationContract
from knowledge.iteration_store import save_iteration_history

logger = logging.getLogger(__name__)


class ConfigReapplyError(RuntimeError):
    """Raised when the edited configuration cannot be pushed to the backend."""


@dataclass
class ReapplyResult:
    """Structured outcome of a reapply (tasks 25, 26)."""

    run_id: str
    used_simulator: bool
    success: bool
    blocks: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    output_comparison: Optional[dict[str, Any]] = None
    output_comparison_pending: bool = False
    message: str = ""
    persisted: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "used_simulator": self.used_simulator,
            "success": self.success,
            "blocks": self.blocks,
            "rejections": self.rejections,
            "output_comparison": self.output_comparison,
            "output_comparison_pending": self.output_comparison_pending,
            "message": self.message,
        }


class ConfigReapplyService:
    """Push an edited contract through the existing backend and compare output."""

    def __init__(
        self,
        composer: Optional[ConfigPushComposer] = None,
        api_config: Optional[ExecutionApiConfig] = None,
        output_locations: Optional[OutputLocations] = None,
    ) -> None:
        self._api_config = api_config or ExecutionApiConfig.from_env()
        self._composer = composer or ConfigPushComposer(self._api_config)
        self._locations = output_locations or OutputLocations.from_env()

    def reapply(
        self,
        contract: ConfigurationContract,
        *,
        bu: str = "",
        platform: str = "",
        category: str = "",
        session_id: str = "",
        auth_token: Optional[str] = None,
        compare_output: bool = False,
        produced_output_file: Optional[str] = None,
        known_output_file: Optional[str] = None,
        output_sheet: Optional[str] = None,
    ) -> ReapplyResult:
        """Compose + push the contract; optionally compare produced vs known output.

        Never raises for a backend-level rejection: a rejected block is reported
        in the result so the UI can display exactly what the existing API refused.
        Raises :class:`ConfigReapplyError` only for unexpected transport failures
        the client did not already fold into a :class:`BlockPushResult`.
        """
        backend, used_simulator = self._build_backend(auth_token)
        operations = self._composer.compose(contract)
        try:
            results = backend.push(operations)
        except Exception as exc:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[reapply] backend push failed")
            raise ConfigReapplyError(str(exc)) from exc

        blocks = [self._block_payload(r) for r in results]
        rejections = [
            {
                "block": r.block.value,
                "index": rej.index,
                "uuid": rej.uuid,
                "kind": rej.kind.value,
                "code": rej.code,
                "field": rej.field,
                "message": rej.message,
            }
            for r in results
            for rej in r.rejections
        ]
        success = all(r.success for r in results)

        result = ReapplyResult(
            run_id=_uuid.uuid4().hex[:12],
            used_simulator=used_simulator,
            success=success,
            blocks=blocks,
            rejections=rejections,
            message=(
                "Configuration accepted by the execution API."
                if success
                else "The execution API rejected part of the configuration."
            ),
        )

        if compare_output:
            self._attach_output_comparison(
                result, produced_output_file, known_output_file, output_sheet
            )

        result.persisted = self._persist(bu, platform, category, session_id, result)
        return result

    # -- internals ----------------------------------------------------------
    def _attach_output_comparison(
        self,
        result: ReapplyResult,
        produced: Optional[str],
        known: Optional[str],
        sheet: Optional[str],
    ) -> None:
        """Best-effort produced-vs-known diff; pending when files are absent."""
        try:
            produced_path = self._locations.resolve_produced(produced) or self._locations.latest_produced()
            known_path = self._locations.resolve_known(known)
            if produced_path is None or known_path is None:
                result.output_comparison_pending = True
                return
            report, _ = compare_output_files(
                str(produced_path), str(known_path), sheet=sheet, options=CompareOptions()
            )
            result.output_comparison = report.model_dump(mode="json")
        except (OutputFileNotFound, OutputFileError) as exc:
            logger.info("[reapply] output comparison unavailable: %s", exc)
            result.output_comparison_pending = True
        except Exception as exc:  # noqa: BLE001 - comparison must never break reapply
            logger.warning("[reapply] output comparison failed: %s", exc)
            result.output_comparison_pending = True

    def _build_backend(self, auth_token: Optional[str]) -> tuple[ExecutionBackend, bool]:
        if self._api_config.use_simulator:
            logger.info("[reapply] using offline simulator backend")
            return ExecutionSimulator(), True
        logger.info("[reapply] using external backend at %s", self._api_config.base_url)
        return ExecutionApiClient(self._api_config, auth_token=auth_token), False

    def _persist(
        self, bu: str, platform: str, category: str, session_id: str, result: ReapplyResult
    ) -> bool:
        try:
            return save_iteration_history(
                bu=bu or "no-bu",
                platform=platform or "no-platform",
                category=category or "no-category",
                session_id=session_id or "no-session",
                run_id=result.run_id,
                history={
                    "run_id": result.run_id,
                    "kind": "reapply",
                    "used_simulator": result.used_simulator,
                    "success": result.success,
                    "blocks": result.blocks,
                    "rejections": result.rejections,
                    "output_comparison": result.output_comparison,
                },
            )
        except Exception as exc:  # noqa: BLE001 - persistence must never break reapply
            logger.warning("[reapply] could not persist reapply history: %s", exc)
            return False

    @staticmethod
    def _block_payload(r: BlockPushResult) -> dict[str, Any]:
        return {
            "block": r.block.value,
            "success": r.success,
            "status_code": r.status_code,
            "item_count": r.item_count,
            "accepted_count": r.accepted_count,
            "error_code": r.error_code,
            "message": r.message,
        }


__all__ = ["ConfigReapplyService", "ReapplyResult", "ConfigReapplyError"]
