# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detection entry point (tasks 1-13,17,20). REUSABLE.
# Rework: return a transformation RULE INVENTORY instead of saving bespoke YAML
# to data/generated_configs/; add API mapping + unsupported flags + UUIDs.
# ============================================================================
"""
services/transformation_service.py
=====================================
TransformationService
-----------------------
Single entry point for detecting all transformations between an input and
output DataFrame.  Accepts a pre-built FinalAnalysisState (produced by the
FileAnalysisAgent) together with the two DataFrames and returns a
TransformationResult that contains:

  • The full phase config dict  (config)
  • The resolved column mapping (column_mapping)
  • The absolute path of the saved YAML (config_saved_to)
  • A human-readable summary     (summary())
  • ISO-timestamp of generation  (generated_at)

The YAML is saved to:
    data/generated_configs/<session_id>_<YYYYMMDD_HHMMSS>_transformation_config.yaml

Usage
-----
    from services.transformation_service import run_transformation_detection

    result = run_transformation_detection(
        file_analysis_state = my_final_analysis_state,
        input_df            = df_in,
        output_df           = df_out,
    )
    print(result.summary())
    print(result.config_saved_to)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

from states.final_analysis_state import FinalAnalysisState
from transformation_phases.main import detect_all
from utils.serializer import make_serializable
from utils.transformations.columns_mappings import _extract_column_mapping
import json
# from prompts.prompt_generate_final_config import GENERATE_FINAL_CONFIG_USER_PROMPT
from prompts.config_summary_combined import GENERATE_FINAL_CONFIG_USER_PROMPT

# ─────────────────────────────────────────────────────────────────────────────
# Project-root-relative storage directory (safe regardless of cwd)
# ─────────────────────────────────────────────────────────────────────────────

_ROOT        = Path(__file__).resolve().parent.parent
_CONFIGS_DIR = _ROOT / "data" / "generated_configs"


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransformationResult:
    """
    Structured output returned by run_transformation_detection().

    Attributes
    ----------
    session_id      : Session identifier extracted from FinalAnalysisState.
    config          : Full phase config dict produced by detect_all().
    column_mapping  : {input_column → output_column} rename map.
    config_saved_to : Absolute path of the persisted YAML file, or None if
                      save was skipped / failed.
    generated_at    : ISO-8601 timestamp of when this result was created.
    error           : Non-None when an exception was caught during detection.
    """

    session_id:     str
    config:         Dict[str, Any]  = field(default_factory=dict)
    column_mapping: Dict[str, str]  = field(default_factory=dict)
    config_saved_to: Optional[str]  = None
    generated_at:   str             = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    error: Optional[str] = None

    # ── Human-readable summary ────────────────────────────────────────────────

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"  TransformationService  —  session: {self.session_id}",
            "-" * 60,
            f"  Generated at : {self.generated_at}",
            f"  Column mapping ({len(self.column_mapping)} renamed columns):",
        ]
        for src, tgt in self.column_mapping.items():
            lines.append(f"      {src!r:30s} → {tgt!r}")
        if not self.column_mapping:
            lines.append("      (none detected)")

        # Active-phase summary from metadata
        phase_summary = self.config.get("metadata", {}).get("phase_summary", {})
        if phase_summary:
            lines.append("")
            lines.append("  Phase summary:")
            for phase, status in phase_summary.items():
                badge = "✅" if status == "active" else "⏭️ "
                lines.append(f"    {badge}  {phase:<28} {status}")

        lines.append("")
        if self.config_saved_to:
            lines.append(f"  Config saved → {self.config_saved_to}")
        if self.error:
            lines.append(f"  ⚠️  ERROR: {self.error}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_transformation_detection(
    file_analysis_state: FinalAnalysisState,
    input_df:            pd.DataFrame,
    output_df:           pd.DataFrame,
    save_config:         bool = True,
) -> TransformationResult:
    """
    Detect all transformations between *input_df* and *output_df*.

    Parameters
    ----------
    file_analysis_state : FinalAnalysisState produced by the FileAnalysisAgent.
                          Must contain a ``session_id`` key.
    input_df            : Raw input DataFrame.
    output_df           : Transformed output DataFrame.
    save_config         : When True (default) the config is persisted to
                          data/generated_configs/ as a timestamped YAML file.

    Returns
    -------
    TransformationResult — never raises; errors are surfaced in .error.
    """
    print("\n" + "=" * 60)
    print("  TransformationService — Starting detection")
    print("=" * 60 + "\n")

    session_id = file_analysis_state.get("session_id", "unknown_session")
    result     = TransformationResult(session_id=session_id)

    try:
        # ── 1. Extract column mapping from analysis state ─────────────────
        column_mapping: Dict[str, str] = _extract_column_mapping(file_analysis_state)
        result.column_mapping = column_mapping
        print(f"[TransformationService] Extracted {len(column_mapping)} column mappings")
        print(f"[TransformationService] column_mapping: {column_mapping}")

        # ── 2. Run all phase detectors ────────────────────────────────────
        print(f"[TransformationService] Running detect_all() ...")
        config = detect_all(
            df_in          = input_df,
            df_out         = output_df,
            column_mapping = column_mapping,
            df_in_full     = input_df,   # original unfiltered+unrenamed df_in
        )
        result.config = config
        print(f"[TransformationService] detect_all() completed")

        # ── 3. Persist config to YAML ─────────────────────────────────────
        if save_config and config:
            saved_path = _save_yaml(session_id, config)
            result.config_saved_to = saved_path
            print(f"[TransformationService] Config saved → {saved_path}")

        print(f"[TransformationService] ✅ Detection complete for session '{session_id}'")

    except Exception as exc:
        result.error = str(exc)
        print(f"[TransformationService] ❌ Error for session '{session_id}': {exc}")
        import traceback
        traceback.print_exc()

    print(result.summary())
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_yaml(session_id: str, config: Dict[str, Any]) -> str:
    """
    Persist *config* to a timestamped YAML file under data/generated_configs/.

    File name pattern:
        <session_id>_<YYYYMMDD_HHMMSS>_transformation_config.yaml

    Uses the project-root-relative path so the file is always saved to the
    correct location regardless of the current working directory.

    Returns the absolute path of the saved file.
    """
    _CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{session_id}_{timestamp}_transformation_config.yaml"
    save_path = _CONFIGS_DIR / filename

    try:
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(
                make_serializable(config),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        print(f"[TransformationService] YAML written ({save_path.stat().st_size} bytes) → {save_path}")
    except OSError as exc:
        print(f"[TransformationService] ⚠️  Failed to save YAML: {exc}")
        raise

    return str(save_path)

#__________________________________________________________________________
# user prompt builder for final transformation detection 
#__________________________________________________________________________

def build_final_config_user_prompt(df_in: pd.DataFrame,df_out: pd.DataFrame,detected_transformation:str,original_config: Dict[str, Any]) -> str:
    input_columns_list = list(df_in.columns) if df_in is not None else []
    output_columns_list = list(df_out.columns) if df_out is not None else []
    input_row_count = len(df_in) if df_in is not None else 0
    output_row_count = len(df_out) if df_out is not None else 0
    input_dtypes_dict = {str(k): str(v) for k, v in df_in.dtypes.to_dict().items()} if df_in is not None else {}
    output_dtypes_dict = {str(k): str(v) for k, v in df_out.dtypes.to_dict().items()} if df_in is not None else {}
    input_data_sample = df_in.head(10).to_string() if df_in is not None else ""
    output_data_sample = df_out.head(10).to_string() if df_out is not None else ""
    
    # Dump YAML and escape braces for .format()
    config_yaml = yaml.dump(original_config, sort_keys=False)
    # Replace { with {{ and } with }} to escape for .format()
    config_yaml_escaped = config_yaml.replace('{', '{{').replace('}', '}}')
    
    # Convert dicts to JSON and escape braces
    input_dtypes_json = json.dumps(input_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')
    output_dtypes_json = json.dumps(output_dtypes_dict, indent=2).replace('{', '{{').replace('}', '}}')    
    
    # Escape braces in data samples
    input_data_sample_escaped = input_data_sample.replace('{', '{{').replace('}', '}}')
    output_data_sample_escaped = output_data_sample.replace('{', '{{').replace('}', '}}')
    
    user_context = GENERATE_FINAL_CONFIG_USER_PROMPT.format(
        detection_response=detected_transformation,
        config_result_yaml=config_yaml_escaped,
        input_columns_list=input_columns_list,
        input_row_count=input_row_count,
        input_dtypes_dict=input_dtypes_json,
        output_columns_list=output_columns_list,
        output_row_count=output_row_count,
        output_dtypes_dict=output_dtypes_json,
        input_data_sample=input_data_sample_escaped,
        output_data_sample=output_data_sample_escaped
    )
    return user_context
