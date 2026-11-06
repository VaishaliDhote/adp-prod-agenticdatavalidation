from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from llm_client import LLMClient
from services.config_editor_service import ConfigEditorService
from knowledge.config_store import get_summary
from utils.yaml_validator import validate_yaml_edit, extract_valid_columns

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Add vs Replace Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_add_replace_ambiguity(user_query: str, current_yaml: str) -> dict[str, Any] | None:
    """
    Detect if user is trying to add a rule (filter, calculated column, etc.)
    when existing rules already exist, and if their intent (add vs replace) is unclear.
    
    Returns:
        None if no ambiguity detected (safe to proceed)
        Dict with clarification info if ambiguity detected:
        {
            "needs_clarification": True,
            "rule_type": "filter" | "calculated_column" | ...,
            "existing_description": str,  # Human-readable description of existing rules
            "new_description": str,       # What user wants to add
        }
    """
    q = user_query.lower().strip()
    
    # Patterns that explicitly indicate ADD (preserve existing)
    explicit_add_patterns = [
        r"add (one )?more",
        r"add another",
        r"also (add|filter|include)",
        r"additionally",
        r"on top of",
        r"in addition",
        r"keep.*(existing|current).*(and|also)",
        r"alongside",
        r"as well as",
        r"together with",
    ]
    
    # Patterns that explicitly indicate REPLACE
    explicit_replace_patterns = [
        r"replace.*(with|to|by)",
        r"instead of",
        r"change.*(to|into)",
        r"only (filter|keep)",
        r"remove.*(and|then).*add",
        r"don'?t keep",
        r"clear.*and",
    ]
    
    # Check for explicit add - no ambiguity
    for pattern in explicit_add_patterns:
        if re.search(pattern, q):
            logger.info(f"[AddReplaceDetection] Explicit ADD pattern matched: {pattern}")
            return None
    
    # Check for explicit replace - no ambiguity
    for pattern in explicit_replace_patterns:
        if re.search(pattern, q):
            logger.info(f"[AddReplaceDetection] Explicit REPLACE pattern matched: {pattern}")
            return None
    
    # Now check if user is adding something to an existing non-empty list
    # Patterns that suggest user wants to add a rule
    add_rule_patterns = [
        (r"add\s*(a\s+)?filter", "filter", "phase_2_rows", "filter"),
        (r"filter\s+(by|on|where)", "filter", "phase_2_rows", "filter"),
        (r"add\s*(a\s+)?(calculated|new)\s*column", "calculated_column", "phase_4_enrichment", "calculated_columns"),
        (r"add\s*(a\s+)?lookup", "lookup", "phase_4_enrichment", "lookup"),
    ]
    
    for pattern, rule_type, phase_key, sub_key in add_rule_patterns:
        if re.search(pattern, q):
            # User wants to add this type of rule - check if existing rules exist
            existing_rules = _get_existing_rules(current_yaml, phase_key, sub_key)
            if existing_rules:
                logger.info(f"[AddReplaceDetection] Ambiguous ADD detected: {rule_type}, existing rules: {len(existing_rules)}")
                return {
                    "needs_clarification": True,
                    "rule_type": rule_type,
                    "existing_description": _describe_existing_rules(existing_rules, rule_type),
                    "phase_key": phase_key,
                    "sub_key": sub_key,
                }
    
    return None


def _get_existing_rules(yaml_content: str, phase_key: str, sub_key: str) -> list[dict]:
    """Extract existing rules from the YAML config."""
    if not yaml_content:
        return []
    
    try:
        config = yaml.safe_load(yaml_content) or {}
        
        # Navigate to phase_transformation_result.corrected_config if needed
        if "phase_transformation_result" in config:
            config = config.get("phase_transformation_result", {}).get("corrected_config", {})
        
        phase = config.get(phase_key, {})
        sub = phase.get(sub_key, {})
        
        # Check if applied
        if not sub.get("applied"):
            return []
        
        rules = sub.get("details", {}).get("rules", [])
        return rules if isinstance(rules, list) else []
        
    except yaml.YAMLError:
        return []


def _describe_existing_rules(rules: list[dict], rule_type: str) -> str:
    """Generate human-readable description of existing rules."""
    if not rules:
        return ""
    
    if rule_type == "filter":
        descriptions = []
        for rule in rules:
            col = rule.get("column", {})
            col_name = col.get("name") if isinstance(col, dict) else col
            operator = rule.get("operator", "==")
            value = rule.get("value", rule.get("values", ""))
            if col_name:
                descriptions.append(f"{col_name} {operator} {value}")
        return ", ".join(descriptions) if descriptions else "existing filters"
    
    elif rule_type == "calculated_column":
        col_names = []
        for rule in rules:
            col = rule.get("column", {})
            col_name = col.get("name") if isinstance(col, dict) else "column"
            col_names.append(col_name)
        return ", ".join(col_names) if col_names else "existing calculated columns"
    
    return f"{len(rules)} existing {rule_type} rule(s)"


