# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - Input/Output Analysis. REUSABLE.
# Rework: decouple transformation detection (detect_transformations_for_session,
# column_mapping, columns_mappings) out of this analysis layer - that belongs to
# the Transformation Rule Identification sprint (Deliverable 5), not canonical analysis.
# ============================================================================
from __future__ import annotations

from typing import Dict, List, Tuple, Any
import pandas as pd
from states.filestate import FileState
from states.final_analysis_state import (
    FinalAnalysisState,
    ColumnMapping,
    SheetMapping,
    SheetRelationship,
    ColumnInputRef,
)
from llm_client import LLMClient
import json
from rapidfuzz import fuzz
from utils.transformations import column_mapping,columns_mappings
from services.llm_transformation import detect_transformations_for_session
from utils.transformations.columns_mappings import _extract_column_mapping



_COLUMN_CHUNK_SIZE = 15   # max output columns sent to LLM in a single call


def _build_chunk_payload(
    base_payload: Dict,
    chunk_cols: List[str],
) -> Dict:
    """
    Build a trimmed *possible_matches* payload that only contains the output
    columns in *chunk_cols*.  Input columns and input sample data are always
    kept in full so the LLM has the complete source schema for reference.
    """
    matched   = base_payload.get("matched_columns", {})
    unmatched = base_payload.get("unmatched_columns", [])
    sample    = base_payload.get("sample_data", {})

    chunk_set = set(chunk_cols)
    return {
        "output_columns":   chunk_cols,
        "input_columns":    base_payload.get("input_columns", []),
        "matched_columns":  {k: v for k, v in matched.items()   if k in chunk_set},
        "unmatched_columns":[c    for c in unmatched             if c in chunk_set],
        "sample_data": {
            "input":  sample.get("input", {}),   # keep full input sample
            "output": {k: v for k, v in sample.get("output", {}).items() if k in chunk_set},
        },
    }


async def _call_llm_for_chunk(
    chunk_payload: Dict,
    system_prompt: str,
    client: "LLMClient",
) -> List[Dict]:
    """
    Call the LLM for a single output-column chunk.  Returns the list of
    column_mapping dicts for that chunk (or an empty list on failure).
    """
    import re

    response_text = await client.generate_response(
        system_prompt=system_prompt,
        context=json.dumps(chunk_payload),
    )

    response_text_stripped = response_text.strip()
    json_candidate = None
    match = re.search(r'\{[\s\S]*\}', response_text_stripped)
    if match:
        json_candidate = match.group(0)
    else:
        json_candidate = response_text_stripped

    try:
        parsed: Any = json.loads(json_candidate)
    except json.JSONDecodeError:
        # ── Attempt graceful recovery: close unclosed brackets so that
        #    a truncated but otherwise valid response can still be parsed.
        try:
            fixed = _close_truncated_json(json_candidate)
            parsed = json.loads(fixed)
            print(f"[call_llm_for_column_mapping] ⚠️  Recovered truncated JSON for chunk "
                  f"({len(chunk_payload['output_columns'])} cols)")
        except Exception:
            print(
                f"[call_llm_for_column_mapping] ❌  JSON parse failed for chunk "
                f"({len(chunk_payload['output_columns'])} cols); "
                f"first 200 chars: {response_text_stripped[:200]}"
            )
            return []

    if isinstance(parsed, dict) and "output_sheets" in parsed:
        sheets = parsed["output_sheets"]
        if sheets and isinstance(sheets, list):
            return sheets[0].get("column_mappings", [])
    return []


