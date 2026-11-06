# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5/6/7 - Pipeline orchestration (loader->analysis->detection->config).
# REUSABLE but REPURPOSE. Rework: remove the quarantined engine nodes
# (validation_check / phase_executor imports from dead_code), and add IR-build +
# deterministic JSON compiler + external-API execution nodes (Deliverable 8).
# ============================================================================
"""
graph_builder_execution.py
============================
LangGraph workflow — end-to-end pipeline:

  file_loader
      ↓
  analysis
      ↓
  sheet_validation
      ↓
  phase_transformation   ← LLM-corrects the auto-detected phase config
      ↓                     Saves to  data/generated_configs/
  phase_executor         ← runs execution phases 0-10 in order
      ↓                     Saves to  data/executed_outputs/
  END

State keys
----------
  phase_transformation_result : dict
      • session_id
      • original_config
      • corrected_config        ← used by phase_executor
      • column_mapping
      • corrections_made
      • correction_success
      • config_saved_to         ← data/generated_configs/<id>_corrected_config.yaml

  phase_executor_result : dict
      • success
      • session_id
      • input_path
      • output_path             ← data/executed_outputs/<id>/output.<ext>
      • sheet_name
      • output_shape
      • total_s
      • phase_steps
      • error

Usage
-----
    import asyncio
    from graph_builder_execution import run_full_pipeline

    result_state = asyncio.run(run_full_pipeline("160cf6ce-bb3b-4d0d-9d45-18645e791e2f"))

    print(result_state["phase_transformation_result"]["config_saved_to"])
    print(result_state["phase_executor_result"]["output_path"])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph, END

from states import final_analysis_state
from states.filestate import FileState
from states.final_analysis_state import FinalAnalysisState

from agents.file_loader_agent           import FileLoaderAgent
from agents.file_analysis_agent         import FileAnalysisAgent
from agents.transformation_agent_old  import PhaseTransformationAgent
from agents.transformation_agent import TransformationAgent
from agents.json_config_editor_agent import JsonConfigEditorAgent
from knowledge.config_store import store_draft_json, get_config_json_for_session

from llm_client import LLMClient
from utils.file_loader_util import load_sheets_from_file_state
import pandas as pd
import os
import yaml
from utils.serializer import make_serializable

# ─────────────────────────────────────────────────────────────────────────────
# State type
# ─────────────────────────────────────────────────────────────────────────────

FullPipelineState = dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
# Nodes
# ─────────────────────────────────────────────────────────────────────────────

async def _file_loader_node(state: FullPipelineState) -> FullPipelineState:
    """Load single input and output files for the session."""
    session_id = state.get("session_id")
    if not session_id:
        raise ValueError("session_id must be provided in the initial state")

    loader = FileLoaderAgent()
    print("=" * 60)
    print(f"[file_loader] Loading files for session: {session_id}")
    file_state: FileState = await loader.load_session_files(session_id=session_id)
    state["file_state"] = file_state
    print("[file_loader] Files loaded successfully")
    return state

async def _analysis_node(state: FullPipelineState) -> FullPipelineState:
    """Analyse single sheet from input and output files."""
    file_state: FileState = state.get("file_state", {})
    if not file_state:
        raise ValueError("file_state missing; file_loader must run first")
    input_sheets  = load_sheets_from_file_state(file_state, "input_files")
    output_sheets = load_sheets_from_file_state(file_state, "output_files")
    try:
        for filename,sheets in input_sheets.items():
            for sheet_name, df in sheets.items():
                input_df = df
                input_sheet_name = sheet_name
                print(f"[analysis] found input : {filename} | Sheet: {sheet_name}  ")
                break
            break
        for filename, sheets in output_sheets.items():
            for sheet_name, df in sheets.items():
                output_df = df
                output_sheet_name = sheet_name
                print(f"[analysis] found output : {filename} | Sheet: {sheet_name}  ")
                break
            break
    except Exception as e:
        raise ValueError(f"No sheets found : {e}")
    print(f"[analysis] input df shape: {input_df.shape}")
    print(f"[analysis] output df shape: {output_df.shape}")
    agent = FileAnalysisAgent()
    print("=" * 60)
    print("[analysis] Running analysis on single sheet")
    analysis: FinalAnalysisState = await agent.analyze(input_df, input_sheet_name, output_df, output_sheet_name)
    state["analysis"] = analysis
    print("[analysis] Analysis completed")
    # ── Save analysis state to YAML ───────────────────────────────────────────
    analysis_dir = Path(__file__).resolve().parent / "data"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_state_path = analysis_dir / f"{state.get('session_id', 'unknown')}_analysis_state.yaml"

    with open(analysis_state_path, "w", encoding="utf-8") as f:
        yaml.dump(
            make_serializable(dict(state)),
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"[analysis] ✅ Analysis state saved → {analysis_state_path}")

    return state

async def _phase_transformation_node(state: FullPipelineState) -> FullPipelineState:
    """
    LLM-corrects the auto-detected phase config produced by
    GetConfigurationService, then saves the corrected YAML to
    data/generated_configs/<session_id>_corrected_config.yaml.

    Reads  : state["session_id"], state["analysis"]
    Writes : state["phase_transformation_result"]
    """
    session_id: str              = state.get("session_id", "")
    analysis:   FinalAnalysisState = state.get("analysis", {})

    print("=" * 60)
    print(f"[phase_transformation] Running PhaseTransformationAgent for session: {session_id}")
    file_state: FileState = state.get("file_state", {})
    session_id: str              = state.get("session_id", "")
    analysis:   FinalAnalysisState = state.get("analysis", {})
    if not file_state:
        raise ValueError("file_state missing; file_loader must run first")
    input_sheets  = load_sheets_from_file_state(file_state, "input_files")
    output_sheets = load_sheets_from_file_state(file_state, "output_files")
    try:
        for filename,sheets in input_sheets.items():
            for sheet_name, df in sheets.items():
                input_df = df
                print(f"[_transformation_node] found input : {filename} | Sheet: {sheet_name}  ")
                break
            break
        for filename, sheets in output_sheets.items():
            for sheet_name, df in sheets.items():
                output_df = df
                print(f"[analysis] found output : {filename} | Sheet: {sheet_name}  ")
                break
            break
    except Exception as e:
        raise ValueError(f"No sheets found : {e}")
    print(f"[_transformation_node] input df shape: {input_df.shape}")
    print(f"[_transformation_node] output df shape: {output_df.shape}")
    # Inject session_id into analysis so TransformationService uses the correct id
    if isinstance(analysis, dict) and session_id:
        analysis = {**analysis, "session_id": session_id}
    llm_client = LLMClient()
    agent      = PhaseTransformationAgent(
        llm_client            = llm_client,
        save_corrected_config = True
    )

    result: dict[str, Any] = await agent.run(
        session_id           = session_id,
        final_analysis_state = analysis,        
        input_df               = input_df,
        output_df              = output_df,
    )

    state["phase_transformation_result"] = result

    # config_saved_to = result.get("config_saved_to")
    # if config_saved_to:
    #     print(f"[phase_transformation] ✅ Config saved → {config_saved_to}")

    #     # ── Append sheet_validations into the saved YAML config ───────────────
    #     # This is the single source of truth for /execute_transformation to
    #     # reload validation rules — no separate file needed.
    #     sheet_validations = state.get("sheet_validations") or {}
    #     if sheet_validations:
    #         import yaml
    #         from utils.serializer import make_serializable
    #         yaml_path = Path(config_saved_to)
    #         if yaml_path.exists():
    #             with open(yaml_path, "r", encoding="utf-8") as f:
    #                 saved_config = yaml.safe_load(f) or {}
    #             saved_config["sheet_validations"] = make_serializable(sheet_validations)
    #             with open(yaml_path, "w", encoding="utf-8") as f:
    #                 yaml.dump(
    #                     saved_config, f,
    #                     default_flow_style=False,
    #                     allow_unicode=True,
    #                     sort_keys=False,
    #                 )
    #             print(f"[phase_transformation] ✅ sheet_validations appended → {yaml_path}")
    #         else:
    #             print(f"[phase_transformation] ⚠️  YAML not found to append validations: {yaml_path}")
    #     else:
    #         print("[phase_transformation] ⚠️  No sheet_validations in state — skipping append")
    # else:
    #     print("[phase_transformation] ⚠️  Config not saved — original config will be used for execution")

    # return state
    # ── Save full pipeline state to YAML ──────────────────────────────────────
    configs_dir     = Path(__file__).resolve().parent / "data" / "generated_configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    full_state_path = configs_dir / f"{session_id}_full_state.yaml"

    with open(full_state_path, "w", encoding="utf-8") as f:
        yaml.dump(make_serializable(dict(state)),
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    state["full_state_saved_to"] = str(full_state_path)
    print(f"[phase_transformation] ✅ Full state saved → {full_state_path}")

    return state

async def _transformation_node(state: FullPipelineState) -> FullPipelineState: 
    file_state: FileState = state.get("file_state", {})
    session_id: str              = state.get("session_id", "")
    analysis:   FinalAnalysisState = state.get("analysis", {})
    if not file_state:
        raise ValueError("file_state missing; file_loader must run first")
    input_sheets  = load_sheets_from_file_state(file_state, "input_files")
    output_sheets = load_sheets_from_file_state(file_state, "output_files")
    try:
        for filename,sheets in input_sheets.items():
            for sheet_name, df in sheets.items():
                input_df = df
                print(f"[_transformation_node] found input : {filename} | Sheet: {sheet_name}  ")
                break
            break
        for filename, sheets in output_sheets.items():
            for sheet_name, df in sheets.items():
                output_df = df
                print(f"[analysis] found output : {filename} | Sheet: {sheet_name}  ")
                break
            break
    except Exception as e:
        raise ValueError(f"No sheets found : {e}")
    print(f"[_transformation_node] input df shape: {input_df.shape}")
    print(f"[_transformation_node] output df shape: {output_df.shape}")
    # Inject session_id into analysis so TransformationService uses the correct id
    if isinstance(analysis, dict) and session_id:
        analysis = {**analysis, "session_id": session_id}

    agent = TransformationAgent()
    response = await agent.analyze(
        file_analysis_state = analysis,
        input_df            = input_df,
        output_df           = output_df,
    )
    # Convert TransformationResult dataclass → plain dict so that
    # state serialisation (yaml.dump) and downstream .get() calls work.
    # Always use session_id from graph state — response.session_id may be
    # "unknown_session" if the analysis dict did not carry the session_id key.
    state["phase_transformation_result"] = {
        "session_id":         session_id,
        "corrected_config":   response.config,
        "original_config":    response.config,
        "column_mapping":     response.column_mapping,
        "config_saved_to":    response.config_saved_to,
        "generated_at":       response.generated_at,
        "correction_success": response.error is None,
        "corrections_made":   [],
        "error":              response.error,
    }
    configs_dir     = Path(__file__).resolve().parent / "data" / "generated_configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    full_state_path = configs_dir / f"{session_id}_full_state.yaml"

    with open(full_state_path, "w", encoding="utf-8") as f:
        yaml.dump(make_serializable(dict(state)),
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    state["full_state_saved_to"] = str(full_state_path)
    print(f"[phase_transformation] ✅ Full state saved → {full_state_path}")
    return state
# ─────────────────────────────────────────────────────────────────────────────
# Graph builder
# ─────────────────────────────────────────────────────────────────────────────

def build_graph():
    """
    Compile the LangGraph workflow.

    file_loader → analysis → sheet_validation → phase_transformation → phase_executor → END
    """
    graph = StateGraph(FullPipelineState)

    graph.add_node("file_loader",          _file_loader_node)
    graph.add_node("analysis",             _analysis_node)
    graph.add_node("phase_transformation", _phase_transformation_node)

    graph.set_entry_point("file_loader")
    graph.add_edge("file_loader",          "analysis")
    graph.add_edge("analysis",             "phase_transformation")
    graph.add_edge("phase_transformation", END)

    return graph.compile()

def build_transformation_only_graph():
    """
    _transformation_node → END
    """
    graph = StateGraph(FullPipelineState)

    graph.add_node("_transformation_node", _transformation_node)

    graph.set_entry_point("_transformation_node")
    graph.add_edge("_transformation_node", END)

    return graph.compile()

# ─────────────────────────────────────────────────────────────────────────────
# Convenience entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_full_pipeline(session_id: str) -> dict:
    """
    Run the complete end-to-end pipeline for a session.

    Returns the final graph state dict.  Key results:

        state["phase_transformation_result"]["config_saved_to"]
            → data/generated_configs/<session_id>_corrected_config.yaml

        state["phase_executor_result"]["output_path"]
            → data/executed_outputs/<session_id>/output.<ext>

        state["phase_executor_result"]["success"]   → True / False
        state["phase_executor_result"]["phase_steps"] → per-phase breakdown
    """
    graph = build_graph()

    print("\n" + "=" * 60)
    print(f"  🚀  Full Pipeline  —  session: {session_id}")
    print("=" * 60 + "\n")

    result_state = await graph.ainvoke({"session_id": session_id})

    # ── Final summary ─────────────────────────────────────────────────────────
    pt = result_state.get("phase_transformation_result", {})
    pe = result_state.get("phase_executor_result",       {})

    print("\n" + "=" * 60)
    print("  ✅  Full Pipeline completed")
    print(f"  State keys : {list(result_state.keys())}")
    print("\n  📄  Config")
    print(f"      correction_success : {pt.get('correction_success')}")
    print(f"      config_saved_to    : {pt.get('config_saved_to')}")
    if pt.get('error'):
        print(f"      error              : {pt.get('error')}")
    print("\n  📦  Execution")
    print(f"      success            : {pe.get('success')}")
    print(f"      output_path        : {pe.get('output_path')}")
    print(f"      output_shape       : {pe.get('output_shape')}")
    print(f"      total_s            : {pe.get('total_s')}s")

    for s in (pe.get("phase_steps") or []):
        badge = {"ok": "✅", "error": "❌", "skipped": "⏭️ "}.get(s.get("status"), "❓")
        shape = f"{s.get('rows_out')}r × {s.get('cols_out')}c"
        print(f"    {badge} {s.get('phase_name',''):<35} {s.get('duration_s', 0):>6.3f}s  [{shape}]"
              + (f"  ← {s['error']}" if s.get("error") else ""))

    print("=" * 60 + "\n")
    return result_state

async def run_transformation_pipeline(session_id: str) -> dict:
    """
    Run only the phase_transformation → phase_executor steps.

    Skips file_loader / analysis / sheet_validation.
    Requires that input/output files already exist under:
        data/input_files/<session_id>/
        data/output_files/<session_id>/

    Returns the final graph state dict.  Key results:

        state["phase_transformation_result"]["config_saved_to"]
            → data/generated_configs/<session_id>_corrected_config.yaml

        state["phase_executor_result"]["output_path"]
            → data/executed_outputs/<session_id>/output.<ext>

        state["phase_executor_result"]["success"]     → True / False
        state["phase_executor_result"]["phase_steps"] → per-phase breakdown
    """

    graph = build_transformation_only_graph()

    print("\n" + "=" * 60)
    print(f"  🚀  Transformation + Executor Pipeline  —  session: {session_id}")
    print("=" * 60 + "\n")

    # ── Resolve file paths so _sheet_validation_node can load them ────────────
    input_files:  dict = {}
    output_files: dict = {}

    input_folder  = Path(__file__).resolve().parent / "data" / "input_files"  / session_id
    output_folder = Path(__file__).resolve().parent / "data" / "output_files" / session_id

    if input_folder.exists():
        for f in input_folder.glob("*.xls*"):
            input_files[f.name] = {"path": str(f)}

    if output_folder.exists():
        for f in output_folder.glob("*.xls*"):
            output_files[f.name] = {"path": str(f)}

    result_state = await graph.ainvoke({
        "session_id": session_id,
        "analysis":   {},
        "file_state": {
            "input_files":  input_files,
            "output_files": output_files,
        },
    })

    # ── Final summary ─────────────────────────────────────────────────────────
    pt = result_state.get("phase_transformation_result", {})
    pe = result_state.get("phase_executor_result",       {})

    print("\n" + "=" * 60)
    print("  ✅  Transformation + Executor Pipeline completed")
    print(f"  State keys : {list(result_state.keys())}")
    print("\n  📄  Config")
    print(f"      correction_success : {pt.get('correction_success')}")
    print(f"      config_saved_to    : {pt.get('config_saved_to')}")   # ← fixed print.f typo
    if pt.get('error'):
        print(f"      error              : {pt.get('error')}")
    print("\n  📦  Execution")
    print(f"      success            : {pe.get('success')}")
    print(f"      output_path        : {pe.get('output_path')}")
    print(f"      output_shape       : {pe.get('output_shape')}")
    print(f"      total_s            : {pe.get('total_s')}s")

    for s in (pe.get("phase_steps") or []):
        badge = {"ok": "✅", "error": "❌", "skipped": "⏭️ "}.get(s.get("status"), "❓")
        shape = f"{s.get('rows_out')}r × {s.get('cols_out')}c"
        print(f"    {badge} {s.get('phase_name',''):<35} {s.get('duration_s', 0):>6.3f}s  [{shape}]"
              + (f"  ← {s['error']}" if s.get("error") else ""))

    print("=" * 60 + "\n")
    return result_state

# ─────────────────────────────────────────────────────────────────────────────
# Config Editor Graph
# ─────────────────────────────────────────────────────────────────────────────

ConfigEditorState = dict[str, Any]


async def _load_config_node(state: ConfigEditorState) -> ConfigEditorState:
    """Load the latest JSON config from S3 if not already in state.

    Priority:
      1. current_json already in state (from a previous turn / provided by caller)
      2. drafts/{session_id}/ - latest draft JSON for this session
      3. confirmed/validation_transformation_config.json - base confirmed config
    """
    if state.get("current_json"):
        return state
    json_content = get_config_json_for_session(
        bu=state["bu"],
        platform=state["platform"],
        category=state["category"],
        session_id=state["session_id"],
    ) or ""
    state["current_json"] = json_content
    return state


_S3_CONFIGS_BUCKET = os.getenv("S3_CONFIGS_BUCKET", "agentic-dvts-configs")


async def _llm_edit_node(state: ConfigEditorState) -> ConfigEditorState:
    """Call the JSON editor for one turn and save a draft when changes are made."""
    print(f"[_llm_edit_node] START - session={state['session_id']}")
    agent = JsonConfigEditorAgent(session_id=state["session_id"])

    try:
        result = await agent.run(
            bu=state["bu"],
            platform=state["platform"],
            category=state["category"],
            user_query=state["user_query"],
            current_json=state["current_json"],
            config_version=state.get("config_version", ""),
        )
        if not isinstance(result, dict):
            raise TypeError(f"agent.run() returned {type(result)}, expected dict")
    except Exception as e:
        print(f"[_llm_edit_node] agent.run() raised: {type(e).__name__}: {e}")
        raise

    state["reply"]               = result["reply"]
    state["confirmed"]           = result["confirmed"]
    state["intent"]              = result.get("intent", "edit")
    state["validation_warnings"] = result.get("validation_warnings", [])
    state["affected_section"]    = result.get("affected_section")
    state["affected_uuid"]       = result.get("affected_uuid")
    state["affected_columns"]    = result.get("affected_columns", [])
    state["changes"]             = result.get("changes", [])
    state["changes_display"]     = result.get("changes_display", "")
    state["summary"]             = result.get("summary")

    # CANCEL: signal a reload from the confirmed config.
    if state["intent"] == "cancel":
        state["proposed_json"] = ""
        state["confirmed"] = False
        return state

    proposed = result.get("proposed_json") or ""
    if proposed and proposed != state["current_json"]:
        state["proposed_json"] = proposed
        try:
            draft_key = store_draft_json(
                bu=state["bu"],
                platform=state["platform"],
                category=state["category"],
                session_id=state["session_id"],
                json_content=proposed,
                s3_bucket=_S3_CONFIGS_BUCKET,
            )
            print(f"[ConfigEditor] Saved JSON draft: {draft_key}")
        except Exception as e:
            print(f"[ConfigEditor] Warning: Failed to save JSON draft: {e}")
    else:
        # Clarifying question or no change — keep the current JSON.
        state["proposed_json"] = state["current_json"]

    return state


async def _save_config_node(state: ConfigEditorState) -> ConfigEditorState:
    """Save confirmed JSON to S3."""
    agent = JsonConfigEditorAgent(session_id=state["session_id"])
    try:
        save_status = await agent.save(
            bu=state["bu"],
            platform=state["platform"],
            category=state["category"],
            json_content=state["proposed_json"],
        )
        print(f"[_save_config_node] ✅ Save completed: {save_status}")
    except Exception as e:
        print(f"[_save_config_node] ❌ Save FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise

    state["save_status"] = save_status
    return state


def _route_after_edit(state: ConfigEditorState) -> str:
    confirmed = state.get("confirmed")
    print(f"[_route_after_edit] confirmed={confirmed} → routing to {'save_config' if confirmed else 'END'}")
    return "save_config" if confirmed else END


def build_config_editor_graph():
    """
    load_config → llm_edit → (confirmed?) save_config → END
                                         (not confirmed) → END
    """
    graph = StateGraph(ConfigEditorState)

    graph.add_node("load_config", _load_config_node)
    graph.add_node("llm_edit",    _llm_edit_node)
    graph.add_node("save_config", _save_config_node)

    graph.set_entry_point("load_config")
    graph.add_edge("load_config", "llm_edit")
    graph.add_conditional_edges(
        "llm_edit",
        _route_after_edit,
        {"save_config": "save_config", END: END},
    )
    graph.add_edge("save_config", END)

    return graph.compile()


async def run_config_editor_pipeline(
    bu: str,
    platform: str,
    category: str,
    session_id: str,
    user_query: str,
    current_json: str = "",
    config_version: str = "",
) -> dict:
    graph = build_config_editor_graph()
    return await graph.ainvoke({
        "bu":             bu,
        "platform":       platform,
        "category":       category,
        "session_id":     session_id,
        "user_query":     user_query,
        "current_json":   current_json,
        "config_version": config_version,
    })

