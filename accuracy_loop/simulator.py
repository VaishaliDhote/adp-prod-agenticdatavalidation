# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# In-process deterministic backend simulator (tasks 5-8, offline).
#
# The existing backend exposes no single "execute" endpoint, and a live backend
# is not always reachable from the dev loop / CI. This simulator implements the
# same :class:`~accuracy_loop.models.ExecutionBackend` contract as the real
# :class:`~accuracy_loop.client.ExecutionApiClient` and enforces a small set of
# KNOWLEDGE-GROUNDED backend constraints (drawn from the contract analysis in
# ``documents_plan/contract_analysis``) so the accuracy loop can run, converge
# and be asserted deterministically without any network access.
# ============================================================================
"""
accuracy_loop.simulator
=======================
Deterministic in-process stand-in for the existing DVTS backend.

It reproduces the ``{success, data, message, errorCode}`` envelope and rejects
rows on exactly the discrepancies Deliverable 8 must detect and repair:

* MAPPING       - a rule references a column uuid absent from ``column-relations``
                  (tasks 14, 20).
* VALIDATION    - ``minLengthAllowed`` > ``maxLengthAllowed`` on a validation row,
                  or an out-of-vocabulary ``validationRuleType`` (task 12).
* TRANSFORMATION - a transformation ``targetFileName`` that is neither the
                  ``"<null>"`` sentinel nor a known dataset id, or a
                  dataset-rule ``dataSetId`` that resolves to nothing (task 13).

A fully self-consistent Deliverable 7 configuration passes every constraint
(100% acceptance, one iteration); a config carrying any of the above defects is
rejected row-by-row, which is exactly what the analyzer repairs.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from accuracy_loop.models import (
    BlockPushResult,
    ConfigBlock,
    MismatchKind,
    PushOperation,
    RowRejection,
)

logger = logging.getLogger(__name__)

_NULL_SENTINEL = "<null>"
_VALID_RULE_TYPES = {"client", "CAM"}


class ExecutionSimulator:
    """Deterministic offline implementation of :class:`ExecutionBackend`."""

    def __init__(self, *, enforce_dataset_refs: bool = True) -> None:
        # Whether to reject dataset-rule references that cannot be resolved.
        self._enforce_dataset_refs = enforce_dataset_refs

    # -- ExecutionBackend ---------------------------------------------------
    def push(self, operations: list[PushOperation]) -> list[BlockPushResult]:
        """Evaluate every operation in order and return one result each."""
        known_columns: set[str] = set()
        known_datasets: set[str] = set()
        results: list[BlockPushResult] = []

        for op in operations:
            if op.block is ConfigBlock.COLUMN_RELATIONS:
                known_columns = self._collect_column_uuids(op.payload)
                results.append(self._accept_all(op))
            elif op.block is ConfigBlock.CONFIGURATION_DATA:
                known_datasets = self._collect_dataset_ids(op.payload)
                results.append(self._accept_all(op))
            elif op.block is ConfigBlock.VALIDATION_RULES:
                results.append(self._evaluate_validation(op, known_columns))
            elif op.block is ConfigBlock.TRANSFORMATION_RULES:
                results.append(self._evaluate_transformation(op, known_columns, known_datasets))
            elif op.block is ConfigBlock.DATASET_TRANSFORMATION_RULES:
                results.append(self._evaluate_dataset(op, known_datasets))
            else:  # pragma: no cover - exhaustive
                results.append(self._accept_all(op))
        return results

    # -- per-block evaluators ----------------------------------------------
    def _evaluate_validation(
        self, op: PushOperation, known_columns: set[str]
    ) -> BlockPushResult:
        rejections: list[RowRejection] = []
        for idx, row in enumerate(op.payload or []):
            uuid = row.get("sourceColumn")
            if uuid and uuid not in known_columns:
                rejections.append(
                    RowRejection(
                        index=idx, uuid=uuid, kind=MismatchKind.MAPPING,
                        code="REF_UNKNOWN_COLUMN", field="sourceColumn",
                        expected="a uuid present in column-relations", actual=str(uuid),
                        message="validation-rule references an unknown column uuid",
                    )
                )
                continue
            rule_type = row.get("validationRuleType")
            if rule_type is not None and rule_type not in _VALID_RULE_TYPES:
                rejections.append(
                    RowRejection(
                        index=idx, uuid=uuid, kind=MismatchKind.CONFIG,
                        code="RULE_TYPE_INVALID", field="validationRuleType",
                        expected="client|CAM", actual=str(rule_type),
                        message="validation-rule has an unsupported validationRuleType",
                    )
                )
                continue
            length_reject = self._check_length_range(idx, uuid, row)
            if length_reject is not None:
                rejections.append(length_reject)
        return self._result(op, rejections)

    def _evaluate_transformation(
        self, op: PushOperation, known_columns: set[str], known_datasets: set[str]
    ) -> BlockPushResult:
        rejections: list[RowRejection] = []
        for idx, row in enumerate(op.payload or []):
            uuid = row.get("sourceColumnName")
            if uuid and uuid not in known_columns:
                rejections.append(
                    RowRejection(
                        index=idx, uuid=uuid, kind=MismatchKind.MAPPING,
                        code="REF_UNKNOWN_COLUMN", field="sourceColumnName",
                        expected="a uuid present in column-relations", actual=str(uuid),
                        message="transformation-rule references an unknown source column uuid",
                    )
                )
                continue
            target = row.get("targetFileName")
            if target not in (None, _NULL_SENTINEL) and target not in known_datasets:
                rejections.append(
                    RowRejection(
                        index=idx, uuid=uuid, kind=MismatchKind.TRANSFORMATION,
                        code="TARGET_FILE_UNRESOLVED", field="targetFileName",
                        expected=f"'{_NULL_SENTINEL}' or a known dataset id", actual=str(target),
                        message="transformation-rule targetFileName does not resolve",
                    )
                )
        return self._result(op, rejections)

    def _evaluate_dataset(
        self, op: PushOperation, known_datasets: set[str]
    ) -> BlockPushResult:
        rejections: list[RowRejection] = []
        if self._enforce_dataset_refs:
            for idx, row in enumerate(op.payload or []):
                dataset_id = row.get("dataSetId")
                if dataset_id and dataset_id not in known_datasets:
                    rejections.append(
                        RowRejection(
                            index=idx, uuid=row.get("fieldUUID"), kind=MismatchKind.TRANSFORMATION,
                            code="DATASET_UNRESOLVED", field="dataSetId",
                            expected="a dataset id declared in configuration-data",
                            actual=str(dataset_id),
                            message="dataset-transformation-rule dataSetId does not resolve",
                        )
                    )
        return self._result(op, rejections)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _check_length_range(idx: int, uuid: Any, row: dict[str, Any]) -> RowRejection | None:
        raw_min = row.get("minLengthAllowed")
        raw_max = row.get("maxLengthAllowed")
        if raw_min is None or raw_max is None:
            return None
        try:
            lo, hi = int(raw_min), int(raw_max)
        except (TypeError, ValueError):
            return None
        if lo > hi:
            return RowRejection(
                index=idx, uuid=uuid, kind=MismatchKind.VALIDATION,
                code="LENGTH_RANGE_INVALID", field="maxLengthAllowed",
                expected=f"maxLengthAllowed >= minLengthAllowed ({lo})", actual=str(hi),
                message="validation-rule minLengthAllowed exceeds maxLengthAllowed",
            )
        return None

    @staticmethod
    def _collect_column_uuids(payload: Any) -> set[str]:
        uuids: set[str] = set()
        for row in payload or []:
            uuid = row.get("uuid")
            if uuid:
                uuids.add(uuid)
        return uuids

    @staticmethod
    def _collect_dataset_ids(payload: Any) -> set[str]:
        ids: set[str] = set()
        if not isinstance(payload, dict):
            return ids
        for key in ("inputRules", "dataRules", "outputFileRules"):
            blob = payload.get(key)
            for entry in ExecutionSimulator._loads_array(blob):
                if isinstance(entry, dict):
                    ds = entry.get("dataSetId")
                    if ds:
                        ids.add(ds)
        return ids

    @staticmethod
    def _loads_array(blob: Any) -> list[Any]:
        if blob is None:
            return []
        if isinstance(blob, list):
            return blob
        if isinstance(blob, str) and blob.strip():
            try:
                parsed = json.loads(blob)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return []

    @staticmethod
    def _accept_all(op: PushOperation) -> BlockPushResult:
        return BlockPushResult(
            block=op.block, action=op.action, method=op.method, path=op.path,
            success=True, status_code=200, item_count=op.item_count,
            accepted_count=op.item_count, error_code=None,
            message="accepted", rejections=[],
            raw={"success": True, "data": {"accepted": op.item_count}},
        )

    @staticmethod
    def _result(op: PushOperation, rejections: list[RowRejection]) -> BlockPushResult:
        success = not rejections
        accepted = op.item_count - len(rejections)
        return BlockPushResult(
            block=op.block, action=op.action, method=op.method, path=op.path,
            success=success, status_code=200 if success else 422,
            item_count=op.item_count, accepted_count=max(accepted, 0),
            error_code=None if success else rejections[0].code,
            message="accepted" if success else f"{len(rejections)} row(s) rejected",
            rejections=rejections,
            raw={
                "success": success,
                "data": {"accepted": max(accepted, 0), "rejected": len(rejections)},
            },
        )


__all__ = ["ExecutionSimulator"]
