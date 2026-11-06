# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 8 - Execute + compare + store skeleton (tasks 5-13). REUSABLE but REPURPOSE.
# Rework: replace run_executor_only_pipeline (quarantined Python engine) with the
# EXTERNAL API call; keep the compare (mismatched_cols) and output-store logic;
# add accuracy measurement + max-5-iteration/95% loop + iteration history (tasks 16,22-25).
# ============================================================================
"""
Transformation Execution Service

Handles the execution of transformations on input files using confirmed configurations.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from storage.dynamodb.document_repository import (
    fetch_latest_confirmed_config,
    insert_executed_output_document,
)
from storage.s3.file_repository import download_file_bytes, upload_file_bytes
from storage.s3.config_repository import CONFIGS_BUCKET
from services.file_upload_service import FileUploadService

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of transformation execution."""
    success: bool
    session_id: str
    execution_id: str
    output_path: str | None
    validation_passed: bool
    validation_message: str
    mismatched_cols: list[dict]
    error: str | None = None


class TransformationExecutionService:
    """Service for executing transformations."""
    
    def __init__(
        self,
        input_base_dir: Path,
        output_base_dir: Path,
        config_base_dir: Path,
        executed_output_dir: Path,
        files_bucket: str | None = None,
        configs_bucket: str | None = None,
    ) -> None:
        self.input_base_dir = input_base_dir
        self.output_base_dir = output_base_dir
        self.config_base_dir = config_base_dir
        self.executed_output_dir = executed_output_dir
        self.files_bucket = files_bucket or os.getenv(
            "S3_FILES_BUCKET",
            "agentic-data-validate-tranform-files"
        )
        self.configs_bucket = configs_bucket or CONFIGS_BUCKET
        self.file_service = FileUploadService(self.files_bucket)
    
    async def execute(
        self,
        session_id: str,
        business_unit: str,
        platform: str,
        category: str,
        document_id: str | None = None,
    ) -> ExecutionResult:
        """
        Execute transformation on an input file using the confirmed configuration.
        
        Args:
            session_id: Session identifier
            business_unit: Business unit
            platform: Platform
            category: Category
            document_id: Optional specific INPUT_FILE document_id
            
        Returns:
            ExecutionResult with execution details
        """
        execution_id = f"{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        try:
            # 1. Fetch latest confirmed config
            latest_confirmed = fetch_latest_confirmed_config(business_unit, platform, category)
            if not latest_confirmed:
                return ExecutionResult(
                    success=False,
                    session_id=session_id,
                    execution_id=execution_id,
                    output_path=None,
                    validation_passed=True,
                    validation_message="",
                    mismatched_cols=[],
                    error=f"No confirmed configuration found for {business_unit}/{platform}/{category}",
                )
            
            # 2. Get input file
            input_doc = self.file_service.get_latest_input_file(session_id, document_id)
            if not input_doc:
                return ExecutionResult(
                    success=False,
                    session_id=session_id,
                    execution_id=execution_id,
                    output_path=None,
                    validation_passed=True,
                    validation_message="",
                    mismatched_cols=[],
                    error=f"No completed INPUT_FILE found for session={session_id}",
                )
            
            input_document_id = input_doc.get("document_id", "")
            filename = input_doc.get("filename", "")
            
            if not filename.endswith((".xlsx", ".xls")):
                return ExecutionResult(
                    success=False,
                    session_id=session_id,
                    execution_id=execution_id,
                    output_path=None,
                    validation_passed=True,
                    validation_message="",
                    mismatched_cols=[],
                    error=f"INPUT_FILE must be Excel (.xlsx/.xls), got '{filename}'",
                )
            
            # 3. Download files locally
            execution_input_folder = self.input_base_dir / session_id / "executions" / execution_id
            execution_input_folder.mkdir(parents=True, exist_ok=True)
            self.config_base_dir.mkdir(parents=True, exist_ok=True)
            
            # Download input file
            input_bytes = download_file_bytes(
                input_doc["s3_key"],
                bucket=input_doc.get("s3_bucket", self.files_bucket),
            )
            safe_filename = self.file_service.sanitize_filename(filename)
            file_path = execution_input_folder / safe_filename
            file_path.resolve().relative_to(execution_input_folder.resolve())
            with open(file_path, "wb") as f:
                f.write(input_bytes)
            
            # Download confirmed config
            confirmed_yaml_bytes = download_file_bytes(
                latest_confirmed["s3_key"],
                bucket=latest_confirmed.get("s3_bucket", self.configs_bucket),
            )
            local_config_path = self.config_base_dir / f"{session_id}_full_state.yaml"
            with open(local_config_path, "wb") as f:
                f.write(confirmed_yaml_bytes)
            
            logger.info(f"Downloaded INPUT_FILE: {file_path}")
            logger.info(f"Downloaded config: {local_config_path}")
            
            # 4. Execute transformation.
            # The legacy in-process Python execution engine has been removed.
            # Deliverable 8 will call the EXTERNAL validation/transformation API
            # here, then compare the produced output against the expected output
            # and store the result. Until that integration exists, execution is
            # reported as unavailable rather than silently succeeding.
            logger.warning(
                "Transformation execution unavailable: legacy Python executor removed; "
                "external-API execution (Deliverable 8) is not yet implemented."
            )
            return ExecutionResult(
                success=False,
                session_id=session_id,
                execution_id=execution_id,
                output_path=None,
                validation_passed=True,
                validation_message="",
                mismatched_cols=[],
                error=(
                    "Transformation execution is not yet available: the legacy Python "
                    "execution engine was removed and external-API execution "
                    "(Deliverable 8) is not implemented."
                ),
            )
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return ExecutionResult(
                success=False,
                session_id=session_id,
                execution_id=execution_id,
                output_path=None,
                validation_passed=True,
                validation_message="",
                mismatched_cols=[],
                error=str(e),
            )
    
    def _save_output(
        self,
        session_id: str,
        execution_id: str,
        output_path: str,
        filename: str,
        input_document_id: str,
        business_unit: str,
        platform: str,
        category: str,
    ) -> None:
        """Save output file to S3 and register in DynamoDB."""
        try:
            op = Path(output_path)
            output_filename = f"{Path(filename).stem}-{input_document_id}.xlsx"
            
            output_s3_key = upload_file_bytes(
                content=op.read_bytes(),
                session_id=session_id,
                file_role="executed_output_files",
                filename=output_filename,
                bu=business_unit,
                platform=platform,
                category=category,
                document_id=input_document_id,
            )
            
            insert_executed_output_document(
                session_id=session_id,
                execution_id=execution_id,
                s3_bucket=self.files_bucket,
                s3_key=output_s3_key,
                filename=output_filename,
                business_unit=business_unit,
                platform=platform,
                category=category,
                input_document_id=input_document_id,
            )
            
            logger.info(f"Saved output to S3: {output_s3_key}")
            
        except Exception as e:
            logger.warning(f"S3/DynamoDB record failed: {e}")
    
    @staticmethod
    def build_validation_error_excel(
        mismatched_cols: list[dict],
        execution_id: str,
        output_dir: Path,
    ) -> Path:
        """
        Build an Excel report of validation mismatches.
        
        Columns:
            A  Field with error
            B  Field Value
            C  Error Message
            
        Returns:
            Path to the saved .xlsx file
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Validation Errors"
        
        # Header row
        headers = ["Field with error", "Field Value", "Error Message"]
        header_fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="left", vertical="center")
        
        # Data rows
        for row_idx, mismatch in enumerate(mismatched_cols, start=2):
            column_name = mismatch.get("column", "")
            rule_key = mismatch.get("rule", "")
            expected = mismatch.get("expected", "")
            actual = mismatch.get("actual", "")
            
            if rule_key == "column_exists":
                field_value = "—"
                error_message = "Provided value is not a valid value"
            else:
                field_value = str(actual) if actual is not None else ""
                error_message = "Provided value is not a valid value"
            
            ws.cell(row=row_idx, column=1, value=column_name)
            ws.cell(row=row_idx, column=2, value=field_value)
            ws.cell(row=row_idx, column=3, value=error_message)
        
        # Column widths
        ws.column_dimensions["A"].width = 35
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 40
        
        # Save
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"validation_errors_{execution_id}.xlsx"
        wb.save(file_path)
        
        return file_path
