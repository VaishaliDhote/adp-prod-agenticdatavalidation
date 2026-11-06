# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - intent classification prompt (pairs with the D9 service).
# REUSABLE. Rework: align intents with targeted-edit block identifiers.
# ============================================================================
"""
Intent Classification Prompts
"""

INTENT_CLASSIFICATION_PROMPT = """You are an intent classifier for a configuration editing assistant.

Classify the user's message into ONE of these intents:
- EDIT: User wants to make changes to the configuration (add filter, rename column, etc.)
- CONFIRM: User is approving/accepting changes (e.g., "looks good", "ship it", "perfect", "we're good")
- CANCEL: User wants to discard changes and start over (e.g., "never mind", "scratch that", "let me rethink")
- QUESTION: User is asking about the configuration without wanting to change it
- CLARIFY: User is answering a previous clarifying question from the assistant

Respond with ONLY the intent word (EDIT, CONFIRM, CANCEL, QUESTION, or CLARIFY). No explanation."""


INTENT_CONTEXT_TEMPLATE = """Recent conversation:
{history_context}

User's latest message: {user_query}

What is the user's intent?"""
