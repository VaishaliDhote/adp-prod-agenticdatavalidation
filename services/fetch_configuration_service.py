# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 8/10 - Generate -> store -> auto-execute -> compare -> upload pipeline.
# REUSABLE but REPURPOSE. Rework: swap the Python executor pipeline for the EXTERNAL
# API (tasks 5-11); retarget stored config from YAML to the exact JSON contract.
# ============================================================================
"""
services/fetch_configuration_service.py
=========================================
FetchConfigurationService
--------------------------
Encapsulates all business logic for the GET /get_configuration endpoint.

Pipeline steps
~~~~~~~~~~~~~~
  1. Create local per-session folders  (input_files / output_files).
  2. Download INPUT_REF + OUTPUT_REF files from DynamoDB/S3 into those folders.
  3. Run the LangGraph full pipeline
       file_loader → analysis → sheet_validation → phase_transformation.
  4. Persist the generated YAML as a draft config in S3
     and write a local copy so the executor can read it.
  5. Auto-execute using the INPUT_REF file already on disk via the
     executor-only pipeline (validation_check → phase_executor).
  6. Upload the execution output (or validation-error report) to S3.
  7. Return a structured FetchConfigurationResult.

The FastAPI route handler in main.py is responsible only for:
  - Extracting / validating query parameters.
  - Calling this service.
  - Mapping the result to an HTTP response.

Note on the executor pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``graph_builder.run_executor_only_pipeline`` drives the executor graph
(``validation_check → phase_executor → END``).  That graph is invoked here
for the auto-execution step using the INPUT_REF reference file.  The
separate /execute_transformation endpoint uses the same graph against
a *user-submitted* INPUT_FILE after the config has been confirmed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from graph_builder import run_full_pipeline
from storage.dynamodb.business_repository import upsert_category_summary
from storage.dynamodb.document_repository import (
    fetch_documents_by_session,
    insert_executed_output_document,
)
from storage.s3.config_repository import save_draft_config
from storage.s3.file_repository import (
    download_file_bytes,
    generate_presigned_download_url,
    upload_file_bytes,
)
from utils.path_utils import create_data_folder, sanitize_filename
from utils.serializer import make_serializable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory layout — mirrors the constants in main.py
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_INPUT_BASE_DIR  = _ROOT / "data" / "input_files"
_OUTPUT_BASE_DIR = _ROOT / "data" / "output_files"
_CONFIG_BASE_DIR = _ROOT / "data" / "generated_configs"
_EXECUTED_OUTPUT_DIR = _ROOT / "data" / "executed_outputs"
_DEFAULT_S3_BUCKET = "agentic-data-validate-tranform-files"


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FetchConfigurationResult:
    """Returned by FetchConfigurationService.run()."""

    session_id:     str
    business_unit:  str
    platform:       str
    category:       str
    summary:        Optional[Any]  = None
    timings:        Optional[Any]  = None
    draft_s3_key:   Optional[str]  = None
    execution_id:   Optional[str]  = None
    execution:      Dict[str, Any] = field(default_factory=dict)
    download_url:   Optional[str]  = None
    output_filename: Optional[str] = None

    @property
    def success(self) -> bool:
        return True  # errors are raised, not captured


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FetchConfigurationService:
    """
    Orchestrates config generation and auto-execution for a session.

    Usage::

        svc    = FetchConfigurationService()
        result = await svc.run(
            session_id    = "abc-123",
            business_unit = "FinanceOps",
            platform      = "SAP",
            category      = "GL",
        )
    """

    def __init__(
        self,
        input_base_dir: Path      = _INPUT_BASE_DIR,
        output_base_dir: Path     = _OUTPUT_BASE_DIR,
        config_base_dir: Path     = _CONFIG_BASE_DIR,
        executed_output_dir: Path = _EXECUTED_OUTPUT_DIR,
        s3_files_bucket: Optional[str] = None,
    ) -> None:
        self._input_base_dir      = input_base_dir
        self._output_base_dir     = output_base_dir
        self._config_base_dir     = config_base_dir
        self._executed_output_dir = executed_output_dir
        self._s3_bucket = s3_files_bucket or os.getenv("S3_FILES_BUCKET", _DEFAULT_S3_BUCKET)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        session_id:    str,
        business_unit: str,
        platform:      str,
        category:      str,
    ) -> FetchConfigurationResult:
        """Full config-generation + auto-execution flow.

        Raises RuntimeError (with a descriptive step label) on unrecoverable
        failures.  Non-critical steps (S3 persist, auto-execution) log warnings
        and continue so the caller always receives a partial result.
        """
        # Step 1 & 2 — folders + S3 download
        logger.info("[FetchConfigurationService] ── Step 1/6  Preparing local session folders  session=%s", session_id)
        try:
            input_folder, output_folder = self._prepare_folders(session_id)
        except Exception as exc:
            logger.exception("[FetchConfigurationService] Failed to create local folders for session %s", session_id)
            raise RuntimeError(f"Failed to prepare local session folders: {exc}") from exc

        logger.info("[FetchConfigurationService] ── Step 2/6  Downloading reference files from S3  session=%s", session_id)
        try:
            await self._download_reference_files(session_id, input_folder, output_folder)
        except Exception as exc:
            logger.exception("[FetchConfigurationService] Failed to fetch reference documents for session %s", session_id)
            raise RuntimeError(f"Failed to download reference files: {exc}") from exc

        # Step 3 — LangGraph pipeline (critical — must succeed)
        logger.info("[FetchConfigurationService] ── Step 3/6  Running LangGraph full pipeline  session=%s", session_id)
        try:
            analysis_state = await self._run_pipeline(session_id)
        except Exception as exc:
            logger.exception("[FetchConfigurationService] LangGraph pipeline failed for session %s", session_id)
            raise RuntimeError(f"Configuration pipeline failed: {exc}") from exc

        # Step 4 — persist draft (non-critical — failure is logged, not raised)
        logger.info("[FetchConfigurationService] ── Step 4/6  Persisting draft config to S3  session=%s", session_id)
        draft_s3_key = await self._persist_draft(
            analysis_state, business_unit, platform, category, session_id
        )
        if draft_s3_key:
            analysis_state["draft_s3_key"] = draft_s3_key

        # Step 5 & 6 — auto-execute with INPUT_REF already on disk (non-critical)
        logger.info("[FetchConfigurationService] ── Step 5/6  Auto-executing with INPUT_REF  session=%s", session_id)
        # execution_result, download_url, 3, execution_id = \
        #     await self._auto_execute(
        #         session_id    = session_id,
        #         business_unit = business_unit,
        #         platform      = platform,
        #         category      = category,
        #         input_folder  = input_folder,
        #     )

        logger.info("[FetchConfigurationService] ── Step 6/6  Building result  session=%s", session_id)
        pt = (analysis_state.get("phase_transformation_result") or {})
        summary = pt.get("summary")

        # ── Persist summary to DynamoDB BusinessPlatformCategoryTable ──────────
        if summary:
            try:
                upsert_category_summary(
                    bu       = business_unit,
                    platform = platform,
                    category = category,
                    summary  = summary,
                )
                logger.info(
                    "[FetchConfigurationService] Summary saved to DynamoDB for BU=%s PLATFORM=%s CATEGORY=%s",
                    business_unit, platform, category,
                )
            except Exception as exc:
                logger.warning("[FetchConfigurationService] Failed to save summary to DynamoDB: %s", exc)

        return FetchConfigurationResult(
            session_id      = session_id,
            business_unit   = business_unit,
            platform        = platform,
            category        = category,
            summary         = summary,
            timings         = pt.get("timings"),
            draft_s3_key    = analysis_state.get("draft_s3_key"),
            # execution_id    = execution_id,
            # execution       = execution_result,
            # download_url    = download_url,
            # output_filename = output_filename,
        )

    # ------------------------------------------------------------------
    # Step 1 – local folder creation
    # ------------------------------------------------------------------

    def _prepare_folders(self, session_id: str):
        """Create per-session input / output directories and return their paths."""
        input_folder  = create_data_folder(self._input_base_dir,  session_id)
        output_folder = create_data_folder(self._output_base_dir, session_id)
        logger.info("[FetchConfigurationService] Folders ready for session: %s", session_id)
        return input_folder, output_folder

    # ------------------------------------------------------------------
    # Step 2 – download reference files from S3
    # ------------------------------------------------------------------

    async def _download_reference_files(
        self,
        session_id:    str,
        input_folder:  Path,
        output_folder: Path,
    ) -> None:
        """
        Fetch INPUT_REF / OUTPUT_REF metadata from DynamoDB and download
        each completed file from S3 into the local session folders.
        Pipeline nodes use glob("*.xls*") on these folders, so files
        must land directly under data/{input|output}_files/<session_id>/.

        Raises RuntimeError if the DynamoDB metadata fetch fails (we cannot
        proceed without knowing which files to download).  Individual file
        download failures are logged as warnings and skipped so that a single
        corrupt/missing S3 object does not abort the entire flow.
        """
        input_docs  = fetch_documents_by_session(session_id, "INPUT_REF")
        output_docs = fetch_documents_by_session(session_id, "OUTPUT_REF")

        if not input_docs:
            logger.warning(
                "[FetchConfigurationService] No INPUT_REF documents found for session %s — "
                "pipeline will attempt to use any files already on disk.",
                session_id,
            )
        if not output_docs:
            logger.warning(
                "[FetchConfigurationService] No OUTPUT_REF documents found for session %s — "
                "pipeline will attempt to use any files already on disk.",
                session_id,
            )

        logger.info(
            "[FetchConfigurationService] session=%s  input_refs=%d  output_refs=%d",
            session_id, len(input_docs), len(output_docs),
        )
        logger.info("[FetchConfigurationService] input_docs=%s", input_docs)
        logger.info("[FetchConfigurationService] output_docs=%s", output_docs)

        self._download_docs(input_docs,  input_folder,  label="input_ref")
        self._download_docs(output_docs, output_folder, label="output_ref")

    def _download_docs(
        self,
        docs:          List[Dict[str, Any]],
        target_folder: Path,
        label:         str,
    ) -> None:
        for doc in docs:
            if doc.get("upload_status") != "completed":
                logger.info(
                    "[FetchConfigurationService] Skipping %s '%s': upload_status='%s'",
                    label, doc.get("filename", ""), doc.get("upload_status"),
                )
                continue
            raw_filename = doc.get("filename", "")
            try:
                s3_key = doc.get("s3_key")
                if not s3_key:
                    logger.warning(
                        "[FetchConfigurationService] Skipping %s '%s': missing s3_key",
                        label, raw_filename,
                    )
                    continue

                bucket   = doc.get("s3_bucket") or self._s3_bucket
                filename = sanitize_filename(raw_filename)

                file_bytes = download_file_bytes(s3_key, bucket=bucket)
                logger.info(
                    "[FetchConfigurationService] %s '%s' downloaded: %d bytes",
                    label, raw_filename, len(file_bytes) if file_bytes else 0,
                )
                if not file_bytes:
                    logger.warning(
                        "[FetchConfigurationService] S3 returned 0 bytes for %s '%s' (key=%s). "
                        "The file was likely uploaded with an empty body via the presigned URL. "
                        "Re-upload the file and call /confirm_upload again.",
                        label, raw_filename, s3_key,
                    )
                    continue

                file_path = target_folder / filename
                # Security: resolved path must stay inside target_folder
                file_path.resolve().relative_to(target_folder.resolve())
                with open(file_path, "wb") as fh:
                    fh.write(file_bytes)

                logger.info(
                    "[FetchConfigurationService] Downloaded %s: %s → %s",
                    label, filename, file_path,
                )
            except Exception as exc:
                logger.warning(
                    "[FetchConfigurationService] Could not download %s '%s': %s",
                    label, raw_filename, exc,
                )

    # ------------------------------------------------------------------
    # Step 3 – LangGraph full pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, session_id: str) -> Dict[str, Any]:
        """
        Run: file_loader → analysis → sheet_validation → phase_transformation.
        Returns the final pipeline state dict.

        Raises RuntimeError if the pipeline returns no state or raises internally.
        """
        logger.info("[FetchConfigurationService] Starting full pipeline: session=%s", session_id)
        state = await run_full_pipeline(session_id=session_id)
        if not state:
            raise RuntimeError(
                f"LangGraph pipeline returned empty state for session '{session_id}'. "
                "Ensure input and output reference files were downloaded successfully."
            )
        logger.info("[FetchConfigurationService] Pipeline complete. keys=%s", list(state.keys()))
        return state

    # ------------------------------------------------------------------
    # Step 4 – persist draft config
    # ------------------------------------------------------------------

    async def _persist_draft(
        self,
        analysis_state: Dict[str, Any],
        business_unit:  str,
        platform:       str,
        category:       str,
        session_id:     str,
    ) -> Optional[str]:
        """
        Serialise the pipeline state to YAML, save a draft to S3,
        and write a local copy for the executor pipeline.
        Returns the S3 key, or None on failure.
        """
        yaml_payload = analysis_state.get("full_state") or analysis_state
        try:
            yaml_content: str = (
                yaml_payload
                if isinstance(yaml_payload, str)
                else yaml.safe_dump(
                    make_serializable(yaml_payload), sort_keys=False, allow_unicode=True
                )
            )
        except Exception as exc:
            logger.warning(
                "[FetchConfigurationService] Could not serialise pipeline state to YAML for session %s: %s",
                session_id, exc,
            )
            return None

        if not yaml_content:
            logger.warning("[FetchConfigurationService] No YAML content generated for session %s", session_id)
            return None

        logger.info(
            "[FetchConfigurationService] YAML length=%d for session=%s",
            len(yaml_content), session_id,
        )

        # ── Save to S3 ────────────────────────────────────────────────────────
        logger.info("[FetchConfigurationService]   4a  Saving draft config to S3  session=%s", session_id)
        draft_s3_key: Optional[str] = None
        try:
            draft_s3_key = save_draft_config(
                bu=business_unit,
                platform=platform,
                category=category,
                session_id=session_id,
                yaml_content=yaml_content,
                version="default",
            )
            logger.info("[FetchConfigurationService] Draft saved to S3: %s", draft_s3_key)
        except Exception as exc:
            logger.warning("[FetchConfigurationService] Failed to save draft to S3: %s", exc)

        # ── Write local copy so executor can load it ──────────────────────────
        logger.info("[FetchConfigurationService]   4b  Writing local config YAML  session=%s", session_id)
        local_path = self._config_base_dir / f"{session_id}_full_state.yaml"
        try:
            local_path.write_text(yaml_content, encoding="utf-8")
            logger.info("[FetchConfigurationService] Local config written: %s", local_path)
        except Exception as exc:
            logger.warning("[FetchConfigurationService] Failed to write local config: %s", exc)

        return draft_s3_key

    # ------------------------------------------------------------------
    # Step 5 & 6 – auto-execute with the INPUT_REF file
    # ------------------------------------------------------------------

    async def _auto_execute(
        self,
        session_id:    str,
        business_unit: str,
        platform:      str,
        category:      str,
        input_folder:  Path,
    ):
        """
        Auto-execution using the INPUT_REF file downloaded in step 2.

        NOTE: The legacy in-process Python execution engine has been removed.
        Auto-execution is disabled until the external validation/transformation
        API (Deliverable 8) is integrated. This method is currently unreferenced
        (its only call site in the config-generation flow is commented out).

        Returns (execution_result, download_url, output_filename, execution_id).
        """
        input_ref_files = list(input_folder.glob("*.xls*"))
        if not input_ref_files:
            logger.info("[FetchConfigurationService] No INPUT_REF file found — skipping auto-execution.")
            return {}, None, None, None

        exec_input_path = input_ref_files[0]
        execution_id = f"{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(
            "[FetchConfigurationService] Auto-execution disabled — legacy executor "
            "removed; external-API execution (Deliverable 8) pending. input=%s  execution_id=%s",
            exec_input_path, execution_id,
        )
        return (
            {
                "success": False,
                "validation_passed": True,
                "error": "Execution engine not available (pending Deliverable 8 external API)",
            },
            None,
            None,
            execution_id,
        )

    async def _handle_validation_failure(
        self,
        vc:            Dict[str, Any],
        execution_id:  str,
        session_id:    str,
        business_unit: str,
        platform:      str,
        category:      str,
    ):
        """Build + upload a validation-error Excel report and return the result tuple."""
        from utils.excel_report import build_validation_error_excel

        error_report_dir = self._executed_output_dir / execution_id
        download_url:    Optional[str] = None
        output_filename: Optional[str] = None

        try:
            error_report_path = build_validation_error_excel(
                mismatched_cols = vc.get("mismatched_cols", []),
                execution_id    = execution_id,
                output_dir      = error_report_dir,
            )
            logger.info("[FetchConfigurationService] Validation error report created: %s", error_report_path)
        except Exception as exc:
            logger.warning(
                "[FetchConfigurationService] Could not build validation error Excel for execution %s: %s",
                execution_id, exc,
            )
            # Return result without a download link — the caller still gets the mismatch details
            return (
                {
                    "success":            False,
                    "validation_passed":  False,
                    "validation_message": vc.get("message", "Validation failed"),
                    "mismatched_cols":    vc.get("mismatched_cols", []),
                },
                None, None, execution_id,
            )

        try:
            err_s3_key = upload_file_bytes(
                content    = error_report_path.read_bytes(),
                session_id = session_id,
                file_role  = "executed_output_files",
                filename   = error_report_path.name,
                bu         = business_unit,
                platform   = platform,
                category   = category,
            )
            download_url    = generate_presigned_download_url(err_s3_key)
            output_filename = error_report_path.name
        except Exception as exc:
            logger.warning("[FetchConfigurationService] Could not upload validation error report: %s", exc)

        return (
            {
                "success":            False,
                "validation_passed":  False,
                "validation_message": vc.get("message", "Validation failed"),
                "mismatched_cols":    vc.get("mismatched_cols", []),
            },
            download_url,
            output_filename,
            execution_id,
        )

    async def _handle_execution_success(
        self,
        pe:              Dict[str, Any],
        exec_input_path: Path,
        execution_id:    str,
        session_id:      str,
        business_unit:   str,
        platform:        str,
        category:        str,
    ):
        """Upload the executed output to S3, record it in DynamoDB, and return the result tuple."""
        output_path = pe.get("output_path")
        if not output_path or not Path(output_path).exists():
            logger.warning(
                "[FetchConfigurationService] Execution succeeded but output missing: %s", output_path
            )
            return (
                {"success": False, "validation_passed": True,
                 "error": f"Output file not found after execution: {output_path}"},
                None, None, execution_id,
            )

        op           = Path(output_path)
        out_filename = f"{exec_input_path.stem}-executed.xlsx"
        out_s3_key:    Optional[str] = None
        download_url:  Optional[str] = None

        try:
            out_s3_key = upload_file_bytes(
                content    = op.read_bytes(),
                session_id = session_id,
                file_role  = "executed_output_files",
                filename   = out_filename,
                bu         = business_unit,
                platform   = platform,
                category   = category,
            )
            insert_executed_output_document(
                session_id        = session_id,
                execution_id      = execution_id,
                s3_bucket         = os.getenv("S3_FILES_BUCKET", _DEFAULT_S3_BUCKET),
                s3_key            = out_s3_key,
                filename          = out_filename,
                business_unit     = business_unit,
                platform          = platform,
                category          = category,
                input_document_id = "",
            )
            download_url = generate_presigned_download_url(out_s3_key)
            logger.info("[FetchConfigurationService] Uploaded executed output → %s", out_s3_key)
        except Exception as exc:
            logger.warning("[FetchConfigurationService] Could not upload executed output: %s", exc)

        return (
            {
                "success":           True,
                "validation_passed": True,
                "output_s3_key":     out_s3_key,
                "output_filename":   out_filename,
            },
            download_url,
            out_filename,
            execution_id,
        )
