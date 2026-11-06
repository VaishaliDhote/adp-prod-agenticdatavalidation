# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - Upload new input files, presigned URLs, doc mgmt (task 16). REUSABLE.
# ============================================================================
"""
File Upload Service

Handles file upload operations including presigned URL generation,
upload confirmation, and document management.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from storage.dynamodb.document_repository import (
    insert_document,
    update_upload_status,
    fetch_documents_by_session,
)
from storage.s3.file_repository import (
    generate_presigned_upload_url,
    get_content_type,
    download_file_bytes,
    VALID_FILE_ROLES,
)

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of initiating a file upload."""
    presigned_url: str
    s3_key: str
    document_id: str
    content_type: str


class FileUploadService:
    """Service for managing file uploads."""
    
    FILE_TYPE_MAP = {
        "input_reference_files": "INPUT_REF",
        "output_reference_files": "OUTPUT_REF",
        "input_files": "INPUT_FILE",
    }
    
    def __init__(self, s3_bucket: str | None = None) -> None:
        self.s3_bucket = s3_bucket or os.getenv(
            "S3_FILES_BUCKET", 
            "agentic-data-validate-tranform-files"
        )
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Strip directory components and reject traversal sequences.
        
        Raises:
            ValueError: If filename is invalid or unsupported
        """
        safe = Path(filename).name
        if not safe or safe in (".", ".."):
            raise ValueError(f"Invalid filename: '{filename}'")
        # Allow word chars, spaces, dashes, dots, and parentheses/brackets so
        # real-world exports like "lohnkontendaten(1)(1).csv" are accepted.
        if not re.match(r'^[\w\-.()\[\] ]+$', safe):
            raise ValueError(f"Filename contains invalid characters: '{filename}'")
        if not safe.lower().endswith((".xlsx", ".xls", ".csv", ".json")):
            raise ValueError(f"Unsupported file extension: '{filename}'")
        return safe
    
    def initiate_upload(
        self,
        session_id: str,
        business_unit: str,
        platform: str,
        category: str,
        file_role: str,
        filename: str,
    ) -> UploadResult:
        """
        Initiate a file upload by generating a presigned URL and creating a document record.
        
        Args:
            session_id: Session identifier
            business_unit: Business unit
            platform: Platform
            category: Category
            file_role: Role of file (input_reference_files, output_reference_files, input_files)
            filename: Original filename
            
        Returns:
            UploadResult with presigned URL and document details
            
        Raises:
            ValueError: If file_role is invalid
        """
        # Validate file role
        valid_roles = VALID_FILE_ROLES - {"executed_output_files"}
        if file_role not in valid_roles:
            raise ValueError(f"Invalid file_role. Must be one of: {valid_roles}")
        
        # Sanitize filename
        safe_filename = self.sanitize_filename(filename)
        
        # Generate document_id (only INPUT_FILE keys are scoped by document_id)
        document_id = uuid.uuid4().hex
        
        # Generate presigned URL
        presigned_url, s3_key = generate_presigned_upload_url(
            session_id,
            file_role,
            safe_filename,
            bu=business_unit,
            platform=platform,
            category=category,
            document_id=document_id if file_role == "input_files" else "",
        )
        
        # Create document record in DynamoDB
        file_type = self.FILE_TYPE_MAP[file_role]
        insert_document(
            session_id=session_id,
            file_type=file_type,
            s3_bucket=self.s3_bucket,
            s3_key=s3_key,
            filename=safe_filename,
            business_unit=business_unit,
            platform=platform,
            category=category,
            upload_status="pending",
            document_id=document_id,
        )
        
        logger.info(f"Initiated upload: {safe_filename} -> {s3_key}")
        
        return UploadResult(
            presigned_url=presigned_url,
            s3_key=s3_key,
            document_id=document_id,
            content_type=get_content_type(safe_filename),
        )
    
    def confirm_upload(
        self,
        session_id: str,
        document_id: str,
        file_type: str,
    ) -> None:
        """
        Confirm that a file upload has completed.
        
        Args:
            session_id: Session identifier
            document_id: Document identifier
            file_type: File type (INPUT_REF, OUTPUT_REF, INPUT_FILE)
            
        Raises:
            ValueError: If file_type is invalid
        """
        valid_types = {"INPUT_REF", "OUTPUT_REF", "INPUT_FILE"}
        if file_type not in valid_types:
            raise ValueError(f"file_type must be one of: {valid_types}")
        
        update_upload_status(session_id, file_type, document_id, "completed")
        logger.info(f"Confirmed upload: {document_id}")
    
    def download_reference_files(
        self,
        session_id: str,
        input_folder: Path,
        output_folder: Path,
    ) -> tuple[int, int]:
        """
        Download reference files from S3 to local folders.
        
        Args:
            session_id: Session identifier
            input_folder: Local folder for input reference files
            output_folder: Local folder for output reference files
            
        Returns:
            Tuple of (input_files_downloaded, output_files_downloaded)
        """
        input_ref_docs = fetch_documents_by_session(session_id, "INPUT_REF")
        output_ref_docs = fetch_documents_by_session(session_id, "OUTPUT_REF")
        
        logger.info(f"Found {len(input_ref_docs)} input_ref and {len(output_ref_docs)} output_ref docs")
        
        input_downloaded = self._download_files(input_ref_docs, input_folder)
        output_downloaded = self._download_files(output_ref_docs, output_folder)
        
        return input_downloaded, output_downloaded
    
    def _download_files(
        self,
        documents: list[dict],
        target_folder: Path,
    ) -> int:
        """Download files to a target folder."""
        target_folder.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        
        for doc in documents:
            if doc.get("upload_status") != "completed":
                continue
            
            try:
                s3_key = doc.get("s3_key")
                s3_bucket = doc.get("s3_bucket", self.s3_bucket)
                filename = self.sanitize_filename(doc.get("filename", ""))
                
                file_bytes = download_file_bytes(s3_key, bucket=s3_bucket)
                file_path = target_folder / filename
                
                # Boundary check
                file_path.resolve().relative_to(target_folder.resolve())
                
                with open(file_path, "wb") as f:
                    f.write(file_bytes)
                
                logger.info(f"Downloaded: {filename} -> {file_path}")
                downloaded += 1
                
            except Exception as e:
                logger.warning(f"Failed to download {doc.get('filename')}: {e}")
        
        return downloaded
    
    def get_latest_input_file(
        self,
        session_id: str,
        document_id: str | None = None,
    ) -> dict | None:
        """
        Get the latest input file document for a session.
        
        Args:
            session_id: Session identifier
            document_id: Optional specific document_id to fetch
            
        Returns:
            Document dict or None if not found
        """
        input_docs = fetch_documents_by_session(session_id, "INPUT_FILE")
        completed_docs = [d for d in input_docs if d.get("upload_status") == "completed"]
        
        if not completed_docs:
            return None
        
        if document_id:
            matched = [d for d in completed_docs if d.get("document_id") == document_id]
            return matched[0] if matched else None
        
        # Return latest by timestamp
        return max(
            completed_docs,
            key=lambda d: d.get("updated_at") or d.get("created_at") or "",
        )
