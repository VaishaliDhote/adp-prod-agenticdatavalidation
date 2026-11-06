# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 / Deliverable 5 - shared LLM helper service. REUSABLE.
# Rework: constrain to reasoning over IR; must not emit final JSON directly.
# ============================================================================
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from llm_client import LLMClient
from states.filestate import SheetMeta


class LLMService:
    """High-level LLM service wrapper.

    This class is responsible for calling LLMClient and exposing
    domain-specific helpers (like sheet summaries). More functions
    can be added here in the future as needed.
    """

    def __init__(
        self,
        session_id: str = "",
        user_email: str = "",
        x_request_id: str = "",
        x_user_session_id: str = "",
        temperature: float = 0.1,
    ) -> None:
        self._client = LLMClient(
            session_id=session_id,
            user_email=user_email,
            x_request_id=x_request_id,
            x_user_session_id=x_user_session_id,
            temperature=temperature,
        )

    async def sheet_summary_generator(
        self,
        sheet_name: str,
        sheet_meta: SheetMeta,
        df: Optional[pd.DataFrame] = None,
    ) -> str:
        """Generate a useful natural-language summary for a sheet.

        Prefer using precomputed FileState metadata (row_count,
        columns, stats) instead of recomputing from the DataFrame.

        Args:
            sheet_name: Name of the worksheet.
            sheet_meta: Metadata for this sheet from FileState.
            df: Optional pandas DataFrame; currently unused but kept
                for possible future extensions.

        Returns:
            A concise, analysis-oriented summary string.
        """
        
        row_count = int(sheet_meta.get("row_count", 0))
        
        columns_meta = sheet_meta.get("columns", {}) or {}        
        
        columns_profile: list[dict[str, Any]] = []
        for col_name, col_meta in columns_meta.items():
            stats = col_meta.get("stats", {}) or {}
            columns_profile.append(
                {
                    "name": col_meta.get("name", col_name),
                    "dtype": col_meta.get("dtype", "unknown"),
                    "non_null_count": int(stats.get("non_null_count", 0)),
                    "unique_count": int(stats.get("unique_count", 0))                    
                }
            )
        context_payload = {
            "sheet_name": sheet_name,
            "row_count": row_count,
            "columns": columns_profile,
        }
        system_prompt = (
            "You are a data analysis assistant. Given metadata about an Excel "
            "sheet (row counts, column types, and simple statistics), you must "
            "write a concise summary of what the sheet appears to contain and "
            "how it might be useful for analysis or for finding relationships "
            "with other sheets or files. Keep it under 8 sentences. Mention "
            "important keys/identifiers and any obvious metrics or dimensions."
        )

        user_context = (
            "Here is the sheet metadata as JSON (derived from a file state). "
            "Generate a human-readable summary that describes:\n"
            "- what the sheet is about,\n"
            "- what each important column represents,\n"
            "- possible relationships this sheet might have with other sheets "
            "(for example via IDs, foreign keys, or shared dimensions).\n\n"
            f"DATA:\n{context_payload}"
        )
        summary = await self._client.generate_response(
            system_prompt=system_prompt,
            context=user_context,
        )
        return summary