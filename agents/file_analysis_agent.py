# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - File Analysis Agent. REUSABLE.
# Rework: drive analysis from FileState (multi-file/multi-sheet) instead of a
# single input/output DataFrame pair.
# ============================================================================
from __future__ import annotations

from typing import Any

from states.filestate import FileState
from states.final_analysis_state import FinalAnalysisState
from services.file_anaysis_service import run_full_analysis
import pandas as pd


class FileAnalysisAgent:
    """Agent responsible for running column/sheet/relationship analysis.

    This agent expects a prepared FileState (e.g. from FileLoaderAgent)
    and orchestrates the full analysis pipeline, including LLM calls.
    """

    def __init__(self) -> None:
        # Placeholder for future dependencies (config, logger, etc.)
        pass

    async def analyze(self, input_df: pd.DataFrame, input_sheet_name: str, output_df: pd.DataFrame, output_sheet_name: str) -> FinalAnalysisState:
        """Run full analysis on the given FileState and return FinalAnalysisState."""

        return await run_full_analysis(input_df=input_df, input_sheet_name=input_sheet_name, output_df=output_df, output_sheet_name=output_sheet_name)
