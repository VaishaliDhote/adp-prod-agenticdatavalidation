# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - LLM transformation detection (derived columns/formulas, tasks 7-8). REUSABLE.
# Rework: feed detected rules into the transformation inventory / IR, not free-form text.
# ============================================================================
"""
LLM Transformation Detection Service
=====================================
Service to detect transformations between input and output Excel files
using LLM-based analysis for a given session ID.

Returns human-readable output describing sheet-level transformations
with derived formulas for calculated columns.
"""

import asyncio
import datetime
# import sys
# from pathlib import Path
# from typing import Optional

# Add parent directory to path for imports when running as script
# sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from llm_client import LLMClient
from prompts.prompt_transformation import TRANSFORMATION_PROMPT


def _inject_runtime_context(prompt_template: str) -> str:
    """Inject the *real* current datetime/date/year into the transformation prompt.

    The prompt contains many literal ``{...}`` tokens (JSON-ish examples like
    ``aggregations{}``), so ``str.format`` is unsafe here. We use plain
    ``str.replace`` against explicit ``<<...>>`` sentinels defined in
    ``prompts/prompt_transformation.py``. This MUST run on every call so that
    long-running servers always send the *current* date to the LLM (not the
    date when the module was first imported).
    """
    now_dt = datetime.datetime.now()
    return (
        prompt_template
        .replace("<<CURRENT_DATETIME>>", now_dt.strftime("%Y-%m-%d %H:%M:%S"))
        .replace("<<CURRENT_DATE>>",     now_dt.strftime("%Y-%m-%d"))
        .replace("<<CURRENT_YEAR>>",     str(now_dt.year))
    )


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# DATA_DIR = Path(__file__).parent.parent / "data"
# INPUT_FILES_DIR = DATA_DIR / "input_files"
# OUTPUT_FILES_DIR = DATA_DIR / "output_files"


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

# def _find_excel_file(directory: Path) -> Optional[Path]:
#     """Find the first Excel file in a directory."""
#     for ext in ["*.xlsx", "*.xls", "*.xlsm"]:
#         files = list(directory.glob(ext))
#         if files:
#             return files[0]
#     return None


# def _load_excel_first_sheet(file_path: Path) -> tuple[pd.DataFrame, str]:
#     """Load the first sheet from an Excel file."""
#     excel_file = pd.ExcelFile(file_path)
#     sheet_name = excel_file.sheet_names[0]
#     df = pd.read_excel(excel_file, sheet_name=sheet_name)
#     return df, sheet_name


def _format_dataframe_for_prompt(df: pd.DataFrame, name: str, max_rows: int = 500) -> str:
    """Format a DataFrame for inclusion in the prompt."""
    info_lines = [
        f"## {name}",
        f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
        f"Columns: {', '.join(df.columns.tolist())}",
        "",
        "### Column Info:",
    ]
    
    for col in df.columns:
        dtype = str(df[col].dtype)
        non_null = df[col].notna().sum()
        null_count = df[col].isna().sum()
        info_lines.append(f"- {col}: dtype={dtype}, non_null={non_null}, null={null_count}")
    
    info_lines.append("")
    info_lines.append("### Complete Data:")
    
    if len(df) > max_rows:
        data_str = df.head(max_rows).to_markdown(index=False)
        info_lines.append(data_str)
        info_lines.append(f"\n... (showing first {max_rows} of {len(df)} rows)")
    else:
        data_str = df.to_markdown(index=False)
        info_lines.append(data_str)
    
    return "\n".join(info_lines)


def _build_user_prompt(
    input_df: pd.DataFrame,
    input_sheet_name: str,
    output_df: pd.DataFrame,
    output_sheet_name: str,
) -> str:
    """Build the user prompt with complete input and output data."""
    input_section = _format_dataframe_for_prompt(input_df, f"INPUT SHEET: {input_sheet_name}")
    output_section = _format_dataframe_for_prompt(output_df, f"OUTPUT SHEET: {output_sheet_name}")
    
    user_prompt = f"""Analyze the following INPUT and OUTPUT DataFrames to detect ALL transformations applied.
input_df={input_section}
output_df={output_section}
"""
    return user_prompt


# ─────────────────────────────────────────────────────────────────────────────
# Main Service Function
# ─────────────────────────────────────────────────────────────────────────────

async def detect_transformations_for_session(   
    input_df: pd.DataFrame,
    input_sheet_name: str,
    output_df: pd.DataFrame,
    output_sheet_name: str,
    max_tokens: int = 16000,
    timeout: int = 600,
) -> str:
    """
    Detect all transformations applied between input and output Excel files
    for a given session ID.
    
    Uses FULL DataFrame data to derive exact formulas and detect deduplication logic.
    
    Args:
        session_id: The session ID (folder name under input_files and output_files)
        max_tokens: Max tokens for LLM response (default 16000)
        timeout: Timeout in seconds (default 600)
        
    Returns:
        String containing human-readable transformation analysis with derived formulas
        
    Raises:
        FileNotFoundError: If session folder or Excel files not found
    """
    # # Locate and load input file
    # input_dir = INPUT_FILES_DIR / session_id
    # if not input_dir.exists():
    #     raise FileNotFoundError(f"Input directory not found: {input_dir}")
    
    # input_file = _find_excel_file(input_dir)
    # if not input_file:
    #     raise FileNotFoundError(f"No Excel file found in: {input_dir}")
    
    # # Locate and load output file
    # output_dir = OUTPUT_FILES_DIR / session_id
    # if not output_dir.exists():
    #     raise FileNotFoundError(f"Output directory not found: {output_dir}")
    
    # output_file = _find_excel_file(output_dir)
    # if not output_file:
    #     raise FileNotFoundError(f"No Excel file found in: {output_dir}")
    
    # # Load Excel files (full data)
    # input_df, input_sheet_name = _load_excel_first_sheet(input_file)
    # output_df, output_sheet_name = _load_excel_first_sheet(output_file)
    
    # Build user prompt with complete data
    user_prompt = _build_user_prompt(
        input_df=input_df,
        input_sheet_name=input_sheet_name,
        output_df=output_df,
        output_sheet_name=output_sheet_name,
    )
    
    # Call LLM with the comprehensive transformation prompt
    client = LLMClient()
    response_text = await client.generate_long_response(
        system_prompt=_inject_runtime_context(TRANSFORMATION_PROMPT),
        context=user_prompt,
        vendor="Bedrock",
        max_tokens=max_tokens,
        timeout=timeout,
    )
    print(f"[LLM Response] Detected transformations for session:\n{len(response_text)} characters")
    return response_text

def run_detect_transformations(session_id: str, **kwargs) -> str:
    """
    Synchronous wrapper for detect_transformations_for_session.
    
    Args:
        session_id: The session ID
        **kwargs: Additional arguments passed to detect_transformations_for_session
        
    Returns:
        String containing the transformation analysis
    """
    return asyncio.run(detect_transformations_for_session(session_id, **kwargs))


