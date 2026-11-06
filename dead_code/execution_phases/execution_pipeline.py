"""
execution/execution_pipeline.py
=================================
⭐ MAIN ORCHESTRATOR

Runs all execution phases in the correct logical order:

  Phase 0  → Setup          (load + normalize + basic dtype + drop empties)
  Phase 1  → Cleaning       (trim, find/replace, type conv, missing values)
  Phase 2  → Structure      (transpose / pivot / unpivot / hierarchy)
  Phase 3  → Columns        (rename → split → merge → select → reorder)
  Phase 4  → Rows           (filter → dedup → sort → subtotals)
  Phase 5  → Enrichment     (lookup → calculated → conditional → text/date/math)
  Phase 6  → Aggregation    (groupby → cond-agg → running total → ranking)
  Phase 7  → Advanced       (array formulas → indirect refs)
  Phase 8  → Finalize       (formula→value, remove intermediate columns)
  Phase 9  → Formatting     (number fmt, cond fmt, data validation, charts)
  Phase 10 → Save           (write xlsx / csv / parquet …)

WHY THIS ORDER (reverse-engineering rationale)
----------------------------------------------
The detection layer (transformation_phases/) discovers WHAT transformations
happened by diffing input ↔ output.  The execution layer then replays those
transformations in the correct *logical dependency* order:

  1. Clean first — normalise types/whitespace before any comparison/formula
  2. Reshape structure — pivot/unpivot before column names stabilise
  3. Column ops — rename before filter/enrichment references new names
  4. Row ops — filter before enrichment (don't compute on discarded rows)
  5. Enrich — add derived columns after the data set is its final shape
  6+ Aggregate → advanced → finalize → format → save

CONFIG KEY MAPPING — detection phase ≠ execution phase number
--------------------------------------------------------------
The detection phases are numbered by transformation *type* (1=structure,
2=rows, 3=columns … 6=cleaning), NOT by execution order.  Each execution
phase reads from the config key that matches its transformation type:

  Execution Phase 1  reads  config["phase_6_cleaning"]   ← detected as type 6
  Execution Phase 2  reads  config["phase_1_structure"]  ← detected as type 1
  Execution Phase 3  reads  config["phase_3_columns"]    ← detected as type 3
  Execution Phase 4  reads  config["phase_2_rows"]       ← detected as type 2
  Execution Phase 5  reads  config["phase_4_enrichment"] ← detected as type 4
  Execution Phase 6  reads  config["phase_5_aggregation"]← detected as type 5

Usage
-----
    from execution_phases.execution_pipeline import ExecutionPipeline

    pipeline = ExecutionPipeline(
        input_path  = "data/input_files/myfile/input.xlsx",
        output_path = "data/executed_outputs/myfile/output.xlsx",
        config      = my_config_dict,          # from phases/main.py detect_all()
    )
    result = pipeline.run()
    print(result.summary())
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ── Phase imports ─────────────────────────────────────────────────────────────
from execution_phases.execution_phase_0_setup      import run as phase0
from execution_phases.execution_phase_1_cleaning   import run as phase1
from execution_phases.execution_phase_2_structure  import run as phase2
from execution_phases.execution_phase_3_columns    import run as phase3
from execution_phases.execution_phase_4_rows       import run as phase4, run_deferred_filters
from execution_phases.execution_phase_5_enrichment import run as phase5
from execution_phases.execution_phase_6_aggregation import run as phase6
from execution_phases.execution_phase_7_advanced   import run as phase7
from execution_phases.execution_phase_8_finalize   import run as phase8
from execution_phases.execution_phase_9_formatting import run as phase9
from execution_phases.execution_phase_10_save      import run as phase10


# ─────────────────────────────────────────────────────────────────────────────
# Config normalization
# ─────────────────────────────────────────────────────────────────────────────

_SCALAR_COERCIONS = {
    "true":  True,
    "false": False,
    "null":  None,
    "none":  None,
    "~":     None,
}


def normalize_config_scalars(obj: Any) -> Any:
    """
    Recursively coerce LLM-emitted scalar strings to their proper Python types.

    Some LLMs (when told to "quote scalars with special characters") wrap
    booleans and null as quoted strings in YAML, e.g. ``applied: 'false'`` or
    ``type: 'null'``. After ``yaml.safe_load`` these become Python strings,
    and ``if cfg.get("applied"):`` then evaluates the non-empty string
    ``"false"`` as truthy — so every phase incorrectly executes.

    This function walks the config tree and converts the string forms
    "true" / "false" / "null" / "none" / "~" (case-insensitive) into their
    actual Python equivalents.  All other strings are left untouched.
    """
    if isinstance(obj, dict):
        return {k: normalize_config_scalars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_config_scalars(v) for v in obj]
    if isinstance(obj, str):
        key = obj.strip().lower()
        if key in _SCALAR_COERCIONS:
            return _SCALAR_COERCIONS[key]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    phase:      str
    status:     str          # "ok" | "skipped" | "error"
    rows_in:    int = 0
    rows_out:   int = 0
    cols_in:    int = 0
    cols_out:   int = 0
    duration_s: float = 0.0
    error:      Optional[str] = None


@dataclass
class PipelineResult:
    success:     bool
    output_path: Optional[str]
    df:          Optional[pd.DataFrame]
    phases:      List[PhaseResult] = field(default_factory=list)
    total_s:     float = 0.0
    error:       Optional[str] = None

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"  Execution Pipeline {'✅ SUCCESS' if self.success else '❌ FAILED'}",
            f"  Output : {self.output_path or 'N/A'}",
            f"  Total  : {self.total_s:.2f}s",
            "-" * 60,
        ]
        for pr in self.phases:
            shape = f"{pr.rows_out}r × {pr.cols_out}c"
            badge = "✅" if pr.status == "ok" else ("⏭️ " if pr.status == "skipped" else "❌")
            lines.append(
                f"  {badge} {pr.phase:<30} {pr.duration_s:>5.2f}s  [{shape}]"
                + (f"  ← {pr.error}" if pr.error else "")
            )
        if self.error:
            lines.append(f"\n  FATAL: {self.error}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline class
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionPipeline:
    """
    Orchestrates all 11 execution phases end-to-end.

    Args:
        input_path  : Path to the raw input Excel / CSV file.
        output_path : Destination path for the transformed output.
        config      : Merged config dict from phases/main.py detect_all().
        sheet_name  : Sheet to read from input and write to output (Excel only).
        stop_on_error : If True, abort the pipeline on the first phase error.
                        If False, log the error and continue with the current df.
    """

    def __init__(
        self,
        input_path:     str,
        output_path:    str,
        config:         Dict[str, Any],
        sheet_name:     str = "Sheet1",
        stop_on_error:  bool = False,
    ) -> None:
        self.input_path    = input_path
        self.output_path   = output_path
        self.config        = normalize_config_scalars(config)
        self.sheet_name    = sheet_name
        self.stop_on_error = stop_on_error

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> PipelineResult:
        """Execute the full pipeline and return a PipelineResult."""
        start_total = time.perf_counter()
        phase_results: List[PhaseResult] = []
        df: Optional[pd.DataFrame] = None
        workbook = None
        saved_path: Optional[str] = None

        print("\n" + "=" * 60)
        print("  🚀  Execution Pipeline Starting")
        print(f"      Input  : {self.input_path}")
        print(f"      Output : {self.output_path}")
        print("=" * 60 + "\n")

        # ── PHASE 0 : Setup ───────────────────────────────────────────────────
        pr = self._run_phase(
            name="Phase 0 — Setup",
            df_in=None,   # Phase 0 loads its own data
        )
        try:
            t0 = time.perf_counter()
            df = phase0(self.input_path, self.config, sheet_name=self.sheet_name)
            pr.rows_out, pr.cols_out = df.shape
            pr.status = "ok"
            pr.duration_s = time.perf_counter() - t0
        except Exception as exc:
            pr.status = "error"
            pr.error  = str(exc)
            pr.duration_s = time.perf_counter() - t0
            phase_results.append(pr)
            print(f"[Phase 0] FATAL: {exc}\n{traceback.format_exc()}")
            return PipelineResult(
                success=False, output_path=None, df=None,
                phases=phase_results,
                total_s=time.perf_counter() - start_total,
                error=f"Phase 0 failed: {exc}",
            )
        phase_results.append(pr)

        # ── PHASES 1–8 : Data transformations ────────────────────────────────
        # Map config phase names to (display_name, function)
        _phase_map = {
            "phase_1_structure":    ("Structure",    phase2),
            "phase_2_rows":         ("Rows",         phase4),
            "phase_3_columns":      ("Columns",      phase3),
            "phase_4_enrichment":   ("Enrichment",   phase5),
            "phase_5_aggregation":  ("Aggregation",  phase6),
            "phase_6_cleaning":     ("Cleaning",     phase1),
            "phase_7_advanced":     ("Advanced",     phase7),
            "phase_8_formatting":   ("Formatting",   phase9),  # Note: phase9 has different signature
            "phase_9_final":        ("Finalize",     phase8),
        }
        
        # Get execution order from config, fallback to default if not present
        default_order = [
            "phase_6_cleaning",     # Cleaning first (was hardcoded Phase 1)
            "phase_1_structure",    # Structure (was hardcoded Phase 2)
            "phase_3_columns",      # Columns (was hardcoded Phase 3)
            "phase_2_rows",         # Rows (was hardcoded Phase 4)
            "phase_4_enrichment",   # Enrichment (was hardcoded Phase 5)
            "phase_5_aggregation",  # Aggregation (was hardcoded Phase 6)
            "phase_7_advanced",     # Advanced (was hardcoded Phase 7)
            "phase_9_final",        # Finalize (was hardcoded Phase 8)
        ]
        
        config_order = self.config.get("execution_order", default_order)
        
        # Filter to only phases we handle here (exclude phase_8_formatting which needs special handling)
        data_phase_names = [p for p in config_order if p in _phase_map and p != "phase_8_formatting"]
        
        print(f"[Pipeline] Execution order from config: {config_order}")
        print(f"[Pipeline] Data phases to run: {data_phase_names}")
        
        phase_num = 1
        for phase_key in data_phase_names:
            display_name, fn = _phase_map[phase_key]
            name = f"Phase {phase_num} — {display_name}"
            pr = self._run_phase(name=name, df_in=df)
            t0 = time.perf_counter()
            try:
                result_df = fn(df, self.config)
                df = result_df
                pr.rows_out, pr.cols_out = df.shape
                pr.status = "ok"
            except Exception as exc:
                pr.status = "error"
                pr.error  = str(exc)
                print(f"[{name}] ERROR: {exc}\n{traceback.format_exc()}")
                if self.stop_on_error:
                    pr.duration_s = time.perf_counter() - t0
                    phase_results.append(pr)
                    return PipelineResult(
                        success=False, output_path=None, df=df,
                        phases=phase_results,
                        total_s=time.perf_counter() - start_total,
                        error=f"{name} failed: {exc}",
                    )
            pr.duration_s = time.perf_counter() - t0
            phase_results.append(pr)
            print(f"[Agent] {name} ✅  shape={df.shape}")
            phase_num += 1
            
            # ── POST-ENRICHMENT FILTER ────────────────────────────────────────
            # After enrichment, re-run filters to catch filters on derived columns
            # that were skipped during Phase 4 (Rows) because columns didn't exist yet
            print("before phase 4 enrichment check")
            if phase_key == "phase_4_enrichment":
                print(f"\n[Pipeline] ═══════════════════════════════════════════════════════════")
                print(f"[Pipeline] Running POST-ENRICHMENT FILTERS (for derived column filters)")
                print(f"[Pipeline] ═══════════════════════════════════════════════════════════")
                deferred_name = f"Phase {phase_num} — Deferred Filters"
                deferred_pr = self._run_phase(name=deferred_name, df_in=df)
                t0 = time.perf_counter()
                try:
                    rows_before = len(df)
                    df = run_deferred_filters(df, self.config)
                    deferred_pr.rows_out, deferred_pr.cols_out = df.shape
                    deferred_pr.status = "ok"
                    rows_after = len(df)
                    if rows_before != rows_after:
                        print(f"[Agent] {deferred_name} ✅  shape={df.shape} (filtered {rows_before} → {rows_after})")
                    else:
                        print(f"[Agent] {deferred_name} ✅  shape={df.shape} (no additional filtering)")
                except Exception as exc:
                    deferred_pr.status = "error"
                    deferred_pr.error = str(exc)
                    print(f"[{deferred_name}] ERROR: {exc}\n{traceback.format_exc()}")
                    if self.stop_on_error:
                        deferred_pr.duration_s = time.perf_counter() - t0
                        phase_results.append(deferred_pr)
                        return PipelineResult(
                            success=False, output_path=None, df=df,
                            phases=phase_results,
                            total_s=time.perf_counter() - start_total,
                            error=f"{deferred_name} failed: {exc}",
                        )
                deferred_pr.duration_s = time.perf_counter() - t0
                phase_results.append(deferred_pr)
                phase_num += 1

        # ── Formatting Phase (special handling due to workbook return) ────────
        pr = self._run_phase(name=f"Phase {phase_num} — Formatting", df_in=df)
        t0 = time.perf_counter()
        try:
            df, workbook = phase9(df, self.config, workbook=None, sheet_name=self.sheet_name)
            pr.rows_out, pr.cols_out = df.shape
            pr.status = "ok"
        except Exception as exc:
            pr.status = "error"
            pr.error  = str(exc)
            workbook  = None
            print(f"[Phase {phase_num} — Formatting] ERROR: {exc}\n{traceback.format_exc()}")
            if self.stop_on_error:
                pr.duration_s = time.perf_counter() - t0
                phase_results.append(pr)
                return PipelineResult(
                    success=False, output_path=None, df=df,
                    phases=phase_results,
                    total_s=time.perf_counter() - start_total,
                    error=f"Phase {phase_num} — Formatting failed: {exc}",
                )
        pr.duration_s = time.perf_counter() - t0
        phase_results.append(pr)
        phase_num += 1

        # ── Save Phase ────────────────────────────────────────────────────────
        save_phase_num = phase_num
        pr = self._run_phase(name=f"Phase {save_phase_num} — Save", df_in=df)
        t0 = time.perf_counter()
        try:
            saved_path = phase10(
                df, self.config, self.output_path,
                workbook=workbook, sheet_name=self.sheet_name,
            )
            pr.rows_out, pr.cols_out = df.shape
            pr.status = "ok"
        except Exception as exc:
            pr.status = "error"
            pr.error  = str(exc)
            saved_path = None
            print(f"[Phase {save_phase_num} — Save] ERROR: {exc}\n{traceback.format_exc()}")
        pr.duration_s = time.perf_counter() - t0
        phase_results.append(pr)

        total_s = time.perf_counter() - start_total
        result  = PipelineResult(
            success=saved_path is not None,
            output_path=saved_path,
            df=df,
            phases=phase_results,
            total_s=total_s,
        )
        print(result.summary())
        return result

    # ── Internal helper ───────────────────────────────────────────────────────

    def _run_phase(
        self,
        name:   str,
        df_in:  Optional[pd.DataFrame],
    ) -> PhaseResult:
        rows_in = df_in.shape[0] if df_in is not None else 0
        cols_in = df_in.shape[1] if df_in is not None else 0
        return PhaseResult(
            phase=name,
            status="running",
            rows_in=rows_in,
            cols_in=cols_in,
        )