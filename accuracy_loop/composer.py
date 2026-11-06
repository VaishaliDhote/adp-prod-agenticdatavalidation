# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Config -> backend push composer (tasks 3, 4).
#
# There is no single "execute" endpoint in the existing backend; an execution is
# COMPOSED by pushing each of the five configuration blocks to its matching
# ``/ahub/ds/config/*`` endpoint (from the DVTS V2 API reference). This module
# turns one exact :class:`~contract.models.ConfigurationContract` into the ordered
# list of :class:`~accuracy_loop.models.PushOperation` calls that realise it.
# ============================================================================
"""
accuracy_loop.composer
======================
Translate an exact ``ConfigurationContract`` into ordered push operations.

Ordering matters: ``column-relations`` is pushed first so that the backend (and
the offline simulator) can resolve the column uuids that the rule blocks
reference (tasks 19, 20 mapping validation). The identity triple is attached as
query parameters on every call, exactly as the ``/by`` config endpoints expect.
"""
from __future__ import annotations

from typing import Any

from accuracy_loop.models import (
    ConfigBlock,
    ExecutionApiConfig,
    PushOperation,
)
from contract.models import ConfigurationContract

# Deterministic push order (dependencies first).
_PUSH_ORDER: tuple[ConfigBlock, ...] = (
    ConfigBlock.COLUMN_RELATIONS,
    ConfigBlock.CONFIGURATION_DATA,
    ConfigBlock.DATASET_TRANSFORMATION_RULES,
    ConfigBlock.TRANSFORMATION_RULES,
    ConfigBlock.VALIDATION_RULES,
)


class ConfigPushComposer:
    """Compose the ordered backend calls for a complete configuration."""

    def __init__(self, config: ExecutionApiConfig | None = None) -> None:
        self._config = config or ExecutionApiConfig()

    def compose(self, contract: ConfigurationContract) -> list[PushOperation]:
        """Return one :class:`PushOperation` per block, in dependency order.

        Args:
            contract: The exact Deliverable 7 configuration contract.

        Returns:
            Ordered push operations (``column-relations`` first).
        """
        document = contract.to_contract_dict()
        data: dict[str, Any] = document.get("data", {})
        config_data: dict[str, Any] = data.get(ConfigBlock.CONFIGURATION_DATA.value, {}) or {}
        params = self._identity_params(config_data)

        operations: list[PushOperation] = []
        for block in _PUSH_ORDER:
            if block is ConfigBlock.CONFIGURATION_DATA:
                payload: Any = config_data
                item_count = 1 if config_data else 0
            else:
                rows = data.get(block.value, []) or []
                payload = rows
                item_count = len(rows)
            operations.append(
                PushOperation(
                    block=block,
                    action=block.value,
                    method=self._config.methods.get(block.value, "POST"),
                    path=self._config.endpoints.get(block.value, f"/ahub/ds/config/{block.value}"),
                    params=params,
                    payload=payload,
                    item_count=item_count,
                )
            )
        return operations

    @staticmethod
    def _identity_params(config_data: dict[str, Any]) -> dict[str, str]:
        """Extract the ``bu``/``platform``/``dataCategory`` query params."""
        return {
            "bu": str(config_data.get("bu", "")),
            "platform": str(config_data.get("platform", "")),
            "dataCategory": str(config_data.get("dataCategory", "")),
        }


__all__ = ["ConfigPushComposer"]