def _close_truncated_json(text: str) -> str:
    """
    Best-effort repair of a truncated JSON string by removing the last
    incomplete object and closing all open brackets/braces.
    """
    # Work backwards to find the last complete object (ends with '}')
    last_brace = text.rfind('}')
    if last_brace != -1:
        text = text[:last_brace + 1]

    # Count opening vs closing brackets to figure out what needs closing
    opens  = {"[": "]", "{": "}"}
    close  = {"]": "[", "}": "{"}
    stack  = []
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in opens:
            stack.append(opens[ch])
        elif ch in close and stack and stack[-1] == ch:
            stack.pop()

    # Append any missing closing tokens in LIFO order
    for closer in reversed(stack):
        text += closer
    return text


async def call_llm_for_column_mapping(
    possible_matches: List[Dict],
) -> List[ColumnMapping]:
    """Use the LLM to turn possible column matches into semantic ColumnMapping objects.

    Large output schemas (>_COLUMN_CHUNK_SIZE columns) are split into chunks
    and processed with separate LLM calls whose results are merged.  This
    prevents token-limit truncation that breaks JSON parsing.

    This uses LLMClient.generate_response and expects pure JSON in the reply.
    """
    system_prompt = """You are a data schema mapping expert.

        Input:
        - Multiple input sheets metadata (columns, datatype, sample values)
        - Multiple output sheet metadata
        - Precomputed candidate mappings with pre-matched and unmatched columns

        Task:
        For each output sheet:
        For each column in that sheet:

        STEP 1 — VALIDATE PRE-MATCHED COLUMNS:
        - Review columns listed in "matched_columns" (output → input mapping).
        - Confirm whether the match is correct based on column names AND sample data values.
        - If the match is WRONG (name misleading or data does not align), reclassify it as unmatched and find the correct source.
        - If the match is CORRECT, keep it and assign mapping_type accordingly.

        STEP 2 — RESOLVE UNMATCHED COLUMNS:
        - For each column in "unmatched_columns", inspect all available input columns and sample data.
        - Try to find the best matching input column(s) based on:
            a) Name similarity (e.g. "ProductName_Cleaned" → "ProductName")
            b) Sample data overlap (values in output exist in some input column)
            c) Semantic meaning (e.g. "State_Filled" likely comes from "State")
        - If a source is found, assign mapping_type: "single_source" or "derived_column".
        - If NO source can be determined, still include the column with mapping_type: "derived_column" and confidence: 0.3.

        STEP 3 — CLASSIFY EACH MAPPING:
        1. Identify source column(s)
        2. Classify mapping_type using THESE STRICT RULES:
            - single_source  → output column comes from EXACTLY ONE input column
                               with NO aggregation or mathematical computation.
                               Only simple transformations are allowed: cleaning, trimming, formatting, 
                               standardizing, type conversion, null filling, case changes.
                               The row count remains the same (1:1 row mapping).

            - derived_column → output column involves ANY of the following:
                               a) Aggregation functions on one or more columns (SUM, AVG, COUNT, MIN, MAX, 
                                  MEAN, MEDIAN, STD, VAR, etc.) - row count typically reduces (N:1 mapping)
                               b) Mathematical operations or calculations between columns
                               c) Any computation that changes the cardinality of rows
                               d) Window functions or rolling calculations
                               IMPORTANT: If the output has fewer rows than input due to grouping/aggregation,
                               all non-key columns are likely derived_column.

            - multi_source   → output column directly concatenates or merges multiple input columns
                               without computation (string concatenation only, no math)

        3. source_groups rules:
            - single_source  → source_groups has exactly ONE group with exactly ONE column
            - derived_column → source_groups has ONE group with TWO OR MORE columns
            - multi_source   → source_groups may have multiple groups or multiple columns

        4. Identify if the column is a join key candidate.

        IMPORTANT RULES:
        - Every output column MUST appear in the response — do NOT skip any.
        - matched_columns are pre-computed hints; you MUST verify them, not blindly accept.
        - unmatched_columns MUST be resolved or included with best-effort mapping.
        - confidence MUST reflect your certainty: confirmed match ≥ 0.9, inferred ≈ 0.7, guessed ≈ 0.3–0.5.
        - Return ONLY valid JSON (no markdown, no extra text) with this exact structure:

        {
        "output_sheets": [
        {        
        "output_sheet": "sheet_name_string",
        "column_mappings": [
            {
            "output_column": "column_name_string",
            "mapping_type": "single_source",
            "confidence": 0.95,
            "source_groups": [
                {
                "group_id": 1,
                "role": "primary_combination",
                "columns": [
                    {
                    "input_file": "file_name_string",
                    "sheet": "sheet_name_string",
                    "column": "column_name_string"
                    }
                ]
                }
            ],
            "is_join_key_candidate": false
            }
        ]
        }]}

        Rules:
        - mapping_type MUST be: "single_source", "multi_source", "derived_column"
        - confidence MUST be a number between 0 and 1 (e.g., 0.95, not "0.95")
        - group_id MUST be an integer (e.g., 1, not "1")
        - is_join_key_candidate MUST be boolean (true or false, not "true" or "false")
        - Do NOT return JSON as string
        - Do NOT skip any sheet
        - Do NOT skip any column (matched or unmatched)
        - process all output sheets one by one. do not stop after the first sheet.
        """
    # print("payload of column mapping--------",payload,"system_prompt....",system_prompt)

    client = LLMClient()

    # ── Chunk large schemas to avoid LLM token-limit truncation ──────────────
    all_output_cols: List[str] = (
        possible_matches.get("output_columns", [])
        if isinstance(possible_matches, dict)
        else []
    )

    if not all_output_cols or not isinstance(possible_matches, dict):
        # Legacy path: pass as-is and hope for the best
        response_text = await client.generate_response(
            system_prompt=system_prompt,
            context=json.dumps(possible_matches),
        )
        import re
        response_text_stripped = response_text.strip()
        match = re.search(r'\{[\s\S]*\}', response_text_stripped)
        json_candidate = match.group(0) if match else response_text_stripped
        try:
            parsed: Any = json.loads(json_candidate)
            if isinstance(parsed, dict) and "output_sheets" in parsed:
                return parsed["output_sheets"]
            return parsed  # type: ignore[return-value]
        except json.JSONDecodeError:
            print("Exception in json parsing the column\nRaw response (first 500 chars):", response_text_stripped[:500])
            raise ValueError(f"LLM response is not valid JSON: {response_text_stripped}...")

    # Split output columns into chunks
    chunks = [
        all_output_cols[i: i + _COLUMN_CHUNK_SIZE]
        for i in range(0, len(all_output_cols), _COLUMN_CHUNK_SIZE)
    ]
    print(f"[call_llm_for_column_mapping] {len(all_output_cols)} output cols → "
          f"{len(chunks)} chunk(s) of ≤{_COLUMN_CHUNK_SIZE}")

    all_mappings: List[Dict] = []
    for idx, chunk_cols in enumerate(chunks):
        chunk_payload = _build_chunk_payload(possible_matches, chunk_cols)
        print(f"[call_llm_for_column_mapping] chunk {idx + 1}/{len(chunks)}: "
              f"{chunk_cols}")
        mappings = await _call_llm_for_chunk(chunk_payload, system_prompt, client)
        all_mappings.extend(mappings)
        print(f"[call_llm_for_column_mapping] chunk {idx + 1} → "
              f"{len(mappings)} mappings received")

    # Wrap into the expected output_sheets structure
    return [{"output_sheet": "default", "column_mappings": all_mappings}]


