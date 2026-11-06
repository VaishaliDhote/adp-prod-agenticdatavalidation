# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 6 - LLM config populator/corrector (closest to compiler, tasks 12-18).
# REUSABLE but REPURPOSE. NOTE: misnamed '_old' - it is LIVE (imported by graph_builder
# as PhaseTransformationAgent). Rename it. Rework: drive a DETERMINISTIC IR->JSON
# compiler instead of LLM free-form rewriting of the bespoke YAML structure.
# ============================================================================
"""
agents/phase_transformation_agent.py
=======================================
PhaseTransformationAgent
--------------------------
Calls GetConfigurationService to get the auto-detected phase configuration,
then sends it to the LLM together with:

  • Full input DataFrame   (loaded from ConfigurationResult.input_file)
  • Full output DataFrame  (loaded from ConfigurationResult.output_file)
  • Column mapping         (from GetConfigurationService)
  • Auto-detected config   (from GetConfigurationService)

The LLM reviews the config and returns it **in the exact same structure**,
only correcting wrong detections (deleting false positives, adding missed
operations, fixing values inside existing blocks).

The returned config is validated to ensure it is structurally identical to
the input config before being accepted.

Usage
-----
    from agents.phase_transformation_agent import PhaseTransformationAgent
    from llm_client import LLMClient

    agent  = PhaseTransformationAgent(LLMClient())
    result = await agent.run(
        session_id           = "160cf6ce-bb3b-4d0d-9d45-18645e791e2f",
        final_analysis_state = my_final_analysis_state,   # FinalAnalysisState
    )

    corrected_config = result["corrected_config"]
    print(result["summary"])
"""

from __future__ import annotations

import json
import re
from datetime import datetime
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from charset_normalizer import detect
import pandas as pd
import yaml

from llm_client import LLMClient
from services.get_configuration import GetConfigurationService, ConfigurationResult
from states.final_analysis_state import FinalAnalysisState
from utils.serializer import make_serializable
from services.transformation_service import run_transformation_detection, TransformationResult,build_final_config_user_prompt
from services.llm_transformation import detect_transformations_for_session
# from prompts.prompt_generate_final_config import GENERATE_FINAL_CONFIG_PROMPT, GENERATE_FINAL_CONFIG_USER_PROMPT
from prompts.config_summary_combined import GENERATE_FINAL_CONFIG_SUMMARY_PROMPT

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Top-level keys that MUST be present in any valid phase config
_REQUIRED_TOP_LEVEL_KEYS = {
    "preprocessing",
    "execution_order",
    "error_handling",
    "metadata",
}

# Phase keys that are expected in the config body
_PHASE_KEYS = {
    "phase_1_structure",
    "phase_2_rows",
    "phase_3_columns",
    "phase_4_enrichment",
    "phase_5_aggregation",
    "phase_6_cleaning",
    "phase_7_advanced",
    "phase_8_formatting",
    "phase_9_final",
}

# Max tokens for LLM correction call
_MAX_TOKENS = 16000


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

