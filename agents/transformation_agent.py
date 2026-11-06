# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detection agent (rule inventory, task 20). REUSABLE.
# Rework: produce the transformation inventory/IR; drop bespoke YAML final-config output.
# ============================================================================
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
import yaml

from states.final_analysis_state import FinalAnalysisState
from services.transformation_service import run_transformation_detection, TransformationResult
from services.llm_transformation import detect_transformations_for_session
from llm_client import LLMClient
# from prompts.prompt_generate_final_config import GENERATE_FINAL_CONFIG_PROMPT, GENERATE_FINAL_CONFIG_USER_PROMPT
from utils.serializer import make_serializable
from prompts.config_summary_combined import GENERATE_FINAL_CONFIG_SUMMARY_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# Config storage directory
# ─────────────────────────────────────────────────────────────────────────────

_ROOT        = Path(__file__).resolve().parent.parent
_CONFIGS_DIR = _ROOT / "data" / "generated_configs"


class TransformationAgent:
    """Agent responsible for running column/sheet/relationship analysis.

    Expects a prepared FinalAnalysisState (e.g. from FileAnalysisAgent)
    together with the input and output DataFrames, then delegates the full
    detection pipeline to TransformationService and returns a
    TransformationResult.
    """

    def __init__(self) -> None:
        pass

    async def analyze(
        self,
        file_analysis_state: FinalAnalysisState,
        input_df:            pd.DataFrame,
        output_df:           pd.DataFrame,
        save_config:         bool = True,
    ) -> TransformationResult:
        """Run full transformation detection and return a TransformationResult."""

        return run_transformation_detection(
            file_analysis_state = file_analysis_state,
            input_df            = input_df,
            output_df           = output_df,
            save_config         = save_config,
        )

    async def generate_final_llm_config(
        self,
        session_id: str,
        detection_response: str,
        config_result: TransformationResult,
        input_df: pd.DataFrame,
        output_df: pd.DataFrame,
        llm_client: Optional[LLMClient] = None,
        max_tokens: int = 16000,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Generate final configuration by merging transformation detection results
        with initial configuration using LLM analysis.

        Takes the response from detect_transformations_for_session function and
        the initial config_result, then generates a complete, executable final
        configuration with:
        - Correct column mappings (output_column -> input_column)
        - Complete transformation parameters
        - Exact formulas analyzed with column names
        - All missing details filled in based on data analysis
        - Everything marked as final working JSON/YAML

        Args:
            session_id: The session ID
            detection_response: Output from detect_transformations_for_session
            config_result: TransformationResult with initial config
            input_df: Input DataFrame
            output_df: Output DataFrame
            llm_client: Optional LLMClient instance (creates new if not provided)
            max_tokens: Max tokens for LLM response
            timeout: Timeout in seconds

        Returns:
            Dict containing:
            - final_config: The complete final configuration
            - config_saved_to: Path where the YAML was saved
            - llm_response: Raw LLM response for debugging
        """
        # Initialize LLM client if not provided
        if llm_client is None:
            llm_client = LLMClient(session_id=session_id)

        # ── Build context payload ──────────────────────────────────────────────
        print("[TransformationAgent] Building LLM context for final config generation...")

        # Prepare DataFrames info
        input_columns_list = ", ".join(input_df.columns.tolist())
        output_columns_list = ", ".join(output_df.columns.tolist())
        input_dtypes_dict = {col: str(input_df[col].dtype) for col in input_df.columns}
        output_dtypes_dict = {col: str(output_df[col].dtype) for col in output_df.columns}

        # Sample data - include head + middle + tail to show complete sort patterns
        input_data_sample = input_df.head(10).to_markdown(index=False) or ""
        output_data_sample = self._get_strategic_output_sample(output_df)

        # Convert config_result to YAML string for context
        config_result_dict = {
            "session_id": session_id,
            "config": config_result.config,
            "column_mapping": config_result.column_mapping,
            "generated_at": config_result.generated_at,
        }
        config_result_yaml = yaml.dump(
            make_serializable(config_result_dict),
            default_flow_style=False,
            sort_keys=False,
        )
        # Escape braces for .format()
        config_result_yaml = config_result_yaml.replace('{', '{{').replace('}', '}}')
        
        # Convert dicts to JSON and escape braces
        input_dtypes_json = json.dumps(input_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')
        output_dtypes_json = json.dumps(output_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')
        
        # Convert detection response to string and escape braces
        detection_response_str = str(detection_response).replace('{', '{{').replace('}', '}}')
        
        # Escape braces in data samples
        input_data_sample_escaped = input_data_sample.replace('{', '{{').replace('}', '}}')
        output_data_sample_escaped = output_data_sample.replace('{', '{{').replace('}', '}}')

        # Build user prompt
        user_prompt = GENERATE_FINAL_CONFIG_USER_PROMPT.format(
            detection_response=detection_response_str,
            config_result_yaml=config_result_yaml,
            input_columns_list=input_columns_list,
            input_row_count=len(input_df),
            input_dtypes_dict=input_dtypes_json,
            output_columns_list=output_columns_list,
            output_row_count=len(output_df),
            output_dtypes_dict=output_dtypes_json,
            input_data_sample=input_data_sample_escaped,
            output_data_sample=output_data_sample_escaped,
            session_id=session_id,
        )

        # ── Call LLM ────────────────────────────────────────────────────────────
        print("[TransformationAgent] Calling LLM to generate final configuration...")
        llm_response = await llm_client.generate_long_response(
            system_prompt=GENERATE_FINAL_CONFIG_SUMMARY_PROMPT,
            context=user_prompt,
            max_tokens=max_tokens,
            timeout=timeout,
        )

        # ── Parse LLM response as YAML ──────────────────────────────────────────
        print("[TransformationAgent] Parsing LLM response...")
        try:
            final_config = yaml.safe_load(llm_response)
            if not isinstance(final_config, dict):
                raise ValueError("LLM response did not produce a valid dict")
            print("[TransformationAgent] ✓ Successfully parsed YAML configuration")
        except yaml.YAMLError as e:
            print(f"[TransformationAgent] ❌ YAML parsing error: {e}")
            print(f"[TransformationAgent] Raw LLM response:\n{llm_response}")
            raise

        # ── Save to file ────────────────────────────────────────────────────────
        print("[TransformationAgent] Saving final configuration to YAML...")
        config_saved_to = self._save_final_config(session_id, final_config)

        # ── Return result ───────────────────────────────────────────────────────
        result = {
            "session_id": session_id,
            "final_config": final_config,
            "config_saved_to": config_saved_to,
            "llm_response_raw": llm_response,
            "generation_timestamp": datetime.now().isoformat(),
        }

        print(f"[TransformationAgent] ✓ Final config saved to: {config_saved_to}")
        return result

    def _save_final_config(self, session_id: str, final_config: Dict[str, Any]) -> str:
        """
        Save final configuration to YAML file.

        File naming: <session_id>_full_state_llm.yaml

        Args:
            session_id: The session ID
            final_config: The final configuration dict

        Returns:
            Absolute path to the saved file
        """
        # Ensure configs directory exists
        _CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{session_id}_{timestamp}_full_state_llm.yaml"
        filepath = _CONFIGS_DIR / filename

        # Save YAML
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(
                make_serializable(final_config),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        return str(filepath)

    def _get_strategic_output_sample(self, df: pd.DataFrame, head_n: int = 10, mid_n: int = 5, tail_n: int = 5) -> str:
        """
        Get strategic sample from output DataFrame showing head + middle + tail.
        This helps the LLM detect complete sort patterns across all groups.
        
        Args:
            df: Output DataFrame to sample
            head_n: Number of rows from start
            mid_n: Number of rows from middle
            tail_n: Number of rows from end
            
        Returns:
            Markdown table string with strategic samples
        """
        total_rows = len(df)
        
        if total_rows <= head_n + mid_n + tail_n:
            # Small enough to show all
            return df.to_markdown(index=False) or ""
        
        # Get head
        head_df = df.head(head_n)
        
        # Get middle samples (around the 33% and 66% marks to see transitions)
        mid_start1 = total_rows // 3
        mid_start2 = 2 * total_rows // 3
        mid_df1 = df.iloc[mid_start1:mid_start1 + mid_n // 2]
        mid_df2 = df.iloc[mid_start2:mid_start2 + mid_n // 2]
        
        # Get tail
        tail_df = df.tail(tail_n)
        
        # Combine with section markers
        sections = []
        sections.append("**First rows (1-{}):**\n{}".format(head_n, head_df.to_markdown(index=False)))
        sections.append("**Middle rows (~row {}):**\n{}".format(mid_start1, mid_df1.to_markdown(index=False)))
        sections.append("**Middle rows (~row {}):**\n{}".format(mid_start2, mid_df2.to_markdown(index=False)))
        sections.append("**Last rows ({}-{}):**\n{}".format(total_rows - tail_n + 1, total_rows, tail_df.to_markdown(index=False)))
        
        return "\n\n".join(sections)
