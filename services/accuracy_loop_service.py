# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop service.
# Thin orchestration over Deliverable 7 (complete-config generation/assembly) and
# the Deliverable 8 accuracy-loop package. Given a canonical model (or a session
# to ingest), it generates the initial complete configuration, runs the
# execute -> compare -> analyze -> correct -> regenerate loop against the existing
# backend (real client or offline simulator), and preserves the run history
# (Sprint 8 tasks 1-25). Consumed by the /accuracy-loop HTTP routes and by tests.
# ============================================================================
"""
services.accuracy_loop_service
==============================
Orchestrates the Deliverable 8 development-accuracy loop.

Framework-agnostic, mirroring :class:`services.complete_config_service.CompleteConfigService`:

* :meth:`run` - canonical model -> initial complete config -> full accuracy loop.
* :meth:`run_from_session` - ingest a session first, then :meth:`run`.

Failures are normalized to :class:`AccuracyLoopError` so the API layer can map
them to the unified error contract.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Optional

from accuracy_loop.accuracy import compute_accuracy
from accuracy_loop.client import ExecutionApiClient
from accuracy_loop.loop import AccuracyLoopController, LoopOutcome
from accuracy_loop.models import (
    AccuracyLoopContext,
    ExecutionApiConfig,
    ExecutionBackend,
    OutputComparisonReport,
)
from accuracy_loop.output_comparison import (
    CompareOptions,
    OutputFileError,
    OutputFileNotFound,
    OutputLocations,
    compare_output_files,
)
from accuracy_loop.simulator import ExecutionSimulator
from canonical.models import CanonicalAnalysisModel
from complete_config.models import CompleteConfigContext
from ingestion.ingestion_service import IngestionService
from knowledge.iteration_store import save_iteration_history
from services.complete_config_service import (
    CompleteConfigError,
    CompleteConfigService,
)

logger = logging.getLogger(__name__)


class AccuracyLoopError(RuntimeError):
    """Raised when the accuracy loop cannot run or complete."""


class AccuracyLoopResult:
    """Bundle produced by :meth:`AccuracyLoopService.run`.

    Attributes:
        outcome: The :class:`~accuracy_loop.loop.LoopOutcome` (final config +
            per-iteration history + final accuracy + stop reason).
        run_id: Deterministic-per-call identifier for this loop run.
        session_id: The session the loop ran for (may be empty for model-only).
        used_simulator: Whether the offline simulator backed the execution.
        persisted: Whether the history reached DynamoDB (vs local-cache only).
    """

    __slots__ = ("outcome", "run_id", "session_id", "used_simulator", "persisted")

    def __init__(
        self,
        outcome: LoopOutcome,
        run_id: str,
        session_id: str,
        used_simulator: bool,
        persisted: bool,
    ) -> None:
        self.outcome = outcome
        self.run_id = run_id
        self.session_id = session_id
        self.used_simulator = used_simulator
        self.persisted = persisted

    # -- serialisation -----------------------------------------------------
    def history_payload(self) -> dict[str, Any]:
        """JSON-serialisable run history (task 25) for persistence + response."""
        outcome = self.outcome
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "used_simulator": self.used_simulator,
            "final_accuracy": outcome.final_accuracy.accuracy if outcome.final_accuracy else None,
            "stop_reason": outcome.stop_reason.value if outcome.stop_reason else None,
            "iteration_count": len(outcome.iterations),
            "accuracy_report": (
                outcome.final_accuracy.model_dump(mode="json") if outcome.final_accuracy else None
            ),
            "iterations": [it.model_dump(mode="json") for it in outcome.iterations],
        }


class AccuracyLoopService:
    """Run the Deliverable 8 execution + development accuracy loop."""

    def __init__(
        self,
        complete_config_service: Optional[CompleteConfigService] = None,
        controller: Optional[AccuracyLoopController] = None,
        ingestion_service: Optional[IngestionService] = None,
        api_config: Optional[ExecutionApiConfig] = None,
        output_locations: Optional[OutputLocations] = None,
    ) -> None:
        self._complete = complete_config_service or CompleteConfigService()
        self._controller = controller or AccuracyLoopController(self._complete)
        self._ingestion = ingestion_service or IngestionService()
        self._api_config = api_config
        self._locations = output_locations or OutputLocations.from_env()

    # -- output locations (manual produced-output workflow, tasks 9-11) -----
    @property
    def output_locations(self) -> OutputLocations:
        """The network share + local drop/expected folders for output files."""
        return self._locations

    # -- full run from a prebuilt canonical model --------------------------
    def run(
        self,
        model: CanonicalAnalysisModel,
        context: Optional[AccuracyLoopContext] = None,
        *,
        auth_token: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AccuracyLoopResult:
        """Generate the initial config then run the accuracy loop (tasks 1-25)."""
        loop_ctx = context or AccuracyLoopContext()
        cc_ctx = loop_ctx.complete_config_context()

        rows = self._initial_rows(model, cc_ctx)
        if loop_ctx.demo_defects:
            rows = self._seed_defects(rows)

        backend, used_simulator = self._build_backend(auth_token)
        try:
            outcome = self._controller.run(model, cc_ctx, loop_ctx, rows, backend)
        except CompleteConfigError as e:
            raise AccuracyLoopError(f"regeneration failed during loop: {e}") from e
        except Exception as e:  # noqa: BLE001 - normalize to a domain error
            logger.exception("[accuracy-loop] loop execution failed")
            raise AccuracyLoopError(str(e)) from e

        # 9-11) fold the produced-vs-known output diff into the final accuracy
        # when the operator has dropped a produced output file and requested it.
        if loop_ctx.compare_output:
            self._fold_output_comparison(loop_ctx, outcome)

        sid = session_id or getattr(model, "session_id", "") or ""
        run_id = _uuid.uuid4().hex[:12]
        result = AccuracyLoopResult(
            outcome=outcome,
            run_id=run_id,
            session_id=sid,
            used_simulator=used_simulator,
            persisted=False,
        )
        result.persisted = self._persist(loop_ctx, result)
        return result

    def run_from_session(
        self,
        session_id: str,
        context: Optional[AccuracyLoopContext] = None,
        *,
        auth_token: Optional[str] = None,
    ) -> AccuracyLoopResult:
        """Ingest a session, then run the full accuracy loop end-to-end."""
        model = self._ingest(session_id)
        model.session_id = model.session_id or session_id
        return self.run(model, context, auth_token=auth_token, session_id=session_id)

    # -- internals ----------------------------------------------------------
    def compare_output(
        self,
        produced: Optional[str] = None,
        known: Optional[str] = None,
        *,
        sheet: Optional[str] = None,
        options: Optional[CompareOptions] = None,
    ) -> OutputComparisonReport:
        """Diff a produced output file against the known output (tasks 9-11).

        ``produced``/``known`` may be a bare filename (resolved inside the drop /
        expected folders) or an explicit path. When ``produced`` is omitted, the
        newest file in the drop folder is used. Raises :class:`AccuracyLoopError`
        (mapped to the unified error contract by the route) when a file is
        missing or cannot be read.
        """
        produced_path = self._locations.resolve_produced(produced)
        if produced_path is None:
            produced_path = self._locations.latest_produced()
        if produced_path is None:
            raise AccuracyLoopError(
                "No produced output file found. Download the output from the network "
                f"share and paste it into '{self._locations.drop_dir}'."
            )
        known_path = self._locations.resolve_known(known)
        if known_path is None:
            raise AccuracyLoopError(
                "No known/expected output file found. Place the expected output in "
                f"'{self._locations.expected_dir}' or pass an explicit path."
            )
        try:
            report, _ = compare_output_files(
                produced_path, known_path, sheet=sheet, options=options
            )
        except (OutputFileNotFound, OutputFileError) as e:
            raise AccuracyLoopError(str(e)) from e
        return report

    def _fold_output_comparison(
        self, loop_ctx: AccuracyLoopContext, outcome: LoopOutcome
    ) -> None:
        """Recompute the final accuracy with the produced-vs-known output diff.

        Best-effort: if the operator has not yet dropped a produced file (or a
        file cannot be read), the loop's row-acceptance accuracy is preserved and
        the comparison stays pending rather than failing the run.
        """
        if not outcome.iterations:
            return
        try:
            report = self.compare_output(
                produced=loop_ctx.produced_output_file,
                known=loop_ctx.known_output_file,
                sheet=loop_ctx.output_sheet,
            )
        except AccuracyLoopError as exc:
            logger.warning("[accuracy-loop] output comparison skipped: %s", exc)
            return
        last = outcome.iterations[-1]
        folded = compute_accuracy(last.push_results, output_report=report)
        last.accuracy = folded
        outcome.final_accuracy = folded
        logger.info(
            "[accuracy-loop] folded output diff: output_accuracy=%.4f overall=%.4f",
            report.output_accuracy, folded.accuracy,
        )

    def _initial_rows(
        self, model: CanonicalAnalysisModel, cc_ctx: CompleteConfigContext
    ) -> dict[str, list[dict]]:
        """Generate Deliverable 4 & 6 blocks as the loop's initial rows."""
        try:
            gen = self._complete.generate(model, cc_ctx)
        except CompleteConfigError as e:
            raise AccuracyLoopError(f"initial configuration generation failed: {e}") from e
        return {
            "validation": list(gen.validation.rows) if gen.validation else [],
            "transformation": list(gen.transformation.rows) if gen.transformation else [],
            "dataset": list(gen.transformation.dataset_rows) if gen.transformation else [],
        }

    def _build_backend(self, auth_token: Optional[str]) -> tuple[ExecutionBackend, bool]:
        config = self._api_config or ExecutionApiConfig.from_env()
        if config.use_simulator:
            logger.info("[accuracy-loop] using offline simulator backend")
            return ExecutionSimulator(), True
        logger.info("[accuracy-loop] using external backend at %s", config.base_url)
        return ExecutionApiClient(config, auth_token=auth_token), False

    def _persist(self, loop_ctx: AccuracyLoopContext, result: AccuracyLoopResult) -> bool:
        try:
            return save_iteration_history(
                bu=loop_ctx.bu,
                platform=loop_ctx.platform,
                category=loop_ctx.data_category,
                session_id=result.session_id or "no-session",
                run_id=result.run_id,
                history=result.history_payload(),
            )
        except Exception as exc:  # noqa: BLE001 - persistence must never break the run
            logger.warning("[accuracy-loop] could not persist iteration history: %s", exc)
            return False

    def _ingest(self, session_id: str) -> CanonicalAnalysisModel:
        return self._ingestion.ingest_session(session_id)

    @staticmethod
    def _seed_defects(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Verification affordance: inject fixable defects into the first rows.

        Deterministically corrupts the first validation row (inverted length
        range -> VALIDATION mismatch) and the first transformation row
        (unresolved targetFileName -> TRANSFORMATION mismatch) so the loop
        demonstrably converges over multiple iterations. Never used in production.
        """
        validation = [dict(r) for r in rows["validation"]]
        transformation = [dict(r) for r in rows["transformation"]]
        if validation:
            validation[0]["minLengthAllowed"] = "10"
            validation[0]["maxLengthAllowed"] = "5"
        if transformation:
            transformation[0]["targetFileName"] = "DEMO_UNRESOLVED_FILE"
        return {
            "validation": validation,
            "transformation": transformation,
            "dataset": rows["dataset"],
        }


__all__ = ["AccuracyLoopService", "AccuracyLoopError", "AccuracyLoopResult"]