def build_clarification_message(ambiguity_info: dict[str, Any], user_query: str) -> str:
    """Build a user-friendly clarification message for add vs replace ambiguity."""
    rule_type = ambiguity_info["rule_type"]
    existing = ambiguity_info["existing_description"]
    
    # Friendly rule type names
    type_names = {
        "filter": "filter",
        "calculated_column": "calculated column",
        "lookup": "lookup",
    }
    friendly_type = type_names.get(rule_type, rule_type)
    
    return (
        f"You currently have a {friendly_type} configured: **{existing}**\n\n"
        f"Would you like me to:\n"
        f"1. **Keep the existing {friendly_type}** and add your new one (both will apply)\n"
        f"2. **Replace the existing {friendly_type}** with your new one only\n\n"
        f"Just reply with '1' or '2', or say 'add another' / 'replace'."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Intent Classification
# ─────────────────────────────────────────────────────────────────────────────

class UserIntent:
    """Represents classified user intent."""
    EDIT = "edit"              # User wants to make changes
    CONFIRM = "confirm"        # User confirms changes
    CLARIFY = "clarify"        # User is answering a clarification question
    QUESTION = "question"      # User is asking about the config (not editing)
    CANCEL = "cancel"          # User wants to cancel/undo
    
    @classmethod
    def all_intents(cls) -> list[str]:
        return [cls.EDIT, cls.CONFIRM, cls.CLARIFY, cls.QUESTION, cls.CANCEL]


def classify_intent_rules(user_query: str, conversation_history: list[dict] | None = None) -> tuple[str, bool]:
    """
    Rule-based intent classification (fast, free).
    
    Returns:
        (intent, is_confident) - is_confident=True means strong pattern match,
                                 is_confident=False means fallback to default
    """
    q = user_query.lower().strip()
    
    # Confirmation patterns - high confidence
    confirm_patterns = [
        r"^(confirm|save|done|yes|looks good|lgtm|ok|okay|approve|accept)\.?$",
        r"^(that'?s? (correct|right|good|fine|perfect))\.?$",
        r"^(go ahead|proceed|apply)\.?$",
        r"^yes,? (please|do it|save|confirm)\.?$",
    ]
    for pattern in confirm_patterns:
        if re.match(pattern, q):
            return UserIntent.CONFIRM, True
    
    # Cancel patterns - high confidence
    cancel_patterns = [
        r"^(cancel|undo|revert|reset|nevermind|never mind|start over)\.?$",
        r"^(don'?t|do not) (save|apply|confirm)",
        r"^(scratch that|forget it|abort)\.?$",
    ]
    for pattern in cancel_patterns:
        if re.match(pattern, q):
            return UserIntent.CANCEL, True

    # Vague / non-actionable messages — must trigger a clarifying question,
    # NOT an edit. These are short phrases that contain no concrete instruction
    # ("what to change", "to what value", "which column", etc.). Routing them
    # to EDIT causes the LLM to hallucinate changes.
    vague_patterns = [
        r"^(edit|edits|editing)\.?$",
        r"^edit (the )?(config|configuration|it|this|that)\.?$",
        r"^(change|update|modify|fix|tweak|adjust) (it|this|that|the config(uration)?)\.?$",
        r"^(make (changes|edits|it better|some changes))\.?$",
        r"^(improve|optimi[sz]e|clean( it)? up|refactor)( it| this| the config(uration)?)?\.?$",
        r"^(do something|something else|anything|whatever)\.?$",
        r"^(hi|hello|hey|help|start)\.?$",
    ]
    for pattern in vague_patterns:
        if re.match(pattern, q):
            return UserIntent.CLARIFY, True

    # Question patterns (asking about config, not editing)
    question_patterns = [
        r"^(what|which|how|where|when|why|can you explain|show me|list)",
        r"\?$",
    ]
    # Only treat as question if no action verbs
    action_verbs = ["add", "remove", "change", "update", "set", "filter", "rename", "create", "delete"]
    has_action = any(verb in q for verb in action_verbs)
    if not has_action:
        for pattern in question_patterns:
            if re.search(pattern, q):
                return UserIntent.QUESTION, True

    # Check if this is a clarification response (previous message was a question from assistant)
    if conversation_history and len(conversation_history) >= 1:
        last_msg = conversation_history[-1]
        if last_msg.get("role") == "assistant_response" and "?" in last_msg.get("content", ""):
            # Likely a response to clarification question
            return UserIntent.CLARIFY, True

    # Has action verb AND enough substance — likely a real edit request.
    # We require at least 4 words OR a digit/column-like token so messages
    # like bare "change" / "update" don't slip through as EDIT.
    if has_action:
        word_count = len(q.split())
        has_value_hint = bool(re.search(r"\d|[\"']|=|>|<|@|\.com", q))
        if word_count >= 4 or has_value_hint:
            return UserIntent.EDIT, True
        # Short action-only message — most likely needs clarification, but if
        # we have prior conversation context we let the LLM fallback take a
        # second look (e.g. "add another one" after a previous filter edit).
        if conversation_history:
            return UserIntent.CLARIFY, False
        return UserIntent.CLARIFY, True

    # No strong match — default to CLARIFY (low confidence triggers LLM fallback).
    # Previously this defaulted to EDIT, which let the LLM invent changes.
    return UserIntent.CLARIFY, False


# ─────────────────────────────────────────────────────────────────────────────
# LLM-based Intent Classification (fallback)
# ─────────────────────────────────────────────────────────────────────────────

INTENT_CLASSIFICATION_PROMPT = """You are an intent classifier for a configuration editing assistant.

Classify the user's LATEST message into ONE of these intents, using the recent
conversation as context:

- EDIT:     User describes a CONCRETE change with enough detail to act on
            (which step / which column / what value). Examples:
              "add a filter where Email ends with adp.com"
              "rename Name to Employee Name"
              "set min age to 21"
            DO NOT classify as EDIT if the message is just "edit", "change it",
            "update the config", "make it better", "improve" — those are CLARIFY.

- CONFIRM:  User approves the pending change ("looks good", "ship it", "yes save it").

- CANCEL:   User wants to discard pending changes ("never mind", "scratch that").

- QUESTION: User is asking ABOUT the configuration without proposing a change
            ("what filters are applied?", "which columns are kept?").

- CLARIFY:  Either (a) the user's message is too vague to act on and we need
            to ask back, OR (b) the user is ANSWERING a clarifying question the
            assistant just asked. Use the conversation history to decide.
            Examples:
              Assistant: "Which column?"  User: "Email"          -> CLARIFY
              Assistant: "What value?"    User: "21"             -> CLARIFY
              User (no context): "edit", "change it", "fix it"   -> CLARIFY

When in doubt between EDIT and CLARIFY, prefer CLARIFY — it is always safe to
ask one more question; it is NOT safe to invent an edit.

Respond with ONLY the intent word (EDIT, CONFIRM, CANCEL, QUESTION, or CLARIFY). No explanation."""


async def classify_intent_llm(
    user_query: str,
    conversation_history: list[dict] | None = None,
    session_id: str = "",
) -> str:
    """
    LLM-based intent classification (handles nuanced language).
    Used as fallback when rule-based classifier has low confidence.
    """
    # Build context with recent history
    history_context = ""
    if conversation_history and len(conversation_history) >= 2:
        recent = conversation_history[-4:]  # Last 2 turns
        history_context = "\n".join(
            f"{msg.get('role', 'unknown')}: {msg.get('content', '')[:200]}"
            for msg in recent
        )
    
    context = f"""Recent conversation:
{history_context or "None"}

User's latest message: {user_query}

What is the user's intent?"""

    try:
        client = LLMClient(session_id=session_id, temperature=0.0)
        response = await client.generate_response(
            system_prompt=INTENT_CLASSIFICATION_PROMPT,
            context=context,
            max_tokens=20,  # Only need one word
            timeout=10,     # Fast timeout for simple classification
        )
        
        # Parse response - extract intent word
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
        
        # Default to EDIT if parsing fails
        logger.warning(f"[IntentClassifier] Could not parse LLM response: {response}, defaulting to EDIT")
        return UserIntent.EDIT
        
    except Exception as e:
        logger.error(f"[IntentClassifier] LLM classification failed: {e}, defaulting to EDIT")
        return UserIntent.EDIT


async def classify_intent(
    user_query: str,
    conversation_history: list[dict] | None = None,
    session_id: str = "",
    use_llm_fallback: bool = True,
) -> str:
    """
    Hybrid intent classification:
    1. Try rule-based first (fast, free)
    2. If low confidence and use_llm_fallback=True, call LLM
    
    Args:
        user_query: The user's message
        conversation_history: Recent conversation for context
        session_id: For LLM client
        use_llm_fallback: Whether to use LLM when rules are uncertain
    
    Returns:
        Classified intent string
    """
    # Step 1: Rule-based classification
    intent, is_confident = classify_intent_rules(user_query, conversation_history)
    
    if is_confident:
        logger.info(f"[IntentClassifier] Rule-based (confident): {intent}")
        return intent
    
    # Step 2: LLM fallback if enabled
    if use_llm_fallback:
        logger.info(f"[IntentClassifier] Rule-based uncertain, calling LLM fallback")
        return await classify_intent_llm(user_query, conversation_history, session_id)
    
    logger.info(f"[IntentClassifier] Rule-based (default): {intent}")
    return intent


class ConfigEditorAgent:
    """
    Agent responsible for orchestrating config editing conversations.
    
    Responsibilities:
    - Intent classification
    - Retry logic on validation failures
    - Coordinating between LLM calls and validation
    - Deciding when to escalate vs. retry
    """
    
    MAX_RETRIES = 2
    
    def __init__(self, session_id: str = "") -> None:
        self._service = ConfigEditorService(session_id=session_id)
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
        # Get conversation history for intent classification
        history = self._service._repo.fetch_history(self.session_id)
        history_dicts = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in history
        ] if history else []
        
        # Check if this is the first message in the session
        is_first_message = len(history_dicts) == 0
        config_summary = None
        if is_first_message:
            config_summary = get_summary(bu, platform, category)
            logger.info(f"[ConfigEditorAgent] First message in session, fetched summary: {bool(config_summary)}")
        
        # 1. Classify user intent (hybrid: rules first, LLM fallback)
        intent = await classify_intent(
            user_query=user_query,
            conversation_history=history_dicts,
            session_id=self.session_id,
            use_llm_fallback=True,
        )
        logger.info(f"[ConfigEditorAgent] Classified intent: {intent}")
        
        # 2. Handle based on intent
        if intent == UserIntent.CONFIRM:
            return {
                "reply": "CONFIRMED",
                "confirmed": True,
                "proposed_yaml": current_yaml,
                "changes_display": "",
                "changes": [],
                "affected_phases": [],
                "intent": intent,
                "validation_warnings": [],
            }
        
        if intent == UserIntent.CANCEL:
            return {
                "reply": "Changes cancelled. Starting fresh from the confirmed configuration.",
                "confirmed": False,
                "proposed_yaml": "",  # Signal to reload from confirmed
                "changes_display": "",
                "changes": [],
                "affected_phases": [],
                "intent": intent,
                "validation_warnings": [],
            }
        
        # 2.5 Check for add vs replace ambiguity BEFORE calling LLM
        # This prevents the LLM from silently replacing existing rules
        if intent == UserIntent.EDIT:
            ambiguity = detect_add_replace_ambiguity(user_query, current_yaml)
            if ambiguity and ambiguity.get("needs_clarification"):
                logger.info(f"[ConfigEditorAgent] Add/Replace ambiguity detected: {ambiguity['rule_type']}")
                clarification_msg = build_clarification_message(ambiguity, user_query)
                return {
                    "reply": clarification_msg,
                    "confirmed": False,
                    "proposed_yaml": "",  # No YAML changes proposed yet
                    "changes_display": "",
                    "changes": [],
                    "affected_phases": [],
                    "intent": UserIntent.CLARIFY,  # Override to CLARIFY
                    "validation_warnings": [],
                }
        
        # 3. For EDIT, CLARIFY, QUESTION intents - call LLM with retry logic
        input_cols, output_cols = extract_valid_columns(current_yaml)
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
            result = await self._service.process_turn(
                bu=bu,
                platform=platform,
                category=category,
                user_query=effective_query,
                current_yaml=current_yaml,
                config_version=config_version,
            )
            
            result["intent"] = intent
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
        return {
            "reply": (
                f"I'm having trouble generating a valid configuration edit. "
                f"Error: {last_error}. Could you try rephrasing your request?"
            ),
            "confirmed": False,
            "proposed_yaml": "",
            "changes_display": "",
            "changes": [],
            "affected_phases": [],
            "intent": intent,
            "validation_warnings": [],
        }

    async def save(self, bu: str, platform: str, category: str, yaml_content: str) -> str:
        """Save confirmed configuration to S3 (with summary regeneration)."""
        return await self._service.save_confirmed_config(bu, platform, category, yaml_content)

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