async def call_llm_for_sheet_and_relationship_mapping(
    file_state: FileState,
    column_mappings: List[ColumnMapping],
) -> Tuple[List[SheetMapping], List[SheetRelationship]]:
    """Use the LLM to infer sheet-level mappings and relationships.

    Expects a JSON object with keys 'sheet_mappings' and 'relationships'.
    """

    payload = {
        "file_state": file_state,
        "column_mappings": column_mappings,
    }
    system_prompt = """You are a data pipeline reconstruction expert.

                Input:
                - Column mappings for MULTIPLE output sheets
                - Input sheet metadata
                - Output sheet metadata (ALL sheets)

                Task:
                For EACH output sheet:
                Reconstruct the pipeline used to generate that sheet.

                IMPORTANT:
                - You MUST process ALL output sheets
                - DO NOT stop after first sheet
                - DO NOT return a single object

                Return ONLY valid JSON (not string) in this structure:

                {
                "sheet_mappings": [
                    {
                    "output_sheet": "string",
                    "pipeline": [
                        {
                        "step": 1,
                        "operation": "join",
                        "left_sheet": "string",
                        "right_sheet": "string",
                        "join_keys": [
                            {
                            "left_column": "string",
                            "right_column": "string"
                            }
                        ],
                        "join_type": "inner | left | right | full | unknown",
                        "cardinality": "one_to_one | one_to_many | many_to_one | many_to_many | unknown",
                        "confidence": "float"
                        },
                        {
                        "step": 2,
                        "operation": "filter",
                        "columns_involved": ["string"],
                        "filter_type": "row_reduction | conditional_filter | unknown",
                        "confidence": "float"
                        }
                    ]
                    }
                ]
                }

                Rules:
                - MUST return a list under "sheet_mappings"
                - MUST include ALL output sheets
                - DO NOT return JSON as string
                - DO NOT skip any sheet
                - If no operations found, return empty pipeline
                - process output sheets one by one and ensure each sheet has its own pipeline entry.
                    """


    client = LLMClient()
    response_text = await client.generate_response(
        system_prompt=system_prompt,
        context=json.dumps(payload),
    )
    # print("**********************"*10)
    # print("call_llm_for_sheet_and_relationship_mapping")
    # print(response_text)
    # print("**********************"*10)
    try:
        parsed: Any = json.loads(response_text)
    except json.JSONDecodeError:
        return [], []

    sheet_mappings = parsed.get("sheet_mappings", []) if isinstance(parsed, dict) else []
    # relationships = parsed.get("relationships", []) if isinstance(parsed, dict) else []
    # sheet_mappings= response_text

    # return sheet_mappings, relationships
    return sheet_mappings