class PhaseTransformationAgent:
    """
    Corrects an auto-detected phase configuration using LLM reasoning.

    The agent:
      1. Calls GetConfigurationService.get_config(session_id) to get the
         auto-detected config.
      2. Loads the full input and output DataFrames from the resolved file paths.
      3. Sends df_in, df_out, column_mapping, and auto_detected_config to the
         LLM with a strict prompt that instructs it to return the config in the
         EXACT same YAML structure, only fixing incorrect detections.
      4. Parses & validates the LLM response, falling back to the original
         config if the response is structurally invalid.
      5. Returns a result dict with corrected_config, corrections_made,
         and a human-readable summary.

    Args:
        llm_client           : Initialised LLMClient instance.
        config_service       : Optional pre-built GetConfigurationService.
                               If None, a default one is created.
        max_retries          : Number of LLM retry attempts on parse failure.
        save_corrected_config: Persist the corrected config YAML alongside
                               the original in data/generated_configs/.
    """

    def __init__(
        self,
        llm_client:            LLMClient,
        config_service:        Optional[GetConfigurationService] = None,
        max_retries:           int  = 0,
        save_corrected_config: bool = True,
    ) -> None:
        self._llm            = llm_client
        self._svc            = config_service or GetConfigurationService()
        self._max_retries    = max_retries
        self._save_corrected = save_corrected_config

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def run(
        self,
        session_id:           str,
        final_analysis_state: FinalAnalysisState,
        input_df:            pd.DataFrame,
        output_df:          pd.DataFrame,
        save_config:         bool     = True       
    ) -> Dict[str, Any]:
        
        agent_start = time.perf_counter()
        timings: Dict[str, float] = {}

        print("\n" + "=" * 65)
        print("  🤖  PhaseTransformationAgent — Starting")
        print(f"      Session : {session_id}")
        print("=" * 65 + "\n")

        # ── Step 1: Get auto-detected config ──────────────────────────────
        print("[Agent] Step 1/4 — Running GetConfigurationService …")
        _t = time.perf_counter()
        # config_result: ConfigurationResult = self._svc.get_config(
        #     session_id           = session_id,
        #     sheet_name           = sheet_name,
        #     extra_column_mapping = extra_column_mapping,
        # )
        detected_transformations = final_analysis_state.get("transformations_llm_detected")
        # column_mapping = final_analysis_state.get("column_mappings")
        if not detected_transformations:
            raise ValueError("FinalAnalysisState is missing 'transformations_llm_detected' key needed for LLM context.")
        config_result:TransformationResult = run_transformation_detection(
            file_analysis_state = final_analysis_state,
            input_df            = input_df,
            output_df           = output_df,
            save_config         = save_config,
        )        
        timings["1_get_configuration"] = round(time.perf_counter() - _t, 3)
        print(f"[Agent] ⏱  Step 1 done in {timings['1_get_configuration']}s")
        if config_result.error:
            return self._error_result(
                session_id,
                config_result.config,
                config_result.column_mapping,
                f"GetConfigurationService failed: {config_result.error}",
            )

        original_config = config_result.config 
        column_mapping   = config_result.column_mapping 
        # print(f"[Agent] Config obtained. Active phases: "
        #       f"{self._active_phases(original_config)}")

        # ── Step 2: Load full DataFrames ──────────────────────────────────
        # print("[Agent] Step 2/4 — Loading full input/output DataFrames …")
        # _t = time.perf_counter()
        # df_in  = self._load_full_df(config_result.input_file,  config_result.sheet_name, "input")
        # df_out = self._load_full_df(config_result.output_file, config_result.sheet_name, "output")
        # timings["2_load_dataframes"] = round(time.perf_counter() - _t, 3)
        # print(f"[Agent] ⏱  Step 2 done in {timings['2_load_dataframes']}s  "
        #       f"(input {df_in.shape}, output {df_out.shape})")

        # ── Step 3: Build LLM context ──────────────────────────────────────
        # print("[Agent] Step 3/4 — Building LLM context …")
        # _t = time.perf_counter()
        # context_payload = self._build_context(
        #     session_id     = session_id,
        #     column_mapping = column_mapping,
        #     config_result  = config_result,
        #     df_in          = input_df,
        #     df_out         = output_df,
        # )
        # timings["3_build_context"] = round(time.perf_counter() - _t, 3)
        # print(f"[Agent] ⏱  Step 3 done in {timings['3_build_context']}s")

        # ── Step 4: LLM correction call ───────────────────────────────────
        print("[Agent] Step 4/4 — Calling LLM for config correction …")
        _t = time.perf_counter()
        
        corrected_config, corrections_made, success, llm_attempt_timings, llm_summary = \
            await self._call_llm_with_retry(
                original_config = original_config,
                detected_transformations=detected_transformations,
                # context_payload = context_payload,
                session_id       = session_id,
                df_in            = input_df,
                df_out           = output_df,
            )
        timings["4_llm_correction"] = round(time.perf_counter() - _t, 3)
        timings.update(llm_attempt_timings)
        print(f"[Agent] ⏱  Step 4 done in {timings['4_llm_correction']}s")

        # ── Programmatic corrections ──────────────────────────────────────
        _t = time.perf_counter()
        serial_column_analysis = self._analyze_serial_columns(input_df, output_df, column_mapping)
        corrected_config, programmatic_corrections = self._apply_programmatic_corrections(
            corrected_config, serial_column_analysis
        )
        corrections_made.extend(programmatic_corrections)
        timings["5_programmatic_corrections"] = round(time.perf_counter() - _t, 3)
        print(f"[Agent] ⏱  Programmatic corrections done in {timings['5_programmatic_corrections']}s")

        # ── Optionally save corrected config ──────────────────────────────
        saved_path: Optional[str] = None
        if self._save_corrected and success:
            _t = time.perf_counter()
            saved_path = self._save_corrected_yaml(session_id, corrected_config)
            timings["6_save_yaml"] = round(time.perf_counter() - _t, 3)
            print(f"[Agent] ⏱  YAML save done in {timings['6_save_yaml']}s → {saved_path}")

        timings["total"] = round(time.perf_counter() - agent_start, 3)

        # ── Build result ──────────────────────────────────────────────────
        result = {
            "session_id":         session_id,
            # "original_config":    original_config,
            "corrected_config":   corrected_config,
            "column_mapping":     column_mapping,
            # "corrections_made":   corrections_made,
            "correction_success": success,
            # "config_saved_to":    saved_path,
            "summary":            llm_summary,
            # "timings":            timings,
            # "summary":            self._build_summary(
            #     session_id, config_result, corrections_made, success, saved_path, timings
            # ),
        }

        # print(result["summary"])
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Context builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_context(
        self,
        session_id:     str,
        config_result:  ConfigurationResult,
        column_mapping: Dict[str, str],
        df_in:          pd.DataFrame,
        df_out:         pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        Assemble the compressed context payload serialised into the LLM prompt.

        The ``auto_detected_config`` is pre-processed by
        ``_compress_config_for_llm`` before inclusion, which:
          • Strips ``aliases`` arrays (engine-only metadata).
          • Replaces ``applied:false`` operation details with a ``__false__``
            placeholder (the LLM only needs to flip the flag).
          • Truncates long ``conditional_columns`` condition lists to a few
            examples + a ``__truncated__`` sentinel.
          • Collapses zero-confidence ``calculated_columns`` entries.
          • Drops the ``metadata`` section (redundant with ``column_mapping``).

        After the LLM returns a corrected config, ``_expand_corrected_config``
        restores all stripped data before the result is returned to the caller.

        Sections sent:
          session_metadata       — file paths, shapes, sheet name
          column_mapping         — {input_col: output_col} rename map
          input_df               — first 10 rows of input DataFrame as records
          output_df              — first 10 rows of output DataFrame as records
          serial_column_analysis — detected serial/index columns and patterns
          auto_detected_config   — COMPRESSED phase config (see above)
        """
        payload: Dict[str, Any] = {
            "column_mapping":       column_mapping,
           "input_df":             make_serializable(
                df_in.to_dict(orient="records")
            ),
            "output_df":            make_serializable(
                df_out.to_dict(orient="records")
            ),
            "data_shapes": {
                "input_rows":  len(df_in),
                "output_rows": len(df_out),
                "input_cols":  len(df_in.columns),
                "output_cols": len(df_out.columns),
            },
            "serial_column_analysis": self._analyze_serial_columns(
                df_in, df_out, column_mapping
            ),
            "auto_detected_config": self._compress_config_for_llm(config_result.config),
        }

        return payload

    # ─────────────────────────────────────────────────────────────────────────
    # LLM call + retry
    # ─────────────────────────────────────────────────────────────────────────

    async def _call_llm_with_retry(
        self,
        original_config: Dict[str, Any],
        # context_payload: Dict[str, Any],
        detected_transformations: str,
        # fall_back: Optional[bool] = False,
        session_id: Optional[str] = None,
        df_in: Optional[pd.DataFrame] = None,
        df_out: Optional[pd.DataFrame] = None,
    ) -> tuple[Dict[str, Any], List[str], bool, Dict[str, float], str]:
        """
        Call the LLM up to (max_retries + 1) times.

        Returns:
            (corrected_config, corrections_made, success_flag, attempt_timings, summary)
        """
        # if fall_back:
        #     print("[Agent] ⚠️  Falling back to original config without LLM correction.")
        #     # system_prompt    = self._build_system_prompt()
        #     # user_context     = self._build_user_context(context_payload)
        #     # extra_feedback   = ""
        #     # attempt_timings: Dict[str, float] = {}

        #     # for attempt in range(1):
        #     #     _t = time.perf_counter()
        #     #     try:
        #     #         print(f"\n[Agent] LLM attempt {attempt + 1}/{self._max_retries + 1} …")
        #     #         print(f"[Agent] Context size: {len(user_context)} chars, system prompt: {len(system_prompt)} chars")

        #     #         full_context = user_context + extra_feedback
                    
        #     #         print(f"[Agent] ⏳ Sending request to LLM (timeout: 600s)...")
        #     #         response = await self._llm.generate_long_response(
        #     #             system_prompt = system_prompt,
        #     #             context       = full_context,
        #     #             max_tokens    = _MAX_TOKENS,
        #     #             timeout       = 600,  # 10 minutes for complex requests
        #     #         )
        #     #         print("*"*80)
        #     #         print(f"[Agent] LLM response received, processing … {response}")
        #     #         llm_s = round(time.perf_counter() - _t, 3)
        #     #         attempt_timings[f"4_llm_attempt_{attempt + 1}"] = llm_s
        #     #         print(f"[Agent] ✅ LLM response received in {llm_s}s")
        #     #         print(f"[Agent] Response size: {len(response)} chars")

        #     #         _tp = time.perf_counter()
        #     #         print(f"[Agent] ⏳ Parsing and validating response...")
        #     #         corrected, corrections, ok = self._parse_and_validate_response(
        #     #             response        = response,
        #     #             original_config = original_config,
        #     #         )
        #     #         parse_s = round(time.perf_counter() - _tp, 3)
        #     #         attempt_timings[f"4_parse_attempt_{attempt + 1}"] = parse_s
        #     #         print(f"[Agent] ✅ Parse + validate completed in {parse_s}s  →  valid={ok}")

        #     #         if ok:
        #     #             # Restore stripped fields (aliases, full conditions, false-op details)
        #     #             corrected = self._expand_corrected_config(corrected, original_config)
        #     #             print(f"[Agent] ✅ LLM correction accepted "
        #     #                 f"({len(corrections)} change(s) noted).")
        #     #             return corrected, corrections, True, attempt_timings

        #     #         # Validation failed — give the LLM the error on retry
        #     #         extra_feedback = (
        #     #             f"\n\n[PREVIOUS ATTEMPT {attempt + 1} FAILED]\n"
        #     #             f"Reason: {corrections[0] if corrections else 'structural mismatch'}\n"
        #     #             f"Please fix and return ONLY the corrected YAML inside "
        #     #             f"```yaml ... ``` fences."
        #     #         )
        #     #         print(f"[Agent] ⚠️  Attempt {attempt + 1} invalid — retrying …")

        #     #     except TimeoutError as exc:
        #     #         attempt_timings[f"4_llm_attempt_{attempt + 1}"] = round(
        #     #             time.perf_counter() - _t, 3
        #     #         )
        #     #         extra_feedback = (
        #     #             f"\n\n[PREVIOUS ATTEMPT {attempt + 1} TIMEOUT]\n"
        #     #             f"Error: {exc}\nPlease try again with the same request."
        #     #         )
        #     #         print(f"[Agent] ❌ Attempt {attempt + 1} TIMEOUT after {attempt_timings[f'4_llm_attempt_{attempt + 1}']}s: {exc}")
        #     #         if attempt < self._max_retries:
        #     #             print(f"[Agent] ⏳ Retrying after timeout...")
                        
        #     #     except Exception as exc:
        #     #         attempt_timings[f"4_llm_attempt_{attempt + 1}"] = round(
        #     #             time.perf_counter() - _t, 3
        #     #         )
        #     #         extra_feedback = (
        #     #             f"\n\n[PREVIOUS ATTEMPT {attempt + 1} RAISED AN EXCEPTION]\n"
        #     #             f"Error: {exc}\nPlease try again."
        #     #         )
        #     #         print(f"[Agent] ❌ Attempt {attempt + 1} exception after {attempt_timings[f'4_llm_attempt_{attempt + 1}']}s: {exc}")

        #     # print("[Agent] ⚠️  All LLM attempts failed. Returning original config.")
        #     # return (
        #     #     original_config,
        #     #     ["LLM correction failed after all retries — original config preserved."],
        #     #     False,
        #     #     attempt_timings,
        #     # )
        # else:
            # llm_detected_transformations = await detect_transformations_for_session(session_id=session_id)
            # print(f"[Agent] Detected transformations for session {session_id}" )
            # auto_detected_config = original_config
            # system_prompt    = GENERATE_FINAL_CONFIG_PROMPT
            
            # # Prepare DataFrame info for prompt
            # input_columns_list = list(df_in.columns) if df_in is not None else []
            # output_columns_list = list(df_out.columns) if df_out is not None else []
            # input_row_count = len(df_in) if df_in is not None else 0
            # output_row_count = len(df_out) if df_out is not None else 0
            # input_dtypes_dict = {str(k): str(v) for k, v in df_in.dtypes.to_dict().items()} if df_in is not None else {}
            # output_dtypes_dict = {str(k): str(v) for k, v in df_out.dtypes.to_dict().items()} if df_in is not None else {}
            # input_data_sample = df_in.head(10).to_string() if df_in is not None else ""
            # output_data_sample = df_out.head(10).to_string() if df_out is not None else ""
            
            # # Dump YAML and escape braces for .format()
            # config_yaml = yaml.dump(auto_detected_config, sort_keys=False)
            # # Replace { with {{ and } with }} to escape for .format()
            # config_yaml_escaped = config_yaml.replace('{', '{{').replace('}', '}}')
            
            # # Convert dicts to JSON and escape braces
            # input_dtypes_json = json.dumps(input_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')
            # output_dtypes_json = json.dumps(output_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')
            
            # # Convert detection response to string and escape braces
            # detection_response_str = str(llm_detected_transformations).replace('{', '{{').replace('}', '}}')
            
            # # Escape braces in data samples
            # input_data_sample_escaped = input_data_sample.replace('{', '{{').replace('}', '}}')
            # output_data_sample_escaped = output_data_sample.replace('{', '{{').replace('}', '}}')
            
            # user_context = GENERATE_FINAL_CONFIG_USER_PROMPT.format(
            #     detection_response=detection_response_str,
            #     config_result_yaml=config_yaml_escaped,
            #     input_columns_list=input_columns_list,
            #     input_row_count=input_row_count,
            #     input_dtypes_dict=input_dtypes_json,
            #     output_columns_list=output_columns_list,
            #     output_row_count=output_row_count,
            #     output_dtypes_dict=output_dtypes_json,
            #     input_data_sample=input_data_sample_escaped,
            #     output_data_sample=output_data_sample_escaped,
            #     session_id=session_id or "",
            # )
        user_context=build_final_config_user_prompt(
            df_in=df_in,
            df_out=df_out,
            detected_transformation=detected_transformations,
            original_config=original_config
            )
        system_prompt    = GENERATE_FINAL_CONFIG_SUMMARY_PROMPT
        attempt_timings: Dict[str, float] = {}
        last_summary: str = ""

        for attempt in range(1):
            _t = time.perf_counter()
            try:
                print(f"\n[Agent] LLM attempt {attempt + 1}/{self._max_retries + 1} …")
                print(f"[Agent] Context size: {len(user_context)} chars, system prompt: {len(system_prompt)} chars")
                
                print(f"[Agent] ⏳ Sending request to LLM (timeout: 600s)...")
                # response = await self._llm.generate_long_response(
                #     system_prompt = system_prompt,
                #     context       = user_context,
                #     max_tokens    = _MAX_TOKENS,
                #     timeout       = 600,  # 10 minutes for complex requests
                # )
                response = await self._llm.generate_response(
                    system_prompt=system_prompt,
                    context=user_context,
                    max_tokens=_MAX_TOKENS,
                    timeout=600,)
                llm_s = round(time.perf_counter() - _t, 3)
                attempt_timings[f"4_llm_attempt_{attempt + 1}"] = llm_s
                print(f"[Agent] ✅ LLM response received in {llm_s}s")
                print(f"[Agent] Response size: {len(response)} chars")
                print(f"[Agent] Response content: {response}")

                _tp = time.perf_counter()
                corrected, corrections, ok, summary = self._parse_and_validate_response(
                    response        = response,
                    original_config = original_config,
                )
                parse_s = round(time.perf_counter() - _tp, 3)
                attempt_timings[f"4_parse_attempt_{attempt + 1}"] = parse_s
                print(f"[Agent] ✅ Parse + validate completed in {parse_s}s  →  valid={ok}")

                last_summary = summary or last_summary  # preserve even if YAML parse fails

                if ok:
                    corrected = self._expand_corrected_config(corrected, original_config)
                    print(f"[Agent] ✅ LLM correction accepted "
                        f"({len(corrections)} change(s) noted).")
                    return corrected, corrections, True, attempt_timings, summary

                print(f"[Agent] ⚠️  Attempt {attempt + 1} invalid")

            except Exception as exc:
                attempt_timings[f"4_llm_attempt_{attempt + 1}"] = round(
                    time.perf_counter() - _t, 3
                )
                print(f"[Agent] ❌ LLM call failed: {exc}")
                return original_config, [f"LLM call failed: {exc}"], False, attempt_timings, ""

        print("[Agent] ⚠️  All LLM attempts failed. Returning original config.")
        return (
            original_config,
            ["LLM correction failed after all retries — original config preserved."],
            False,
            attempt_timings,
            last_summary,  # return whatever summary was extracted, even if YAML was invalid
        )


    # ─────────────────────────────────────────────────────────────────────────
    # Prompt builders
    # ─────────────────────────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return """You are an expert data-transformation configuration reviewer with STRICT validation rules.

You will receive:
  1. Session metadata      — input/output file paths, shapes, sheet name.
  2. Column mapping        — { input_column_name: output_column_name }.
  3. input_df              — the FULL input DataFrame as a JSON array of records.
  4. output_df             — the FULL output DataFrame as a JSON array of records.
  5. serial_column_analysis— detected serial/index columns with their sequence analysis.
  6. auto_detected_config  — a COMPACT version of the YAML phase config produced
                             by a rule-based detector.  The config has been
                             compressed to reduce size:
                               • ``aliases`` lists are omitted (engine-only metadata;
                                 DO NOT add them back in your output).
                               • Operations with ``applied: false`` show
                                 ``details: {__false__: true}`` — keep this placeholder
                                 as-is unless you are flipping the operation to true.
                               • Long ``conditional_columns.conditions`` lists are
                                 truncated; the last entry is
                                 ``{__truncated__: N}`` where N is the original count.
                                 DO NOT emit this sentinel in your output — simply
                                 keep the conditions you can see.
                               • ``calculated_columns`` rules with confidence 0.0 and
                                 unknown formula show ``{__collapsed__: true}`` — keep
                                 them  as-is; the full rule will be restored
                                 automatically.
                             The Python execution engine restores the full structure
                             automatically after your response is received.

YOUR TASK:
  Review the auto-detected configuration against the actual data in input_df
  and output_df, then return a CORRECTED version.

CRITICAL VALIDATION RULES (APPLY THESE STRICTLY):

  ① FOR EVERY OPERATION MARKED "applied: true":
     - You MUST verify it actually occurs in the data transformation.
     - Compare input_df and output_df row-by-row and column-by-column.
     - If the transformation does NOT appear in the actual data, set applied: false.

  ② FOR FILTERS (phase_2_rows.filter):
     - These ARE valid transformations (they reduce row count).
     - Verify each filter rule by checking:
       • Does the rule's column exist in both input and output?
       • Do the output rows satisfy the filter condition?
       • Exclude serial/index columns (like "SL No" or "Employee Count") from filters.

  ③ FOR ENRICHMENT (phase_4_enrichment):
     - Only "applied: true" if actual new columns are added OR existing columns are modified.
     - text_operations: only if email/string values actually change to the mapped values.
     - Do NOT mark as applied if mapping is incomplete or doesn't match the output.
     
  ⑤ FOR CONDITIONAL COLUMNS (phase_4_enrichment.conditional_columns):
     - Verify conditions actually map to the output values
     - Do NOT mark a date column as conditional just because it has few unique values
     - Check if the values are actually derived from the source column conditions

  ⑥ FOR AGGREGATION (phase_5_aggregation):
     - Only "applied: true" if output has FEWER rows than input.
     - Verify that aggregation formulas (sum, avg, count, etc.) match the output values.
     - If output row count ≠ expected aggregated count, set applied: false.

  ⑦ FOR ADVANCED (phase_7_advanced.array_formulas):
     - Only "applied: true" if UNIQUE actually removes duplicates based on the columns.
     - Only "applied: true" if SORT actually reorders rows by the specified column.
     - Check the output_df row order and uniqueness.

  ⑧ ALWAYS SET TO FALSE (UI/constraint elements — never detectable from data):
     - phase_8_formatting.data_validation
     - phase_8_formatting.charts
     - phase_8_formatting.formatting
     - phase_8_formatting.conditional_formatting
     - phase_8_formatting.comments
     - phase_8_formatting.sparklines

  ⑨ FOR RESEQUENCE (phase_9_final.resequence) — CRITICAL RULE:
     This operation re-numbers a serial/index column (like "SL No", "Employee Count",
     "Row No", "Seq") from 1..N after rows are filtered.

     Set applied: true when ALL of the following hold:
       a. The output has FEWER rows than the input (row filtering occurred).
       b. A serial/index column exists in both input and output.
       c. The input serial column was a clean 1..M sequence (1, 2, 3, ..., M).
       d. The output serial column is NOT a clean 1..N sequence — it still holds
          the original sparse values (e.g. 1, 3, 4, 6, 7, ... instead of 1, 2, 3, ...).

     WHEN SET TO TRUE, YOU MUST GENERATE THE RULES:
       phase_9_final:
         resequence:
           applied: true
           details:
             rules:
               - column: "<MAPPED_OUTPUT_COLUMN_NAME>"    ← Use the output column name
                 start_at: 1
                 step: 1

     EXAMPLE FOR THIS SESSION:
       If input_df["SL No"] = [1,2,3,...,100] (clean)
       And output_df["Employee Count"] = [1,3,4,6,7,...] (sparse — OLD serial values)
       And input has 100 rows, output has 52 rows
       → MUST set:
           phase_9_final:
             resequence:
               applied: true
               details:
                 rules:
                   - column: "Employee Count"
                     start_at: 1
                     step: 1

     The serial_column_analysis section will tell you:
       • detected_serial_columns[] — list of detected serial columns
       • For each: input_sequence, output_sequence, input_is_clean_1_to_n, 
         output_is_sparse, etc.
       • resequence_needed: boolean flag indicating if resequence is needed

     If resequence_needed is true in serial_column_analysis, YOU MUST generate the rules.
     If the output serial column is ALREADY a clean 1..N sequence, set applied: false
     and omit the rules section.

THE CONFIG FORMAT MUST NOT CHANGE:
  You MUST:
  ① Return the corrected config as valid YAML inside a SINGLE ```yaml ... ``` fenced block.
  ② Preserve EXACTLY the same top-level keys as the auto-detected config.
  ③ Preserve EVERY existing sub-key inside each phase block.
  ④ Do NOT rename, add, or remove any keys.
  ⑤ The execution_order list MUST remain identical — do not modify it.
  ⑥ You may ONLY:
       • Set  applied: false  for operations that don't exist (false positives).
       • Set  applied: true   for operations that DO exist (and correct values).
       • Fix wrong values inside details blocks (especially unknown formulas!).
       • Add/remove individual entries in rules lists (but keep the list structure).

CORRECTION LOG:
  After the closing ``` fence, add a YAML comment block:
    # --- CORRECTIONS ---
    # [phase_9_final.resequence] Set applied: true with rule for "Employee Count"
    # [phase_7_advanced.array_formulas] Set applied: false (no actual UNIQUE/SORT transformation)
  If no corrections are needed, omit the block.

Return ONLY the ```yaml block (plus the optional comment block).  Nothing else."""

    def _build_user_context(self, context_payload: Dict[str, Any]) -> str:
        """Serialise the context payload into the user message string."""
        serialisable = make_serializable(context_payload)
        return (
            "Here is the full context for your review:\n\n"
            + json.dumps(serialisable, indent=2, ensure_ascii=False)
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Response parser & validator
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_summary(self, response: str) -> str:
        """Extract text after the closing ``` fence as the summary."""
        match = re.search(r"```[^`]*```\s*(.*)", response, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _parse_and_validate_response(
        self,
        response:        str,
        original_config: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[str], bool, str]:
        """
        Extract YAML from LLM response, parse it, and structurally validate.

        Returns:
            (corrected_config, corrections_list, is_valid, summary)
        """
        summary = self._extract_summary(response)
        yaml_text = self._extract_yaml_block(response)
        if not yaml_text:
            # Try parsing the whole response as YAML
            yaml_text = response.strip()
            if yaml_text.startswith("```"):
                yaml_text = yaml_text.split("```")[1]
                if yaml_text.startswith("yaml"):
                    yaml_text = yaml_text[4:]
        
        if not yaml_text:
            return original_config, ["No ```yaml block found in LLM response."], False, summary

        corrections_raw, yaml_clean = self._split_corrections_from_yaml(yaml_text)

        # Sanitize YAML before parsing to handle special characters
        yaml_clean = self._sanitize_yaml(yaml_clean)
        yaml_clean = self._fix_double_quoted_regex_backslashes(yaml_clean)
        

        try:
            corrected = yaml.safe_load(yaml_clean)
        except yaml.YAMLError as exc:
            print(f"[Agent] YAML parse error: {exc}")
            # Try to fix common YAML issues and retry
            yaml_fixed = self._attempt_yaml_fix(yaml_clean, exc)
            if yaml_fixed:
                try:
                    corrected = yaml.safe_load(yaml_fixed)
                    print("[Agent] ✅ YAML fixed and parsed successfully")
                except yaml.YAMLError as exc2:
                    print(f"[Agent] YAML still failed after fix attempt: {exc2}")
                    return original_config, [f"YAML parse error: {exc}"], False, summary
            else:
                return original_config, [f"YAML parse error: {exc}"], False, summary

        if not isinstance(corrected, dict):
            return original_config, ["Parsed YAML is not a dict."], False, summary

        # Restore metadata from original if missing (LLM often omits it)
        if "metadata" not in corrected and "metadata" in original_config:
            corrected["metadata"] = original_config["metadata"]

        # Check required keys (excluding metadata since we restore it)
        required_keys = {"preprocessing", "execution_order", "error_handling"}
        missing_keys = required_keys - set(corrected.keys())
        if missing_keys:
            print(f"[Agent] Missing required keys: {missing_keys}")
            return original_config, [f"Missing required keys: {missing_keys}"], False, summary

        # Validate structure - be lenient, only fail on critical errors
        issues = self._validate_structure(corrected, original_config)
        if issues:
            print(f"[Agent] Structure validation warnings: {issues}")
            # Only fail on critical errors like execution_order changes
            critical_errors = [e for e in issues if "execution_order was changed" in e]
            if critical_errors:
                return original_config, issues, False, summary
            # Non-critical issues: log but proceed

        # Expand any compressed sections back to full config
        corrected = self._expand_corrected_config(corrected, original_config)

        corrections = corrections_raw if corrections_raw else \
            self._diff_configs(original_config, corrected)

        return corrected, corrections, True, summary

    def _extract_yaml_block(self, text: str) -> Optional[str]:
        """Extract content inside the first ```yaml ... ``` fence."""
        match = re.search(r"```yaml\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if (match):
            return match.group(1).strip()
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        if (match):
            candidate = match.group(1).strip()
            if (":" in candidate):
                return candidate
        stripped = text.strip()
        if (stripped.startswith("preprocessing:") or stripped.startswith("execution_order:")):
            return stripped
        return None

    def _split_corrections_from_yaml(self, yaml_text: str) -> tuple[List[str], str]:
        """
        Split the `# --- CORRECTIONS ---` comment block from the YAML body.

        Returns:
            (corrections_list, clean_yaml_string)
        """
        lines = yaml_text.splitlines()
        yaml_lines:       List[str] = []
        correction_lines: List[str] = []
        in_corrections = False

        for line in lines:
            if re.match(r"#\s*---\s*CORRECTIONS", line, re.IGNORECASE):
                in_corrections = True
                continue
            if in_corrections:
                clean = re.sub(r"^#\s*", "", line).strip()
                if clean:
                    correction_lines.append(clean)
            else:
                yaml_lines.append(line)

        return correction_lines, "\n".join(yaml_lines)

    # ─────────────────────────────────────────────────────────────────────────
    # YAML Sanitization helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _fix_double_quoted_regex_backslashes(self, yaml_text: str) -> str:
        """
        Fix YAML double-quoted strings that contain regex/Python backreferences like \1, \2, \3.

        YAML double quotes treat backslash as an escape character, so \1 is invalid YAML.

        Converts:
        expression: "... r'\1****\3' ..."
        into:
        expression: "... r'\\1****\\3' ..."
        """

        def fix_line(line: str) -> str:
            # Only process lines that look like key: value
            if ':' not in line:
                return line

            colon_idx = self._find_yaml_colon(line)
            if colon_idx == -1:
                return line

            key_part = line[:colon_idx]
            rest = line[colon_idx + 1:]

            value = rest.strip()

            # Only fix double-quoted scalar values
            if not (value.startswith('"') and value.endswith('"')):
                return line

            # Escape invalid YAML backslash-number sequences: \1, \2, \3, etc.
            fixed_value = re.sub(
                r'(?<!\\)\\([0-9])',
                r'\\\\\1',
                value
            )

            # Also protect common regex escapes: \d, \D, \s, \S, \w, \W, \b, \B
            fixed_value = re.sub(
                r'(?<!\\)\\([dDsSwWbB])',
                r'\\\\\1',
                fixed_value
            )

            if fixed_value != value:
                leading_space = rest[:len(rest) - len(rest.lstrip())]
                return f"{key_part}:{leading_space}{fixed_value}"

            return line

        return "\n".join(fix_line(line) for line in yaml_text.splitlines())

    def _sanitize_yaml(self, yaml_text: str) -> str:
        """
        Sanitize YAML text to handle common LLM output issues including:
        - Smart/curly quotes
        - Invisible unicode characters
        - Unquoted special characters
        - Reserved words
        - Values starting with reserved indicators
        """
        # Replace smart/curly quotes with regular quotes
        yaml_text = yaml_text.replace('“', '"').replace('”', '"')
        yaml_text = yaml_text.replace(''', "'").replace(''', "'")
        # Replace other problematic unicode
        yaml_text = yaml_text.replace('…', '...')
        yaml_text = yaml_text.replace('–', '-').replace('—', '-')
        
        lines = yaml_text.split('\n')
        sanitized_lines = []
        
        # Characters that require quoting in YAML values
        special_chars = set(':{}[],%&*?|<>=!@`#\\"\'\'\n\t')
        # Reserved words that need quoting
        reserved_words = {'true', 'false', 'yes', 'no', 'on', 'off', 'null', '~', 'none'}
        
        for line in lines:
            # Remove invisible/non-printable unicode (except tab)
            line = ''.join(c for c in line if c.isprintable() or c == '\t')
            
            # Skip empty lines and comments
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                sanitized_lines.append(line)
                continue
            
            # Handle key: value lines (but not list items that start with -)
            if ':' in line and not stripped.startswith('-'):
                # Find the first colon that's not inside quotes
                colon_idx = self._find_yaml_colon(line)
                if colon_idx != -1:
                    key_part = line[:colon_idx]
                    rest = line[colon_idx + 1:]
                    
                    if rest:  # Has a value after colon
                        value = rest.strip()
                        # Preserve leading space after colon
                        leading_space = ' ' if rest and rest[0] == ' ' else ''
                        
                        # Check if value needs quoting
                        needs_quote = False
                        if value and not self._is_already_quoted(value):
                            # Check for special characters
                            if any(c in value for c in special_chars):
                                needs_quote = True
                            # Check for reserved words
                            elif value.lower() in reserved_words:
                                needs_quote = True
                            # Check for values starting with reserved indicators
                            elif value and value[0] in '@`&*!|>':
                                needs_quote = True
                            # Check for numeric strings that might be misinterpreted
                            elif self._looks_like_unintended_conversion(value):
                                needs_quote = True
                        
                        if needs_quote:
                            # Escape internal quotes and wrap
                            value = self._safe_quote(value)
                            line = f"{key_part}:{leading_space}{value}"
            
            sanitized_lines.append(line)
        
        return '\n'.join(sanitized_lines)

    def _find_yaml_colon(self, line: str) -> int:
        """
        Find the index of the first colon that acts as a key-value separator.
        Returns -1 if no such colon is found.
        """
        in_single_quote = False
        in_double_quote = False
        
        for i, c in enumerate(line):
            if c == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif c == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif c == ':' and not in_single_quote and not in_double_quote:
                return i
        
        return -1

    def _is_already_quoted(self, value: str) -> bool:
        """
        Check if value is already properly quoted or is a YAML structure.
        """
        if not value:
            return False
        # Already quoted
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return True
        # Is a list, dict, multiline indicator, or list item
        if value.startswith(('[', '{', '-', '|', '>')):
            return True
        # Is a number (int or float)
        try:
            float(value)
            return True
        except ValueError:
            pass
        return False

    def _safe_quote(self, value: str) -> str:
        """
        Safely quote a value, escaping internal quotes as needed.
        """
        # If it contains double quotes but no single quotes, use single quotes
        if '"' in value and "'" not in value:
            return f"'{value}'"
        # If it contains single quotes but no double quotes, use double quotes
        elif "'" in value and '"' not in value:
            return f'"{value}"'
        # If it contains both, escape double quotes and use double quotes
        elif '"' in value and "'" in value:
            escaped = value.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'
        else:
            # Default to double quotes
            return f'"{value}"'

    def _looks_like_unintended_conversion(self, value: str) -> bool:
        """
        Check if YAML might auto-convert this value unexpectedly.
        """
        # Octal-like numbers (e.g., 0755)
        if re.match(r'^0\d+$', value):
            return True
        # Sexagesimal time notation (e.g., 1:30 interpreted as 90)
        if re.match(r'^\d+:\d+', value):
            return True
        # Scientific notation that might be a string
        if re.match(r'^\d+[eE]\d+$', value):
            return True
        # Date-like strings (e.g., 2024-01-01)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', value):
            return True
        # Version-like strings (e.g., 1.0.0)
        if re.match(r'^\d+\.\d+\.\d+', value):
            return True
        return False

    def _attempt_yaml_fix(self, yaml_text: str, error: yaml.YAMLError) -> Optional[str]:
        """
        Attempt to fix common YAML parsing issues based on the error.
        Tries multiple strategies to recover from parse errors.
        """
        lines = yaml_text.split('\n')
        
        try:
            # Strategy 1: Fix the specific line mentioned in the error
            if hasattr(error, 'problem_mark') and error.problem_mark:
                line_num = error.problem_mark.line
                
                if 0 <= line_num < len(lines):
                    problem_line = lines[line_num]
                    fixed_line = self._fix_problem_line(problem_line)
                    
                    if fixed_line != problem_line:
                        lines[line_num] = fixed_line
                        # Try parsing to see if this fix worked
                        fixed_yaml = '\n'.join(lines)
                        try:
                            yaml.safe_load(fixed_yaml)
                            return fixed_yaml
                        except yaml.YAMLError:
                            # Reset and try other strategies
                            lines[line_num] = problem_line
                
                # Strategy 2: Also check the line before (often the actual problem)
                if line_num > 0:
                    prev_line = lines[line_num - 1]
                    fixed_prev = self._fix_problem_line(prev_line)
                    if fixed_prev != prev_line:
                        lines[line_num - 1] = fixed_prev
                        fixed_yaml = '\n'.join(lines)
                        try:
                            yaml.safe_load(fixed_yaml)
                            return fixed_yaml
                        except yaml.YAMLError:
                            lines[line_num - 1] = prev_line
            
            # Strategy 3: Aggressive quoting of all unquoted values
            aggressive_lines = []
            for line in yaml_text.split('\n'):
                aggressive_lines.append(self._aggressive_quote_line(line))
            
            aggressive_yaml = '\n'.join(aggressive_lines)
            try:
                yaml.safe_load(aggressive_yaml)
                return aggressive_yaml
            except yaml.YAMLError:
                pass
            
            # Strategy 4: Remove problematic lines entirely (last resort)
            if hasattr(error, 'problem_mark') and error.problem_mark:
                line_num = error.problem_mark.line
                if 0 <= line_num < len(lines):
                    # Check if the line is a non-essential comment or empty-ish
                    stripped = lines[line_num].strip()
                    if not stripped or stripped.startswith('#'):
                        del lines[line_num]
                        return '\n'.join(lines)
            
            return None
            
        except Exception as e:
            print(f"[Agent] Error during YAML fix attempt: {e}")
            return None

    def _fix_problem_line(self, line: str) -> str:
        """
        Try to fix a single problematic line by quoting its value.
        """
        stripped = line.strip()
        
        # Skip comments and empty lines
        if not stripped or stripped.startswith('#'):
            return line
        
        # Handle key: value lines
        if ':' in line and not stripped.startswith('-'):
            colon_idx = self._find_yaml_colon(line)
            if colon_idx != -1:
                key_part = line[:colon_idx]
                rest = line[colon_idx + 1:]
                
                if rest.strip():  # Has a value
                    value = rest.strip()
                    leading_space = ' ' if rest and rest[0] == ' ' else ''
                    
                    # Force quote if not already quoted
                    if not self._is_already_quoted(value):
                        value = self._safe_quote(value)
                        return f"{key_part}:{leading_space}{value}"
        
        return line

    def _aggressive_quote_line(self, line: str) -> str:
        """
        Aggressively quote any unquoted value in a line.
        Used as a last-resort fix strategy.
        """
        stripped = line.strip()
        
        # Skip comments, empty lines, and list markers
        if not stripped or stripped.startswith('#') or stripped == '-':
            return line
        
        # Handle list items with values (- key: value or - value)
        if stripped.startswith('- '):
            indent = len(line) - len(line.lstrip())
            content = stripped[2:]
            
            if ':' in content:
                # It's a dict item in a list
                colon_idx = content.index(':')
                key = content[:colon_idx]
                value = content[colon_idx + 1:].strip()
                
                if value and not self._is_already_quoted(value):
                    value = self._safe_quote(value)
                    return ' ' * indent + f'- {key}: {value}'
            else:
                # It's a simple list value
                if content and not self._is_already_quoted(content):
                    content = self._safe_quote(content)
                    return ' ' * indent + f'- {content}'
            
            return line
        
        # Handle regular key: value
        if ':' in line:
            colon_idx = self._find_yaml_colon(line)
            if colon_idx != -1:
                key_part = line[:colon_idx]
                rest = line[colon_idx + 1:]
                
                if rest.strip():
                    value = rest.strip()
                    leading_space = ' ' if rest and rest[0] == ' ' else ''
                    
                    if not self._is_already_quoted(value):
                        value = self._safe_quote(value)
                        return f"{key_part}:{leading_space}{value}"
        
        return line

    def _validate_structure(
        self,
        corrected: Dict[str, Any],
        original:  Dict[str, Any],
    ) -> List[str]:
        """
        Validate that corrected config preserves the structural contract.

        Returns a list of violation strings (empty = valid).
        """
        issues: List[str] = []

        for key in _REQUIRED_TOP_LEVEL_KEYS:
            if key not in corrected:
                issues.append(f"Missing required top-level key: '{key}'")

        original_keys  = set(original.keys())
        corrected_keys = set(corrected.keys())
        added = corrected_keys - original_keys
        if added:
            issues.append(f"LLM introduced unexpected top-level keys: {added}")

        for phase_key in original_keys & _PHASE_KEYS:
            if (phase_key not in corrected):
                issues.append(f"Phase block removed by LLM: '{phase_key}'")
                continue
            orig_sub  = set(original[phase_key].keys())  if isinstance(original.get(phase_key),  dict) else set()
            corr_sub  = set(corrected[phase_key].keys()) if isinstance(corrected.get(phase_key), dict) else set()
            added_sub = corr_sub - orig_sub
            if added_sub:
                issues.append(
                    f"LLM added unexpected sub-keys to '{phase_key}': {added_sub}"
                )

        orig_eo = original.get("execution_order", [])
        corr_eo = corrected.get("execution_order", [])
        if (orig_eo != corr_eo):
            issues.append(
                f"execution_order was changed by LLM. "
                f"Original: {orig_eo}  Corrected: {corr_eo}"
            )

        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Diff helper
    # ─────────────────────────────────────────────────────────────────────────

    def _diff_configs(
        self,
        original:  Dict[str, Any],
        corrected: Dict[str, Any],
        path:      str = "",
    ) -> List[str]:
        """
        Recursively diff two config dicts and return a human-readable
        list of changes.  Limited to 50 entries to keep the output concise.
        """
        changes: List[str] = []

        for key in set(list(original.keys()) + list(corrected.keys())):
            full_path = f"{path}.{key}" if path else key
            orig_val  = original.get(key)
            corr_val  = corrected.get(key)

            if orig_val == corr_val:
                continue

            if isinstance(orig_val, dict) and isinstance(corr_val, dict):
                sub = self._diff_configs(orig_val, corr_val, full_path)
                changes.extend(sub)
            elif key not in original:
                changes.append(f"[{full_path}] ADDED")
            elif key not in corrected:
                changes.append(f"[{full_path}] REMOVED")
            else:
                def _fmt(v: Any) -> str:
                    s = str(v)
                    return s if len(s) <= 80 else s[:77] + "…"
                changes.append(f"[{full_path}] {_fmt(orig_val)} → {_fmt(corr_val)}")

            if len(changes) >= 50:
                changes.append("… (more changes truncated)")
                break

        return changes

    # ─────────────────────────────────────────────────────────────────────────
    # Helper: load full DataFrame from disk
    # ─────────────────────────────────────────────────────────────────────────

    def _load_full_df(
        self,
        file_path:  str,
        sheet_name: str,
        label:      str,
    ) -> pd.DataFrame:
        """
        Load the complete DataFrame from the given file path.
        Returns an empty DataFrame on any error (non-fatal).
        """
        try:
            p      = Path(file_path)
            suffix = p.suffix.lower()
            if suffix in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}:
                # Try with specified sheet_name first
                try:
                    return pd.read_excel(str(p), sheet_name=sheet_name)
                except ValueError as e:
                    if "Worksheet named" in str(e) and "not found" in str(e):
                        # Sheet not found, try loading the first sheet
                        print(f"[Agent] ⚠️  Sheet '{sheet_name}' not found in {label} file, loading first sheet instead.")
                        sheets = pd.read_excel(str(p), sheet_name=None)
                        if sheets:
                            first_sheet_name = list(sheets.keys())[0]
                            return sheets[first_sheet_name]
                        else:
                            return pd.DataFrame()
                    else:
                        raise e
            elif suffix in {".csv", ".tsv"}:
                sep = "\t" if suffix == ".tsv" else ","
                return pd.read_csv(str(p), sep=sep)
            else:
                print(f"[Agent] ⚠️  Unsupported file type '{suffix}' for {label} file.")
                return pd.DataFrame()
        except Exception as exc:
            print(f"[Agent] ⚠️  Could not load {label} DataFrame from '{file_path}': {exc}")
            return pd.DataFrame()

    # ─────────────────────────────────────────────────────────────────────────
    # Helper: save corrected config to disk
    # ─────────────────────────────────────────────────────────────────────────

    def _save_corrected_yaml(
        self,
        session_id:       str,
        corrected_config: Dict[str, Any],
    ) -> str:
        """
        Save corrected config to
            data/generated_configs/<session_id>_corrected_config.yaml
        """
        configs_dir = (
            Path(__file__).resolve().parent.parent
            / "data" / "generated_configs"
        )
        configs_dir.mkdir(parents=True, exist_ok=True)

        save_path = configs_dir / f"{session_id}_corrected_config.yaml"
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(
                make_serializable(corrected_config),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        return str(save_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Misc helpers
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # Config compression / expansion
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _compress_config_for_llm(
        config:         Dict[str, Any],
        max_conditions: int = 5,
    ) -> Dict[str, Any]:
        """
        Produce a token-efficient version of the phase config for the LLM prompt.

        Transformations applied:
          1. ``aliases`` arrays are stripped from every column object — the LLM
             only needs the canonical column **name**.
          2. Operations with ``applied: false`` have their ``details`` replaced
             with ``{"__false__": true}`` — only the flag matters to the LLM.
          3. ``conditional_columns`` condition lists longer than *max_conditions*
             are truncated; a sentinel entry ``{"__truncated__": N}`` is appended
             so the LLM knows the full list is preserved.
          4. ``calculated_columns`` rules with ``confidence: 0.0`` and
             ``expression: unknown`` are collapsed — only the column name and
             metadata are kept.
          5. The ``metadata`` section is dropped (column_mapping is already sent
             separately in the context payload).

        After the LLM returns a corrected version, call
        ``_expand_corrected_config`` to restore the stripped data.
        """
        import copy

        def _strip_aliases(obj: Any) -> Any:
            """Recursively remove 'aliases' keys from dicts."""
            if isinstance(obj, dict):
                return {
                    k: _strip_aliases(v)
                    for k, v in obj.items()
                    if k != "aliases"
                }
            if isinstance(obj, list):
                return [_strip_aliases(i) for i in obj]
            return obj

        cfg = copy.deepcopy(config)

        # ── Drop metadata (redundant in the prompt) ───────────────────────
        cfg.pop("metadata", None)

        for phase_key in _PHASE_KEYS:
            phase = cfg.get(phase_key)
            if not isinstance(phase, dict):
                continue

            for op_key, op_val in phase.items():
                if not isinstance(op_val, dict):
                    continue

                applied = op_val.get("applied", False)

                # ── False operations: drop details entirely ───────────────
                if not applied:
                    op_val["details"] = {"__false__": True}
                    continue

                details = op_val.get("details", {})
                if not isinstance(details, dict):
                    continue

                # ── conditional_columns: truncate conditions ──────────────
                if op_key == "conditional_columns":
                    rules = details.get("rules", [])
                    for rule in rules:
                        conds = rule.get("conditions", [])
                        if len(conds) > max_conditions:
                            total = len(conds)
                            rule["conditions"] = (
                                conds[:max_conditions]
                                + [{"__truncated__": total}]
                            )

                # ── calculated_columns: DO NOT collapse unknowns ─────────
                # Let the LLM see unknown formulas so it can infer them from data
                elif op_key == "calculated_columns":
                    # Keep all rules as-is - LLM needs to see unknowns to fix them
                    pass

        # ── Strip all aliases after phase-level edits ─────────────────────
        cfg = _strip_aliases(cfg)

        return cfg

    @staticmethod
    def _expand_corrected_config(
        corrected: Dict[str, Any],
        original:  Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Restore the fields that were stripped by ``_compress_config_for_llm``.

        Restoration rules (applied by matching phase → operation → column name):

          1. **aliases**       — always restored from *original* for every column
             object (matched by ``name``).
          2. **False-op details** — when an operation still has
             ``{"__false__": true}`` in *details*, or the LLM left it as-is,
             the original ``details`` block is restored.
          3. **Truncated conditions** — when a ``conditions`` list ends with a
             ``{"__truncated__": N}`` sentinel, the full conditions list is
             restored from *original* (the LLM's edits to the first few entries
             are preserved; the rest are taken verbatim from original).
          4. **Collapsed calculated_columns** — rules flagged with
             ``__collapsed__: true`` are restored from original.
          5. **metadata** — always restored wholesale from *original* (it was
             stripped before sending to the LLM).
        """
        import copy

        result = copy.deepcopy(corrected)

        # ── 1. Always restore metadata from original ───────────────────────
        if "metadata" in original:
            result["metadata"] = copy.deepcopy(original["metadata"])

        # ── Build alias lookup: column_name → aliases list ─────────────────
        def _collect_aliases(cfg: Dict[str, Any]) -> Dict[str, List]:
            """Walk the config and build {col_name: aliases} from original."""
            alias_map: Dict[str, List] = {}

            def _walk(obj: Any) -> None:
                if isinstance(obj, dict):
                    if "name" in obj and "aliases" in obj:
                        alias_map[obj["name"]] = obj["aliases"]
                    for v in obj.values():
                        _walk(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _walk(item)

            _walk(cfg)
            return alias_map

        alias_map = _collect_aliases(original)

        def _restore_aliases(obj: Any) -> Any:
            """Add back aliases to every column-like dict (has 'name' key)."""
            if isinstance(obj, dict):
                out = {k: _restore_aliases(v) for k, v in obj.items()}
                if "name" in out and "aliases" not in out:
                    col_name = out["name"]
                    if col_name in alias_map:
                        out["aliases"] = alias_map[col_name]
                return out
            if isinstance(obj, list):
                return [_restore_aliases(i) for i in obj]
            return obj

        result = _restore_aliases(result)

        # ── Build original op-details lookup ──────────────────────────────
        # orig_ops[phase_key][op_key] = original details dict
        orig_ops: Dict[str, Dict[str, Any]] = {}
        for phase_key in _PHASE_KEYS:
            orig_phase = original.get(phase_key, {})
            if isinstance(orig_phase, dict):
                orig_ops[phase_key] = {
                    op_key: copy.deepcopy(op_val)
                    for op_key, op_val in orig_phase.items()
                    if isinstance(op_val, dict)
                }

        for phase_key in _PHASE_KEYS:
            res_phase = result.get(phase_key)
            if not isinstance(res_phase, dict):
                continue

            for op_key, op_val in res_phase.items():
                if not isinstance(op_val, dict):
                    continue

                orig_op = orig_ops.get(phase_key, {}).get(op_key, {})
                details = op_val.get("details", {})

                # ── 2. Restore false-op details ───────────────────────────
                if isinstance(details, dict) and details.get("__false__"):
                    op_val["details"] = copy.deepcopy(
                        orig_op.get("details", {})
                    )
                    continue  # nothing more to do for this op

                # ── 3. Restore truncated conditions ───────────────────────
                if op_key == "conditional_columns" and isinstance(details, dict):
                    rules     = details.get("rules", [])
                    orig_rules = orig_op.get("details", {}).get("rules", [])

                    # Build lookup: output column name → original rule
                    orig_rule_map = {
                        r.get("column", {}).get("name", ""): r
                        for r in orig_rules
                        if isinstance(r, dict)
                    }

                    for rule in rules:
                        conds = rule.get("conditions", [])
                        if not conds:
                            continue
                        last = conds[-1]
                        if isinstance(last, dict) and "__truncated__" in last:
                            col_name = rule.get("column", {}).get("name", "")
                            orig_rule = orig_rule_map.get(col_name, {})
                            orig_conds = orig_rule.get("conditions", [])
                            # Keep the LLM-edited head; replace everything from
                            # the truncation point onward with the original tail.
                            head_count = len(conds) - 1  # exclude sentinel
                            rule["conditions"] = (
                                conds[:head_count]
                                + orig_conds[head_count:]
                            )

                # ── 4. Restore collapsed calculated_columns ───────────────
                if op_key == "calculated_columns" and isinstance(details, dict):
                    rules      = details.get("rules", [])
                    orig_rules = orig_op.get("details", {}).get("rules", [])

                    # Build lookup: column name + execution_order → original rule
                    orig_rule_map = {
                        (r.get("column", {}).get("name", ""),
                         r.get("execution_order")): r
                        for r in orig_rules
                        if isinstance(r, dict)
                    }

                    restored_rules = []
                    for rule in rules:
                        if isinstance(rule, dict) and rule.get("__collapsed__"):
                            key = (
                                rule.get("column", {}).get("name", ""),
                                rule.get("execution_order"),
                            )
                            orig_rule = orig_rule_map.get(key)
                            if orig_rule:
                                restored_rules.append(copy.deepcopy(orig_rule))
                            else:
                                # Fallback: drop the sentinel flag and keep as-is
                                rule.pop("__collapsed__", None)
                                restored_rules.append(rule)
                        else:
                            restored_rules.append(rule)
                    details["rules"] = restored_rules

        return result

    @staticmethod
    def _active_phases(config: Dict[str, Any]) -> List[str]:
        """Return the list of phase keys that have at least one applied op."""
        active = []
        for phase_key in _PHASE_KEYS:
            body = config.get(phase_key, {})
            if isinstance(body, dict):
                if any(
                    isinstance(v, dict) and v.get("applied") is True
                    for v in body.values()
                ):
                    active.append(phase_key)
        return active

    @staticmethod
    def _error_result(
        session_id:     str,
        config:         Dict[str, Any],
        column_mapping: Dict[str, str],
        error_msg:      str,
    ) -> Dict[str, Any]:
        return {
            "session_id":         session_id,
            "original_config":    config,
            "corrected_config":   config,
            "column_mapping":     column_mapping,
            "corrections_made":   [error_msg],
            "correction_success": False,
            "config_saved_to":    None,
            "summary":            f"[PhaseTransformationAgent] ERROR: {error_msg}",
        }

    @staticmethod
    def _build_summary(
        session_id:       str,
        config_result:    ConfigurationResult,
        corrections_made: List[str],
        success:          bool,
        saved_path:       Optional[str],
        timings:          Optional[Dict[str, float]] = None,
    ) -> str:
        lines = [
            "=" * 65,
            f"  PhaseTransformationAgent — {'✅ SUCCESS' if success else '⚠️  FALLBACK TO ORIGINAL'}",
            f"  Session : {session_id}",
            "-" * 65,
            f"  Input   : {config_result.input_file}  {config_result.input_shape}",
            f"  Output  : {config_result.output_file}  {config_result.output_shape}",
            f"  Sheet   : {config_result.sheet_name}",
            f"  Renames : {len(config_result.column_mapping)}",
            "",
            f"  Corrections ({len(corrections_made)}):",
        ]
        for c in corrections_made[:20]:
            lines.append(f"    • {c}")
        if len(corrections_made) > 20:
            lines.append(f"    … and {len(corrections_made) - 20} more")
        if not corrections_made:
            lines.append("    (none — config unchanged)")
        if saved_path:
            lines.append("")
            lines.append(f"  Saved → {saved_path}")

        # ── Timing table ──────────────────────────────────────────────────
        if timings:
            lines.append("")
            lines.append("  ⏱  Timings:")
            _labels = {
                "1_get_configuration":        "  GetConfigurationService  ",
                "2_load_dataframes":          "  Load DataFrames          ",
                "3_build_context":            "  Build LLM context        ",
                "4_llm_correction":           "  LLM correction (total)   ",
                "5_programmatic_corrections": "  Programmatic corrections ",
                "6_save_yaml":                "  Save corrected YAML      ",
                "total":                      "  ── TOTAL ──              ",
            }
            for key in timings:
                if key in _labels:
                    lines.append(f"    {_labels[key]}  {timings[key]:>7.3f}s")
            # Per-attempt LLM breakdown
            attempt_keys = sorted(k for k in timings if k.startswith("4_llm_attempt_"))
            if attempt_keys:
                lines.append("    ── LLM attempt breakdown ──")
                for k in attempt_keys:
                    n = k.split("_")[-1]
                    lines.append(f"      Attempt {n}  LLM call   {timings[k]:>7.3f}s")
                    pk = f"4_parse_attempt_{n}"
                    if pk in timings:
                        lines.append(f"      Attempt {n}  parse/val  {timings[pk]:>7.3f}s")

        lines.append("=" * 65)
        return "\n".join(lines)

    @staticmethod
    def _analyze_serial_columns(
        df_in:          pd.DataFrame,
        df_out:         pd.DataFrame,
        column_mapping: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Detect serial/index columns and determine if resequence is needed.

        df_in  : raw input DataFrame (original column names, e.g. 'SL No').
        df_out : raw output DataFrame (output column names, e.g. 'Employee Count').
        column_mapping: {input_col → output_col}

        Strategy
        --------
        For each numeric column in df_in that looks like a serial index:
          1. Find its counterpart in df_out via column_mapping OR by name match.
          2. Check if input is a clean 1..M sequence.
          3. Check if output is NOT a clean 1..N sequence (sparse).
          4. If both hold and rows were filtered → resequence_needed = True.
        """
        analysis: Dict[str, Any] = {
            "detected_serial_columns": [],
            "resequence_needed": False,
            "resequence_reason": "",
        }

        if df_in.empty or df_out.empty:
            return analysis

        # Serial column name hints (lowercase)
        serial_hints = {
            "sl no", "sl.", "sno", "s no", "serial no", "serial number",
            "row no", "row number", "row num", "row_id",
            "employee count", "emp count", "record no", "record number",
            "entry no", "item no", "line no", "ref no",
            "no.", "num", "seq", "sequence", "index", "idx", "id",
        }

        # Invert mapping: output_col → input_col
        inv_mapping: Dict[str, str] = {v: k for k, v in column_mapping.items()}

        for col in df_in.columns:
            col_lower = str(col).lower().strip()

            # --- Find matching output column ---
            # Priority 1: direct column_mapping lookup
            out_col = column_mapping.get(col)
            # Priority 2: same name exists in output
            if out_col is None and col in df_out.columns:
                out_col = col
            # Priority 3: skip if no match found
            if out_col is None or out_col not in df_out.columns:
                continue

            # --- Must be numeric in input ---
            in_vals = pd.to_numeric(df_in[col], errors="coerce")
            if in_vals.isna().mean() > 0.1:
                continue  # too many non-numeric → not a serial col

            # --- Name hint OR value pattern check ---
            name_is_serial = any(hint in col_lower for hint in serial_hints)

            in_ints = in_vals.dropna().astype(int)
            if len(in_ints) < 2:
                continue

            in_sorted = sorted(in_ints.tolist())
            in_is_clean = (in_sorted == list(range(1, len(in_sorted) + 1))
                           or in_sorted == list(range(len(in_sorted))))
            value_is_serial = in_is_clean

            if not (name_is_serial or value_is_serial):
                continue  # not a serial column

            # --- Check output sequence ---
            out_vals = pd.to_numeric(df_out[out_col], errors="coerce")
            if out_vals.isna().mean() > 0.1:
                continue

            out_ints  = out_vals.dropna().astype(int)
            out_sorted = sorted(out_ints.tolist())
            out_is_clean = (out_sorted == list(range(1, len(out_sorted) + 1))
                            or out_sorted == list(range(len(out_sorted))))
            out_is_sparse = not out_is_clean

            col_analysis = {
                "column_name":         col,
                "mapped_output_name":  out_col,
                "input_sequence":      in_sorted[:10] + (["..."] if len(in_sorted) > 10 else []),
                "input_is_clean_1_to_n": in_is_clean,
                "output_sequence":     out_sorted[:10] + (["..."] if len(out_sorted) > 10 else []),
                "output_is_clean_1_to_n": out_is_clean,
                "output_is_sparse":    out_is_sparse,
                "input_count":         len(in_ints),
                "output_count":        len(out_ints),
            }
            analysis["detected_serial_columns"].append(col_analysis)

            # --- Resequence decision ---
            if in_is_clean and out_is_sparse and len(in_ints) > len(out_ints):
                analysis["resequence_needed"] = True
                analysis["resequence_reason"] = (
                    f"Input '{col}' is clean 1..{len(in_ints)}, "
                    f"output '{out_col}' is sparse {out_sorted[:5]}..., "
                    f"needs renumbering to 1..{len(out_ints)}"
                )
                print(f"  [serial_analysis] ✅ resequence needed: {analysis['resequence_reason']}")
            else:
                print(f"  [serial_analysis] '{col}' → '{out_col}': "
                      f"in_clean={in_is_clean} out_sparse={out_is_sparse} "
                      f"in_count={len(in_ints)} out_count={len(out_ints)}")

        return analysis

    @staticmethod
    def _apply_programmatic_corrections(
        config:                 Dict[str, Any],
        serial_column_analysis: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[str]]:
        """
        Apply corrections that are 100% deterministic and do NOT need the LLM.

        Currently handles:
          • resequence — set applied:true + rules when resequence_needed is True
            and the config does not already have it set correctly.

        Returns (patched_config, corrections_list).
        """
        import copy
        config = copy.deepcopy(config)
        corrections: List[str] = []

        # ── Resequence ────────────────────────────────────────────────────────
        if serial_column_analysis.get("resequence_needed"):
            reseq = config.get("phase_9_final", {}).get("resequence", {})

            # Build rules from detected serial columns
            rules = []
            for col_info in serial_column_analysis.get("detected_serial_columns", []):
                if col_info.get("input_is_clean_1_to_n") and col_info.get("output_is_sparse"):
                    rules.append({
                        "column":   col_info["mapped_output_name"],
                        "start_at": 1,
                        "step":     1,
                    })

            if rules and not reseq.get("applied"):
                # Ensure the entire path exists before writing into it
                if "phase_9_final" not in config:
                    config["phase_9_final"] = {}
                if "resequence" not in config["phase_9_final"]:
                    config["phase_9_final"]["resequence"] = {}
                if "details" not in config["phase_9_final"]["resequence"]:
                    config["phase_9_final"]["resequence"]["details"] = {}

                config["phase_9_final"]["resequence"]["applied"] = True
                config["phase_9_final"]["resequence"]["details"]["rules"] = rules
                col_names = [r["column"] for r in rules]
                corrections.append(
                    f"[phase_9_final.resequence] Set applied:true — "
                    f"programmatic correction for columns {col_names} "
                    f"({serial_column_analysis['resequence_reason']})"
                )
                print(f"  [programmatic] ✅ Resequence patched for {col_names}")

        return config, corrections
