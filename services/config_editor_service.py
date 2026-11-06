# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 9 - Targeted-editing business logic: edit, summary, save (tasks 18-24). REUSABLE.
# Rework: retarget YAML->JSON; locate blocks by UUID; reapply via external API (task 25).
# ============================================================================
from __future__ import annotations

import html
import logging
import os
import re
from typing import Any

import yaml

from llm_client import LLMClient
from prompts.config_editor_prompt import CONFIG_EDITOR_SYSTEM_PROMPT
from prompts.config_summary_regenerate_prompt import CONFIG_SUMMARY_REGENERATE_PROMPT
from storage.dynamodb.chat_repository import ChatRepository, ChatMessage
from storage.dynamodb.business_repository import upsert_category_summary
from knowledge.config_store import get_config_yaml, get_config_yaml_for_session, store_confirmed, store_draft, get_summary
from storage.s3.config_repository import fetch_latest_draft_yaml, fetch_latest_confirmed_yaml
from utils.config_context_builder import (
    build_edit_context,
    merge_edited_slice,
    format_changes_for_display,
    get_change_summary,
)

logger = logging.getLogger(__name__)

_S3_CONFIGS_BUCKET = os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs")


def _extract_yaml_block(text: str) -> str:
    """Extract the first ```yaml ... ``` block from LLM response."""
    match = re.search(r"```yaml\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


_PHASE_KEY_RE = re.compile(r"^phase_\d+_\w+$")


def _sanitize_yaml_blocks_for_display(reply: str) -> str:
    """
    Strip top-level ``phase_X_*:`` keys from any ```yaml ... ``` block in the
    user-visible reply, lifting their children to the top level. The original
    phase-keyed YAML is still used for merging — only the reply shown to the
    user is sanitized.

    Example:
        ```yaml
        phase_2_rows:
          filter:
            applied: true
        ```
    becomes:
        ```yaml
        filter:
          applied: true
        ```
    """
    def _strip(match: re.Match) -> str:
        yaml_text = match.group(1)
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return match.group(0)  # leave unchanged on parse error

        if not isinstance(data, dict) or not data:
            return match.group(0)

        flat: dict[str, Any] = {}
        changed = False
        for k, v in data.items():
            if isinstance(k, str) and _PHASE_KEY_RE.match(k) and isinstance(v, dict):
                flat.update(v)
                changed = True
            else:
                flat[k] = v

        if not changed:
            return match.group(0)

        clean_yaml = yaml.dump(
            flat,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        return f"```yaml\n{clean_yaml}```"

    return re.sub(r"```yaml\s*(.*?)```", _strip, reply, flags=re.DOTALL)


# ─── Reply trimming helpers ───────────────────────────────────────────────────
#
# The user-visible reply must be: short explanation + a single ```yaml``` block.
# The LLM (or echoed history) sometimes still emits a ```summary``` block, a
# "**Changes:**" bullet list, and/or its own "Is this what you meant?" prompt.
# These helpers remove all of that so the UI shows a clean message; a single
# confirmation prompt is then appended by ConfigEditorAgent._append_confirmation_prompt.

# ```summary ... ``` blocks (fenced or with the 'summary' info-string).
_SUMMARY_BLOCK_RE = re.compile(r"```summary\s*.*?```", re.DOTALL | re.IGNORECASE)

# "**Changes:**" header through the end of its bullet list. Stops at the next
# blank line followed by a non-bullet/non-blank line, a fenced block, an `---`
# rule, or end-of-string. Tolerates "Changes:" without bold as well.
_CHANGES_SECTION_RE = re.compile(
    r"(?:\*\*\s*Changes\s*:?\s*\*\*|^\s*Changes\s*:\s*$)"   # header
    r".*?"                                                    # bullets
    r"(?=\n\s*\n(?!\s*[-*])|\n\s*```|\n\s*---|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

# Any "Is this what you meant?" / "Reply 'confirm' ..." line the LLM emits.
_CONFIRM_PROMPT_RE = re.compile(
    r"^.*(?:is this what you meant\??|reply\s*['\"]?confirm['\"]?).*$",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_noise_from_reply(reply: str) -> str:
    """
    Remove summary blocks, **Changes:** sections, the LLM's own confirmation
    prompt, and stray horizontal rules / blank-line runs from the reply shown
    to the user. Leaves the explanation + ```yaml``` block intact.
    """
    if not reply:
        return reply

    cleaned = _SUMMARY_BLOCK_RE.sub("", reply)
    cleaned = _CHANGES_SECTION_RE.sub("", cleaned)
    cleaned = _CONFIRM_PROMPT_RE.sub("", cleaned)

    # Drop standalone horizontal-rule lines that get orphaned after removal.
    cleaned = re.sub(r"(?m)^\s*---\s*$", "", cleaned)
    # Collapse 3+ consecutive newlines into a single blank line.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _extract_summary_block(text: str) -> str:
    """Extract the ```summary ... ``` block from LLM response."""
    match = re.search(r"```summary\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _update_summary_in_yaml(yaml_content: str, new_summary: str) -> str:
    """
    Update the summary field in a YAML config.
    Handles both nested (phase_transformation_result.summary) and top-level summary.
    """
    if not new_summary:
        return yaml_content
    
    try:
        config = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError:
        return yaml_content
    
    # Update in nested location (full_state.yaml format)
    if "phase_transformation_result" in config:
        config["phase_transformation_result"]["summary"] = new_summary
        logger.info("[_update_summary_in_yaml] Updated nested summary")
    else:
        # Update top-level summary
        config["summary"] = new_summary
        logger.info("[_update_summary_in_yaml] Updated top-level summary")
    
    return yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _extract_summary_regex(yaml_content: str) -> str | None:
    """
    Regex fallback: extract the summary value from raw YAML text when
    yaml.safe_load cannot parse the document (e.g. due to a parse error
    in an unrelated section).  Handles block scalars (|, |-) and plain /
    quoted single-line values.
    """
    # Match both "summary: |" block scalars and "summary: <value>" scalars.
    # We look for the key at any indentation level.
    block_re = re.compile(
        r'^[ \t]*summary:\s*\|[-+]?\n((?:(?:[ \t]+.*)\n?)+)',
        re.MULTILINE,
    )
    scalar_re = re.compile(
        r'^[ \t]*summary:\s+(.+)$',
        re.MULTILINE,
    )

    m = block_re.search(yaml_content)
    if m:
        # Strip the common leading indentation from the block lines.
        block_text = m.group(1)
        lines = block_text.splitlines()
        indent = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
        return "\n".join(l[indent:] for l in lines).strip() or None

    m = scalar_re.search(yaml_content)
    if m:
        return m.group(1).strip().strip('"').strip("'") or None

    return None


def extract_summary_from_yaml(yaml_content: str) -> str | None:
    """
    Extract the summary from a YAML config.
    Looks in phase_transformation_result.summary or top-level summary.
    Falls back to regex extraction when the YAML cannot be parsed.
    """
    if not yaml_content:
        return None
    try:
        config = yaml.safe_load(yaml_content) or {}
        # Try nested location first (full_state.yaml format)
        if "phase_transformation_result" in config:
            summary = config["phase_transformation_result"].get("summary")
            if summary:
                return summary
        # Try top-level summary
        return config.get("summary")
    except yaml.YAMLError:
        # YAML has a parse error elsewhere in the document; extract summary
        # directly from the raw text so we don't lose it.
        return _extract_summary_regex(yaml_content)


def get_latest_config(bu: str, platform: str, category: str, session_id: str = None) -> str:
    """
    Public helper used by the endpoint to load the starting YAML.
    If session_id is provided, checks for session drafts first.
    """
    if session_id:
        return get_config_yaml_for_session(bu, platform, category, session_id) or ""
    return get_config_yaml(bu, platform, category) or ""


def get_config_with_source(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> dict[str, Any]:
    """
    Get config along with detailed source information.
    
    Returns:
        {
            "yaml": str,           # The config YAML
            "source": str,         # "draft" | "confirmed" | "none"
            "summary": str | None, # Summary extracted from the YAML itself
        }
    """
    # Priority 1: Check for session-specific drafts
    draft = fetch_latest_draft_yaml(bu, platform, category, session_id)
    if draft:
        summary = extract_summary_from_yaml(draft)
        logger.info("[get_config_with_source] Using DRAFT for session=%s, summary=%s", 
                   session_id, bool(summary))
        return {
            "yaml": draft,
            "source": "draft",
            "summary": summary,
        }
    
    # Priority 2: Fall back to confirmed config
    confirmed = fetch_latest_confirmed_yaml(bu, platform, category)
    if confirmed:
        summary = extract_summary_from_yaml(confirmed)
        # If no summary in YAML, try DynamoDB as fallback
        if not summary:
            summary = get_summary(bu, platform, category)
        logger.info("[get_config_with_source] Using CONFIRMED config for session=%s, summary=%s", 
                   session_id, bool(summary))
        return {
            "yaml": confirmed,
            "source": "confirmed",
            "summary": summary,
        }
    
    logger.warning("[get_config_with_source] No config found for session=%s", session_id)
    return {
        "yaml": "",
        "source": "none",
        "summary": None,
    }


def get_config_with_context(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
) -> dict[str, Any]:
    """
    Get config along with context about whether this is a new session.
    
    Returns:
        {
            "yaml": str,           # The config YAML
            "is_new_session": bool, # True if no drafts exist for this session
            "summary": str | None,  # Config summary (for new sessions)
        }
    """
    # Check if there are existing drafts for this session
    draft = fetch_latest_draft_yaml(bu, platform, category, session_id)
    is_new_session = draft is None
    
    # Get the config (will be confirmed config if no draft)
    yaml_content = draft or get_config_yaml(bu, platform, category) or ""
    
    # Fetch summary only for new sessions
    summary = None
    if is_new_session and yaml_content:
        summary = get_summary(bu, platform, category)
    
    return {
        "yaml": yaml_content,
        "is_new_session": is_new_session,
        "summary": summary,
    }


class ConfigEditorService:

    def __init__(self, session_id: str = "") -> None:
        self._client  = LLMClient(session_id=session_id)
        self._repo    = ChatRepository()
        self.session_id = session_id

    async def process_turn(
        self,
        bu: str,
        platform: str,
        category: str,
        user_query: str,
        current_yaml: str,
        config_version: str = "",
    ) -> dict[str, Any]:
        """
        Run one conversational turn:
          1. Load chat history from DynamoDB
          2. Build minimal focused context (only relevant YAML sections)
          3. Call LLM with focused context + user query
          4. Merge edited slice back into full config
          5. Compute and return changes (not full YAML)

        Returns:
            {
                "reply":           str,         # assistant message to show user
                "confirmed":       bool,        # True when user confirmed changes
                "proposed_yaml":   str,         # full merged config (for internal use)
                "changes_display": str,         # human-readable change summary
                "changes":         list[dict],  # structured changes for API
                "affected_phases": list[str],   # which phases were edited
            }
        """
        logger.info(f"[ConfigEditorService.process_turn] START - session={self.session_id}")
        history = self._repo.fetch_history(self.session_id)
        logger.info(f"[ConfigEditorService.process_turn] Fetched history: type={type(history)}, length={len(history) if history else 0}")
        
        # Validate history items
        if history:
            for i, msg in enumerate(history):
                if not isinstance(msg, dict):
                    logger.error(f"[ConfigEditorService.process_turn] history[{i}] is not a dict: type={type(msg)}, value={msg!r}")
                elif "role" not in msg or "content" not in msg:
                    logger.error(f"[ConfigEditorService.process_turn] history[{i}] missing keys: {msg!r}")

        # Convert history to list of dicts for context builder
        history_dicts = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in history
            if isinstance(msg, dict) and "role" in msg and "content" in msg
        ] if history else []
        
        logger.info(f"[ConfigEditorService.process_turn] Converted {len(history_dicts)} history items")

        # Build minimal focused context (only relevant YAML sections)
        context, affected_phases = build_edit_context(
            user_query=user_query,
            full_yaml=current_yaml,
            conversation_history=history_dicts,
            max_history_turns=2,
        )
        logger.info(f"[ConfigEditorService.process_turn] Built context: length={len(context)}, affected_phases={affected_phases}")

        print("\n" + "="*80)
        print("[ConfigEditorService] LLM CONTEXT BEING SENT:")
        print("="*80)
        print(context)
        print("="*80 + "\n")

        logger.info(f"[ConfigEditorService.process_turn] Calling LLM...")
        raw_response = await self._client.generate_response(
            system_prompt=CONFIG_EDITOR_SYSTEM_PROMPT,
            context=context,
        )
        logger.info(f"[ConfigEditorService.process_turn] LLM response: type={type(raw_response)}, length={len(raw_response) if raw_response else 0}")

        # Sanitize user input before storing to prevent XSS when echoed in responses
        safe_user_query = html.escape(user_query)
        safe_raw_response = html.escape(raw_response)

        # Store both turns with full traceability
        for role, text in (("user_message", safe_user_query), ("assistant_response", safe_raw_response)):
            self._repo.insert(ChatMessage(
                session_id     = self.session_id,
                role           = role,
                message_text   = text,
                bu             = bu,
                platform       = platform,
                category       = category,
                config_version = config_version,
            ))

        confirmed = raw_response.strip().upper() == "CONFIRMED"
        edited_slice = _extract_yaml_block(raw_response)
        # Note: Summary is NOT extracted/updated during edits.
        # Summary is only regenerated when user confirms and saves (in save_confirmed_config)

        # Merge edited slice back into full config
        proposed_yaml = ""
        changes_display = ""
        changes = []

        if edited_slice and not confirmed:
            try:
                proposed_yaml = merge_edited_slice(current_yaml, edited_slice)
                # Don't update summary during edits - only on confirmation
                changes_display = format_changes_for_display(current_yaml, proposed_yaml)
                changes = get_change_summary(current_yaml, proposed_yaml)
            except ValueError as e:
                changes_display = f"⚠️ Could not merge changes: {e}"

        result = {
            "reply":           _strip_noise_from_reply(
                                   _sanitize_yaml_blocks_for_display(raw_response)
                               ),
            "confirmed":       confirmed,
            "proposed_yaml":   proposed_yaml,
            "changes_display": changes_display,
            "changes":         changes,
            "affected_phases": affected_phases,
            # Summary is not generated during edits - only on confirmation
        }
        logger.info(f"[ConfigEditorService.process_turn] Returning result: type={type(result)}, keys={result.keys()}")
        return result

    async def generate_summary_from_config(self, yaml_content: str) -> str | None:
        """
        Use LLM to generate a human-readable summary of the transformations
        applied in the YAML configuration.
        
        Returns:
            Generated summary string or None if generation fails.
        """
        print(f"[generate_summary_from_config] ========== GENERATING SUMMARY VIA LLM ==========")
        print(f"[generate_summary_from_config] yaml_content length={len(yaml_content) if yaml_content else 0}")
        
        if not yaml_content:
            print("[generate_summary_from_config] ❌ No yaml_content provided, returning None")
            return None
        
        try:
            logger.info("[ConfigEditorService] Generating summary from config via LLM...")
            
            user_message = f"""Analyze this transformation configuration and generate a summary:

```yaml
{yaml_content}
```"""
            
            summary = await self._client.generate_response(
                system_prompt=CONFIG_SUMMARY_REGENERATE_PROMPT,
                context=user_message,
                max_tokens=2000,
                timeout=120,
            )
            
            if summary:
                summary = summary.strip()
                print(f"[generate_summary_from_config] ✅ Generated summary ({len(summary)} chars)")
                logger.info("[ConfigEditorService] ✅ Generated summary (%d chars)", len(summary))
                return summary
            
            print("[generate_summary_from_config] ⚠️ LLM returned empty summary")
            logger.warning("[ConfigEditorService] LLM returned empty summary")
            return None
            
        except Exception as e:
            print(f"[generate_summary_from_config] ❌ FAILED: {type(e).__name__}: {e}")
            logger.error("[ConfigEditorService] Failed to generate summary: %s", e)
            return None

    async def save_confirmed_config(
        self,
        bu: str,
        platform: str,
        category: str,
        yaml_content: str,
    ) -> str:
        logger.info(
            "[ConfigEditorService] save_confirmed_config called: bu=%s platform=%s category=%s session=%s",
            bu, platform, category, self.session_id,
        )
        logger.info(
            "[ConfigEditorService] Confirmed YAML length: %d chars",
            len(yaml_content) if yaml_content else 0,
        )
        logger.info(
            "[ConfigEditorService] ── Confirmed YAML content (first 2000 chars) ──\n%s",
            yaml_content[:2000] if yaml_content else "(empty)",
        )
        if len(yaml_content) > 2000:
            logger.info(
                "[ConfigEditorService] ── Confirmed YAML content (last 1000 chars) ──\n%s",
                yaml_content[-1000:],
            )

        # Step 1: Generate updated summary from the final config
        logger.info("[ConfigEditorService] Regenerating summary for confirmed config...")
        new_summary = await self.generate_summary_from_config(yaml_content)
        
        # Step 2: Update summary in YAML if generated successfully
        final_yaml = yaml_content
        if new_summary:
            final_yaml = _update_summary_in_yaml(yaml_content, new_summary)
            logger.info("[ConfigEditorService] Updated summary in YAML")
        
        # Step 3: Save to S3
        logger.info("[ConfigEditorService] Pushing to S3 bucket: %s", _S3_CONFIGS_BUCKET)
        _, latest_key = store_confirmed(
            bu           = bu,
            platform     = platform,
            category     = category,
            session_id   = self.session_id,
            yaml_content = final_yaml,
            s3_bucket    = _S3_CONFIGS_BUCKET,
        )
        logger.info(
            "[ConfigEditorService] ✅ Confirmed config saved to S3: %s (bucket=%s)",
            latest_key, _S3_CONFIGS_BUCKET,
        )
        
        # Step 4: Also update summary in DynamoDB for quick access
        if new_summary:
            try:
                upsert_category_summary(bu, platform, category, new_summary)
                logger.info("[ConfigEditorService] ✅ Summary updated in DynamoDB")
            except Exception as e:
                logger.warning("[ConfigEditorService] Failed to update summary in DynamoDB: %s", e)
        
        return f"Saved to {latest_key}"