async def run_full_analysis(input_df:pd.DataFrame, input_sheet_name: str, output_df:pd.DataFrame, output_sheet_name: str) -> FinalAnalysisState:
    """High-level helper that runs the whole analysis pipeline on a FileState.

    1) Compute possible column matches (pure Python).
    2) Call LLM for semantic column mappings.
    3) Call LLM for sheet mappings and relationships.
    """

    # possible_matches = build_possible_column_matches(file_state)
    possible_matches = columns_mappings.detect_column_mapping(input_df,output_df)
    # print("**********************"*10)
    # print(".........possible_matches..........",possible_matches)
    # print("**********************"*10)
    import asyncio
    try:
        column_mappings, llm_detected_transformations = await asyncio.gather(
            call_llm_for_column_mapping(possible_matches),
            detect_transformations_for_session(input_df, input_sheet_name, output_df, output_sheet_name),
        )
        detection_response_str = str(llm_detected_transformations).replace('{', '{{').replace('}', '}}')
    except Exception as e:
        print(f"[run_full_analysis] gather failed: {e}")
        raise e
    print(f"[run_full_analysis] LLM column_mappings :\n{column_mappings}")
    # sheet_mappings, relationships = await call_llm_for_sheet_and_relationship_mapping(
    #     file_state, column_mappings
    # )

    # sheet_mappings= await call_llm_for_sheet_and_relationship_mapping(
    #     file_state, column_mappings
    # )
    result: FinalAnalysisState = {        
        "column_mappings": column_mappings,
        "sheet_mappings": "sheet_mappings",
        "transformations_llm_detected": detection_response_str,
        # "relationships": relationships,
    }

    return result
