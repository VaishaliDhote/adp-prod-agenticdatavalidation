# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Targeted-editing agent (LIVE ConfigEditorAgent): intent -> edit ->
# validate -> retry (tasks 19-20, 24). REUSABLE.
# Rework: retarget from YAML to JSON blocks; validate against the JSON contract.
# ============================================================================
"""
Config Editor Agent

Orchestrates configuration editing conversations.

Responsibilities:
- Coordinating intent classification (via IntentClassificationService)
- Managing retry logic on validation failures  
- Coordinating between LLM calls and validation
- Formatting responses with summaries and confirmations
"""
from __future__ import annotations

import logging
from typing import Any

from services.config_editor_service import ConfigEditorService
from services.intent_classification_service import IntentClassificationService, UserIntent
from knowledge.config_store import get_summary
from utils.yaml_validator import validate_yaml_edit, extract_valid_columns

logger = logging.getLogger(__name__)


class ConfigEditorAgent:
    """
    Agent responsible for orchestrating config editing conversations.
    
    Uses services for:
    - Intent classification (IntentClassificationService)
    - Config editing (ConfigEditorService)
    
    Handles:
    - Retry logic on validation failures
    - Response formatting
    - Summary display for first messages
    """
    
    MAX_RETRIES = 2
    
    def __init__(self, session_id: str = "") -> None:
        self._service = ConfigEditorService(session_id=session_id)
        self._intent_service = IntentClassificationService(session_id=session_id)
        self.session_id = session_id

    async def run(
        self,
        bu: str,
        platform: str,
        category: str,
        user_query: str,
        current_yaml: str,
        config_version: str = "",
    ) -> dict[str, Any]:
        """
        Run one conversational turn with intent classification, validation, and retry logic.

        Returns:
            {
                "reply":           str,         # LLM response to show user
                "confirmed":       bool,        # True when user confirmed
                "proposed_yaml":   str,         # full merged config (for saving)
                "changes_display": str,         # human-readable change summary
                "changes":         list[dict],  # structured changes for API
                "affected_phases": list[str],   # which phases were edited
                "intent":          str,         # classified user intent
                "validation_warnings": list[str],  # non-blocking validation warnings
            }
        """
        logger.info(f"[ConfigEditorAgent.run] START - session={self.session_id}, bu={bu}, platform={platform}, category={category}")
        logger.info(f"[ConfigEditorAgent.run] user_query={user_query[:100]}..., current_yaml length={len(current_yaml)}")
        
        # Get conversation history for intent classification
        history = self._service._repo.fetch_history(self.session_id)
        logger.info(f"[ConfigEditorAgent.run] Fetched history: {type(history)}, length={len(history) if history else 0}")
        
        history_dicts = []
        if history:
            for i, msg in enumerate(history):
                logger.debug(f"[ConfigEditorAgent.run] history[{i}]: type={type(msg)}, value={msg!r}")
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    history_dicts.append({"role": msg["role"], "content": msg["content"]})
                else:
                    logger.warning(f"[ConfigEditorAgent.run] Skipping malformed history item: {msg!r}")
        
        # Check if this is the first message in the session
        is_first_message = len(history_dicts) == 0
        config_summary = None
        if is_first_message:
            config_summary = get_summary(bu, platform, category)
            logger.info(f"[ConfigEditorAgent] First message, fetched summary: {bool(config_summary)}")
        
        # 1. Classify user intent (hybrid: rules first, LLM fallback)
        logger.info(f"[ConfigEditorAgent.run] Classifying intent...")
        intent = await self._intent_service.classify(
            user_query=user_query,
            conversation_history=history_dicts,
            use_llm_fallback=True,
        )
        logger.info(f"[ConfigEditorAgent] Classified intent: {intent}")
        
        # 2. Handle based on intent
        if intent == UserIntent.CONFIRM:
            logger.info(f"[ConfigEditorAgent.run] Returning CONFIRM response")
            return self._build_response(
                reply="CONFIRMED",
                confirmed=True,
                proposed_yaml=current_yaml,
                intent=intent.value,
            )
        
        if intent == UserIntent.CANCEL:
            logger.info(f"[ConfigEditorAgent.run] Returning CANCEL response")
            return self._build_response(
                reply="Changes cancelled. Starting fresh from the confirmed configuration.",
                confirmed=False,
                proposed_yaml="",  # Signal to reload from confirmed
                intent=intent.value,
            )
        
        # 3. For EDIT, CLARIFY, QUESTION intents - call LLM with retry logic
        input_cols, output_cols = extract_valid_columns(current_yaml)
        logger.info(f"[ConfigEditorAgent.run] Extracted columns: {len(input_cols)} input, {len(output_cols)} output")
        last_error = ""
        
        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.info(f"[ConfigEditorAgent] LLM attempt {attempt}/{self.MAX_RETRIES}")
            
            # Build query with error feedback if retrying
            effective_query = user_query
            if last_error:
                effective_query = (
                    f"{user_query}\n\n"
                    f"[SYSTEM: Previous attempt had validation error: {last_error}. "
                    f"Please fix and try again.]"
                )
            
            # Call the service
            logger.info(f"[ConfigEditorAgent.run] Calling process_turn...")
            try:
                result = await self._service.process_turn(
                    bu=bu,
                    platform=platform,
                    category=category,
                    user_query=effective_query,
                    current_yaml=current_yaml,
                    config_version=config_version,
                )
                logger.info(f"[ConfigEditorAgent.run] process_turn returned: type={type(result)}, keys={result.keys() if isinstance(result, dict) else 'N/A'}")
            except Exception as e:
                logger.exception(f"[ConfigEditorAgent.run] process_turn raised exception: {e}")
                raise
            
            if not isinstance(result, dict):
                logger.error(f"[ConfigEditorAgent.run] process_turn returned non-dict: {type(result)}, value={result!r}")
                raise TypeError(f"process_turn returned {type(result)}, expected dict")
            
            result["intent"] = intent.value
            result["validation_warnings"] = []
            
            # Prepend summary for first message
            if is_first_message and config_summary:
                result["reply"] = self._format_reply_with_summary(config_summary, result["reply"])
                result["summary"] = config_summary
            
            # If no YAML was returned (clarifying question), return as-is
            if not result["proposed_yaml"]:
                return result
            
            # If confirmed, return as-is
            if result["confirmed"]:
                return result
            
            # 4. Validate the LLM output
            is_valid, error, warnings = validate_yaml_edit(
                edited_yaml=result["proposed_yaml"],
                affected_phases=result["affected_phases"],
                valid_input_columns=input_cols,
                valid_output_columns=output_cols,
            )
            
            result["validation_warnings"] = warnings
            
            if warnings:
                logger.warning(f"[ConfigEditorAgent] Validation warnings: {warnings}")
            
            if is_valid:
                # Success! Append confirmation question if changes were made
                logger.info(f"[ConfigEditorAgent] Validation passed on attempt {attempt}")
                if result["proposed_yaml"] and result["proposed_yaml"] != current_yaml:
                    result["reply"] = self._append_confirmation_prompt(result["reply"])
                return result
            
            # Validation failed - retry if we have attempts left
            last_error = error
            logger.warning(f"[ConfigEditorAgent] Validation failed: {error}")
        
        # Max retries exceeded - return error to user
        logger.error(f"[ConfigEditorAgent] Max retries exceeded. Last error: {last_error}")
        return self._build_response(
            reply=(
                f"I'm having trouble generating a valid configuration edit. "
                f"Error: {last_error}. Could you try rephrasing your request?"
            ),
            confirmed=False,
            proposed_yaml="",
            intent=intent.value,
        )

    async def save(self, bu: str, platform: str, category: str, yaml_content: str) -> str:
        """Save confirmed configuration to S3 (with summary regeneration)."""
        return await self._service.save_confirmed_config(bu, platform, category, yaml_content)
    
    def _build_response(
        self,
        reply: str,
        confirmed: bool,
        proposed_yaml: str,
        intent: str,
        changes_display: str = "",
        changes: list[dict] | None = None,
        affected_phases: list[str] | None = None,
        validation_warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a standardized response dict."""
        return {
            "reply": reply,
            "confirmed": confirmed,
            "proposed_yaml": proposed_yaml,
            "changes_display": changes_display,
            "changes": changes or [],
            "affected_phases": affected_phases or [],
            "intent": intent,
            "validation_warnings": validation_warnings or [],
        }

    def _format_reply_with_summary(self, summary: str, llm_reply: str) -> str:
        """
        Format the reply to include the config summary for first-time users.
        Shows current config state and the LLM's response to their edit request.
        """
        return f"""## Current Configuration Summary

{summary}

---

{llm_reply}"""

    def _append_confirmation_prompt(self, reply: str) -> str:
        """
        Append a confirmation question to the LLM reply when changes were made.
        This prompts the user to confirm or continue editing.
        """
        confirmation_text = (
            "\n\n---\n\n"
            "**Is this what you meant?** Reply 'confirm' to save these changes, "
            "or let me know what adjustments you'd like to make."
        )
        return reply + confirmation_text
