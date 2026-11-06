# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Interpret user requested change (task 19). REUSABLE.
# ============================================================================
"""
Intent Classification Service

Handles user intent classification using hybrid approach:
1. Rule-based patterns (fast, free)
2. LLM fallback for ambiguous cases
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Optional

from llm_client import LLMClient
from prompts.intent_classification_prompt import (
    INTENT_CLASSIFICATION_PROMPT,
    INTENT_CONTEXT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class UserIntent(str, Enum):
    """Enum for user intents."""
    EDIT = "edit"
    CONFIRM = "confirm"
    CLARIFY = "clarify"
    QUESTION = "question"
    CANCEL = "cancel"
    
    @classmethod
    def all_intents(cls) -> list[str]:
        return [intent.value for intent in cls]


class IntentClassificationService:
    """Service for classifying user intent."""
    
    # Regex patterns for rule-based classification
    CONFIRM_PATTERNS = [
        r"^(confirm|save|done|yes|looks good|lgtm|ok|okay|approve|accept)\.?$",
        r"^(that'?s? (correct|right|good|fine|perfect))\.?$",
        r"^(go ahead|proceed|apply)\.?$",
        r"^yes,? (please|do it|save|confirm)\.?$",
        # UI button payloads: "save config", "save configuration", "confirm changes", etc.
        r"^(save|confirm|approve|apply) (config|configuration|it|this|changes?|edits?)\.?$",
    ]
    
    CANCEL_PATTERNS = [
        r"^(cancel|undo|revert|reset|nevermind|never mind|start over)\.?$",
        r"^(don'?t|do not) (save|apply|confirm)",
        r"^(scratch that|forget it|abort)\.?$",
    ]
    
    QUESTION_PATTERNS = [
        r"^(what|which|how|where|when|why|can you explain|show me|list)",
        r"\?$",
    ]
    
    ACTION_VERBS = [
        "add", "remove", "change", "update", "set", 
        "filter", "rename", "create", "delete"
    ]
    
    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
    
    def classify_rules(
        self,
        user_query: str,
        conversation_history: list[dict] | None = None,
    ) -> tuple[UserIntent, bool]:
        """
        Rule-based intent classification (fast, free).
        
        Returns:
            (intent, is_confident) - is_confident=True means strong pattern match
        """
        q = user_query.lower().strip()
        print(f"[classify_rules] Checking query: '{q}'")
        
        # Confirmation patterns - high confidence
        for pattern in self.CONFIRM_PATTERNS:
            if re.match(pattern, q):
                print(f"[classify_rules] ✅ Matched CONFIRM pattern: {pattern}")
                return UserIntent.CONFIRM, True
        
        # Cancel patterns - high confidence
        for pattern in self.CANCEL_PATTERNS:
            if re.match(pattern, q):
                print(f"[classify_rules] ✅ Matched CANCEL pattern: {pattern}")
                return UserIntent.CANCEL, True
        
        # Question patterns - only if no action verbs
        has_action = any(verb in q for verb in self.ACTION_VERBS)
        if not has_action:
            for pattern in self.QUESTION_PATTERNS:
                if re.search(pattern, q):
                    return UserIntent.QUESTION, True
        
        # Check if this is a clarification response
        if conversation_history and len(conversation_history) >= 1:
            last_msg = conversation_history[-1]
            if (last_msg.get("role") == "assistant_response" and 
                "?" in last_msg.get("content", "")):
                return UserIntent.CLARIFY, True
        
        # Has action verb - likely an edit request
        if has_action:
            return UserIntent.EDIT, True
        
        # No strong match - low confidence
        return UserIntent.EDIT, False
    
    async def classify_llm(
        self,
        user_query: str,
        conversation_history: list[dict] | None = None,
    ) -> UserIntent:
        """
        LLM-based intent classification (handles nuanced language).
        Used as fallback when rule-based classifier has low confidence.
        """
        # Build context with recent history
        history_context = ""
        if conversation_history and len(conversation_history) >= 2:
            recent = conversation_history[-4:]
            history_context = "\n".join(
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')[:200]}"
                for msg in recent
            )
        
        context = INTENT_CONTEXT_TEMPLATE.format(
            history_context=history_context or "None",
            user_query=user_query,
        )
        
        try:
            client = LLMClient(session_id=self.session_id, temperature=0.0)
            response = await client.generate_response(
                system_prompt=INTENT_CLASSIFICATION_PROMPT,
                context=context,
                max_tokens=20,
                timeout=10,
            )
            
            response_clean = response.strip().upper()
            
            # Map to UserIntent
            intent_map = {
                "EDIT": UserIntent.EDIT,
                "CONFIRM": UserIntent.CONFIRM,
                "CANCEL": UserIntent.CANCEL,
                "QUESTION": UserIntent.QUESTION,
                "CLARIFY": UserIntent.CLARIFY,
            }
            
            for key, value in intent_map.items():
                if key in response_clean:
                    logger.info(f"[IntentClassifier] LLM classified as: {value}")
                    return value
            
            logger.warning(f"[IntentClassifier] Could not parse LLM response: {response}")
            return UserIntent.EDIT
            
        except Exception as e:
            logger.error(f"[IntentClassifier] LLM classification failed: {e}")
            return UserIntent.EDIT
    
    async def classify(
        self,
        user_query: str,
        conversation_history: list[dict] | None = None,
        use_llm_fallback: bool = True,
    ) -> UserIntent:
        """
        Hybrid intent classification:
        1. Try rule-based first (fast, free)
        2. If low confidence and use_llm_fallback=True, call LLM
        """
        intent, is_confident = self.classify_rules(user_query, conversation_history)
        
        if is_confident:
            logger.info(f"[IntentClassifier] Rule-based (confident): {intent}")
            return intent
        
        if use_llm_fallback:
            logger.info(f"[IntentClassifier] Rule-based uncertain, calling LLM fallback")
            return await self.classify_llm(user_query, conversation_history)
        
        logger.info(f"[IntentClassifier] Rule-based (default): {intent}")
        return intent
