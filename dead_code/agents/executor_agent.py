"""
agents/phase_executor_agent.py
================================
PhaseExecutorAgent
------------------
Runs all 11 execution phases in the correct order and returns the
transformed output DataFrame together with rich per-phase diagnostics.

Execution order
---------------
  Phase 0  — Setup          load + normalise + basic dtype + drop empties
  Phase 1  — Cleaning       trim / find-replace / type conv / missing values
  Phase 2  — Structure      transpose / pivot / unpivot / hierarchy
  Phase 3  — Columns        rename → split → merge → select → reorder
  Phase 4  — Rows           filter → dedup → sort → subtotals
  Phase 5  — Enrichment     lookup → calculated → conditional → text/date/math
  Phase 6  — Aggregation    groupby → cond-agg → running total → ranking
  Phase 7  — Advanced       array formulas → indirect refs
  Phase 8  — Finalize       formula→value, remove intermediate columns
  Phase 9  — Formatting     number fmt / cond fmt / data validation / charts
  Phase 10 — Save           write xlsx / csv / parquet / json …

The agent can be driven in three ways:

  A. session_id   — resolves input/output paths and config via
                    GetConfigurationService (auto-detect).
  B. config dict  — callers supply a pre-built config dict plus explicit
                    input_path / output_path.
  C. config YAML  — callers supply a YAML file path plus explicit
                    input_path / output_path.

Usage
-----
    from agents.phase_executor_agent import PhaseExecutorAgent

    agent = PhaseExecutorAgent()

    # ── Option A: session-based (loads saved corrected config from generated_configs/) ──
    result = agent.run_from_session("160cf6ce-bb3b-4d0d-9d45-18645e791e2f")

    # ── Option B: explicit paths + pre-built config dict ──────────────────────
    result = agent.run(
        input_path  = "data/input_files/abc/input.xlsx",
        output_path = "data/executed_outputs/abc/output.xlsx",
        config      = my_config_dict,
    )

    # ── Option C: explicit paths + YAML config file ───────────────────────────
    result = agent.run(
        input_path  = "data/input_files/abc/input.xlsx",
        output_path = "data/executed_outputs/abc/output.xlsx",
        config_path = "data/generated_configs/abc_config.yaml",
    )

    print(result.summary())          # human-readable report
    print(result.output_df)          # final transformed DataFrame
    print(result.output_path)        # absolute path of saved file
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

# ── Execution phase runners ───────────────────────────────────────────────────
from execution_phases.execution_phase_0_setup       import run as _phase0
from execution_phases.execution_phase_1_cleaning    import run as _phase1
from execution_phases.execution_phase_2_structure   import run as _phase2
from execution_phases.execution_phase_3_columns     import run as _phase3
from execution_phases.execution_phase_4_rows        import run as _phase4
from execution_phases.execution_phase_5_enrichment  import run as _phase5
from execution_phases.execution_phase_6_aggregation import run as _phase6
from execution_phases.execution_phase_7_advanced    import run as _phase7
from execution_phases.execution_phase_8_finalize    import run as _phase8
from execution_phases.execution_phase_9_formatting  import run as _phase9
from execution_phases.execution_phase_10_save       import run as _phase10
from execution_phases.execution_pipeline             import normalize_config_scalars


# ── Configuration service ─────────────────────────────────────────────────────
from services.get_configuration import (
    GetConfigurationService,
    ConfigurationResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_ROOT              = Path(__file__).resolve().parent.parent
_EXECUTED_OUT_DIR  = _ROOT / "data" / "executed_outputs"
_GENERATED_CFG_DIR = _ROOT / "data" / "generated_configs"

# Ordered list of (display-name, phase-number) for all 11 phases
_PHASE_REGISTRY: List[Tuple[str, int]] = [
    ("Phase 0  — Setup",        0),
    ("Phase 1  — Cleaning",     1),
    ("Phase 2  — Structure",    2),
    ("Phase 3  — Columns",      3),
    ("Phase 4  — Rows",         4),
    ("Phase 5  — Enrichment",   5),
    ("Phase 6  — Aggregation",  6),
    ("Phase 7  — Advanced",     7),
    ("Phase 8  — Finalize",     8),
    ("Phase 9  — Formatting",   9),
    ("Phase 10 — Save",        10),
]


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PhaseStepResult:
    """Per-phase execution record."""

    phase_name:  str
    phase_num:   int
    status:      str                  # "ok" | "skipped" | "error"
    rows_in:     int     = 0
    cols_in:     int     = 0
    rows_out:    int     = 0
    cols_out:    int     = 0
    duration_s:  float   = 0.0
    error:       Optional[str] = None
    notes:       str     = ""         # e.g. "skipped — no config block"

    @property
    def shape_changed(self) -> bool:
        return (self.rows_in, self.cols_in) != (self.rows_out, self.cols_out)

    def one_line(self) -> str:
        badge = {"ok": "✅", "skipped": "⏭️ ", "error": "❌"}.get(self.status, "❓")
        shape = f"{self.rows_out}r × {self.cols_out}c"
        line  = f"  {badge} {self.phase_name:<35} {self.duration_s:>5.2f}s  [{shape}]"
        if self.error:
            line += f"  ← {self.error}"
        elif self.notes:
            line += f"  ({self.notes})"
        return line


@dataclass
class ExecutorResult:
    """
    Full result returned by PhaseExecutorAgent.run() /
    PhaseExecutorAgent.run_from_session().
    """

    success:      bool
    session_id:   Optional[str]
    input_path:   str
    output_path:  Optional[str]
    sheet_name:   str
    output_df:    Optional[pd.DataFrame]
    phase_steps:  List[PhaseStepResult] = field(default_factory=list)
    total_s:      float = 0.0
    generated_at: str   = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    error:        Optional[str] = None

    # ── Convenience ──────────────────────────────────────────────────────────

    @property
    def output_shape(self) -> Tuple[int, int]:
        return self.output_df.shape if self.output_df is not None else (0, 0)

    @property
    def failed_phases(self) -> List[PhaseStepResult]:
        return [p for p in self.phase_steps if p.status == "error"]

    @property
    def skipped_phases(self) -> List[PhaseStepResult]:
        return [p for p in self.phase_steps if p.status == "skipped"]

    def summary(self) -> str:
        badge = "✅ SUCCESS" if self.success else "❌ FAILED"
        lines = [
            "=" * 68,
            f"  PhaseExecutorAgent  —  {badge}",
            f"  Session    : {self.session_id or 'N/A'}",
            f"  Input      : {self.input_path}",
            f"  Output     : {self.output_path or 'N/A'}",
            f"  Sheet      : {self.sheet_name}",
            f"  Final shape: {self.output_shape[0]}r × {self.output_shape[1]}c",
            f"  Total time : {self.total_s:.2f}s",
            "-" * 68,
        ]
        for step in self.phase_steps:
            lines.append(step.one_line())
        if self.error:
            lines.append("")
            lines.append(f"  FATAL: {self.error}")
        lines.append("=" * 68)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

class PhaseExecutorAgent:
    """
    Orchestrates execution phases 0-10 in order and produces a transformed
    output file and DataFrame.

    Args:
        config_service  : Pre-built GetConfigurationService.  When None a
                          default instance is created (used only by
                          run_from_session).
        stop_on_error   : Abort the pipeline on the first phase error when
                          True.  When False, log the error and continue
                          with the current DataFrame.
        executed_out_dir: Root directory for saved output files.
                          Defaults to  data/executed_outputs/.
    """

    def __init__(
        self,
        config_service:   Optional[GetConfigurationService] = None,
        stop_on_error:    bool = False,
        executed_out_dir: Optional[str] = None,
    ) -> None:
        self._svc              = config_service or GetConfigurationService()
        self._stop_on_error    = stop_on_error
        self._out_root         = (
            Path(executed_out_dir) if executed_out_dir else _EXECUTED_OUT_DIR
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — option A: session-based
    # ─────────────────────────────────────────────────────────────────────────

    def run_from_session(
        self,
        session_id: str,
        execution_id: str,
        input_path: str,
    ) -> ExecutorResult:
        """
        Run the full execution pipeline for a session.

        Args:
            session_id   : Session whose files live under data/input_files/<session_id>/.
            execution_id : ID for the output folder.  Falls back to session_id when not provided.
            input_path   : Path to the input file.

        Returns:
            ExecutorResult
        """
        print("\n" + "=" * 68)
        print("  🤖  PhaseExecutorAgent — run_from_session")
        print(f"      Session      : {session_id}")
        print(f"      Execution ID : {execution_id}")
        print("=" * 68 + "\n")

        # ── Step 1: Locate the saved config YAML ──────────────────────────
        config_path = _GENERATED_CFG_DIR / f"{session_id}_full_state.yaml"
        if config_path.exists():
            config_yaml_path = config_path
            print(f"[Agent] Config   : {config_yaml_path} (found full_state.yaml)")
        else:
            error_msg = (
                f"No saved config found for session '{session_id}'. "
                f"Run the transformation pipeline first."
            )
            print(f"[Agent] ❌ {error_msg}")
            return ExecutorResult(
                success     = False,
                session_id  = session_id,
                input_path  = "",
                output_path = None,
                sheet_name  = "Sheet1",
                output_df   = None,
                error       = error_msg,
            )

        config = self._load_yaml(str(config_yaml_path))
        # ── Extract corrected_config from full_state YAML ─────────────────
        if "phase_transformation_result" in config:
            corrected_config = config.get("phase_transformation_result", {}).get("corrected_config")
            if not corrected_config:
                error_msg = "full_state.yaml found but 'phase_transformation_result.corrected_config' is missing or empty."
                print(f"[Agent] ❌ {error_msg}")
                return ExecutorResult(
                    success     = False,
                    session_id  = session_id,
                    input_path  = "",
                    output_path = None,
                    sheet_name  = "Sheet1",
                    output_df   = None,
                    error       = error_msg,
                )
            print("[Agent] ✅ Extracted corrected_config from phase_transformation_result")
            config = corrected_config

        # ── Step 2: Extract file name and sheet name from input_path ─────
        file_name = Path(input_path).name
        input_suffix = Path(input_path).suffix or ".xlsx"
        # Default to first sheet for Excel, or None for CSV
        if input_suffix.lower() in [".xlsx", ".xls"]:
            try:
                xl = pd.ExcelFile(input_path)
                sheet_name = xl.sheet_names[0] if xl.sheet_names else "Sheet1"
            except Exception as exc:
                print(f"[Agent] ⚠️ Could not read Excel file to get sheet name: {exc}")
                sheet_name = "Sheet1"
        else:
            sheet_name = None

        out_dir      = self._out_root / execution_id
        output_path  = str(out_dir / f"output{input_suffix}")

        print(f"[Agent] Input    : {input_path}")
        print(f"[Agent] Output   : {output_path}")
        print(f"[Agent] File     : {file_name}")
        print(f"[Agent] Sheet    : {sheet_name}\n")

        # ── Step 3: Execute phases ────────────────────────────────────────
        return self.run(
            input_path  = input_path,
            output_path = output_path,
            config      = config,
            sheet_name  = sheet_name,
            session_id  = session_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — options B & C: explicit paths
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        input_path:  str,
        output_path: str,
        config:      Optional[Dict[str, Any]] = None,
        config_path: Optional[str]            = None,
        sheet_name:  str                      = "Sheet1",
        session_id:  Optional[str]            = None,
    ) -> ExecutorResult:
        """
        Execute all 11 phases against the given input file.

        Exactly one of *config* or *config_path* must be provided.

        Args:
            input_path  : Absolute or relative path to the input Excel/CSV.
            output_path : Destination path for the transformed output.
            config      : Pre-built config dict (alternative to config_path).
            config_path : Path to a *_config.yaml file (alternative to config).
            sheet_name  : Worksheet to read/write (Excel only).
            session_id  : Optional label for logging/result tracking.

        Returns:
            ExecutorResult
        """
        if config is None and config_path is None:
            raise ValueError(
                "PhaseExecutorAgent.run() requires either config= or config_path=."
            )

        # Load YAML if needed
        if config is None:
            config = self._load_yaml(config_path)  # type: ignore[arg-type]

        # Coerce LLM-emitted quoted scalars ('true'/'false'/'null') to real
        # Python types so downstream `if cfg.get("applied"):` checks work.
        config = normalize_config_scalars(config)

        print("\n" + "=" * 68)
        print("  🚀  PhaseExecutorAgent — Executing phases 0 → 10")
        print(f"      Input  : {input_path}")
        print(f"      Output : {output_path}")
        print(f"      Sheet  : {sheet_name}")
        print("=" * 68 + "\n")

        start_total  = time.perf_counter()
        phase_steps: List[PhaseStepResult] = []
        df:          Optional[pd.DataFrame] = None
        workbook     = None
        saved_path:  Optional[str]          = None
        fatal_error: Optional[str]          = None

        # ── PHASE 0 — Setup ───────────────────────────────────────────────
        step = self._make_step("Phase 0  — Setup", 0, df_in=None)
        t0   = time.perf_counter()
        try:
            df = _phase0(input_path, config, sheet_name=sheet_name)
            self._finish_step(step, df, duration=time.perf_counter() - t0)
            print(f"[Agent] Phase 0 ✅  shape={df.shape}")
        except Exception as exc:
            fatal_error = f"Phase 0 failed: {exc}"
            step.status    = "error"
            step.error     = str(exc)
            step.duration_s = time.perf_counter() - t0
            phase_steps.append(step)
            print(f"[Agent] Phase 0 ❌  FATAL: {exc}")
            print(traceback.format_exc())
            return ExecutorResult(
                success     = False,
                session_id  = session_id,
                input_path  = input_path,
                output_path = None,
                sheet_name  = sheet_name,
                output_df   = None,
                phase_steps = phase_steps,
                total_s     = time.perf_counter() - start_total,
                error       = fatal_error,
            )
        phase_steps.append(step)

        # ── PHASES 1–8 — Data transformations ─────────────────────────────
        _data_phases = [
            ("Phase 1  — Cleaning",     1,  _phase1),
            ("Phase 2  — Structure",    2,  _phase2),
            ("Phase 3  — Columns",      3,  _phase3),
            ("Phase 4  — Rows",         4,  _phase4),
            ("Phase 5  — Enrichment",   5,  _phase5),
            ("Phase 6  — Aggregation",  6,  _phase6),
            ("Phase 7  — Advanced",     7,  _phase7),
            ("Phase 8  — Finalize",     8,  _phase8),
        ]

        for name, num, fn in _data_phases:
            step = self._make_step(name, num, df_in=df)
            t0   = time.perf_counter()
            try:
                result_df   = fn(df, config)  # type: ignore[call-arg]
                df          = result_df
                self._finish_step(step, df, duration=time.perf_counter() - t0)
                print(f"[Agent] {name} ✅  shape={df.shape}")
            except Exception as exc:
                step.status    = "error"
                step.error     = str(exc)
                step.duration_s = time.perf_counter() - t0
                step.rows_out  = df.shape[0] if df is not None else 0
                step.cols_out  = df.shape[1] if df is not None else 0
                print(f"[Agent] {name} ❌  {exc}")
                print(traceback.format_exc())
                phase_steps.append(step)
                if self._stop_on_error:
                    fatal_error = f"{name} failed: {exc}"
                    return ExecutorResult(
                        success     = False,
                        session_id  = session_id,
                        input_path  = input_path,
                        output_path = None,
                        sheet_name  = sheet_name,
                        output_df   = df,
                        phase_steps = phase_steps,
                        total_s     = time.perf_counter() - start_total,
                        error       = fatal_error,
                    )
                # Non-fatal: continue with the unchanged df
                phase_steps.append(step)
                continue
            phase_steps.append(step)

        # ── PHASE 9 — Formatting ──────────────────────────────────────────
        step = self._make_step("Phase 9  — Formatting", 9, df_in=df)
        t0   = time.perf_counter()
        try:
            df, workbook = _phase9(df, config, workbook=None, sheet_name=sheet_name)
            self._finish_step(step, df, duration=time.perf_counter() - t0)
            print(f"[Agent] Phase 9  — Formatting ✅  shape={df.shape}")
        except Exception as exc:
            step.status    = "error"
            step.error     = str(exc)
            step.duration_s = time.perf_counter() - t0
            step.rows_out  = df.shape[0] if df is not None else 0
            step.cols_out  = df.shape[1] if df is not None else 0
            workbook       = None
            print(f"[Agent] Phase 9  — Formatting ❌  {exc}")
            print(traceback.format_exc())
            if self._stop_on_error:
                phase_steps.append(step)
                return ExecutorResult(
                    success     = False,
                    session_id  = session_id,
                    input_path  = input_path,
                    output_path = None,
                    sheet_name  = sheet_name,
                    output_df   = df,
                    phase_steps = phase_steps,
                    total_s     = time.perf_counter() - start_total,
                    error       = f"Phase 9 failed: {exc}",
                )
        phase_steps.append(step)

        # ── PHASE 10 — Save ───────────────────────────────────────────────
        # Ensure output directory exists before passing to phase 10
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        step = self._make_step("Phase 10 — Save", 10, df_in=df)
        t0   = time.perf_counter()
        try:
            saved_path = _phase10(
                df,
                config,
                output_path,
                workbook   = workbook,
                sheet_name = sheet_name,
            )
            self._finish_step(step, df, duration=time.perf_counter() - t0)
            print(f"[Agent] Phase 10 — Save ✅  → {saved_path}")
        except Exception as exc:
            step.status    = "error"
            step.error     = str(exc)
            step.duration_s = time.perf_counter() - t0
            step.rows_out  = df.shape[0] if df is not None else 0
            step.cols_out  = df.shape[1] if df is not None else 0
            saved_path     = None
            print(f"[Agent] Phase 10 — Save ❌  {exc}")
            print(traceback.format_exc())
        phase_steps.append(step)

        # ── Compile result ────────────────────────────────────────────────
        total_s = time.perf_counter() - start_total
        result  = ExecutorResult(
            success     = saved_path is not None,
            session_id  = session_id,
            input_path  = input_path,
            output_path = saved_path,
            sheet_name  = sheet_name,
            output_df   = df,
            phase_steps = phase_steps,
            total_s     = total_s,
        )
        print(result.summary())
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_step(
        name:   str,
        num:    int,
        df_in:  Optional[pd.DataFrame],
    ) -> PhaseStepResult:
        """Initialise a PhaseStepResult with input shape pre-filled."""
        rows_in = df_in.shape[0] if df_in is not None else 0
        cols_in = df_in.shape[1] if df_in is not None else 0
        return PhaseStepResult(
            phase_name = name,
            phase_num  = num,
            status     = "running",
            rows_in    = rows_in,
            cols_in    = cols_in,
        )

    @staticmethod
    def _finish_step(
        step:     PhaseStepResult,
        df_out:   pd.DataFrame,
        duration: float,
    ) -> None:
        """Mark a step as successful and fill in output shape + duration."""
        step.status     = "ok"
        step.rows_out   = df_out.shape[0]
        step.cols_out   = df_out.shape[1]
        step.duration_s = duration

    @staticmethod
    def _load_yaml(config_path: str) -> Dict[str, Any]:
        """Load and parse a YAML config file."""
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(
                f"[PhaseExecutorAgent] Config file not found: {config_path}"
            )
        with open(p, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        print(f"[Agent] Config loaded from: {p}")
        return cfg or {}
