# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2 - Pydantic model PATTERN reference for JSON data models (task 21).
# REUSABLE AS A TEMPLATE for building the exact JSON contract models.
# ============================================================================
"""
Request DTOs - Pydantic models for API request validation.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


class BaseRequestParams(BaseModel):
    """Base request params with session_id."""
    session_id: str = Field(..., min_length=1, description="Session identifier")
    
    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id is alphanumeric/UUID format, no path traversal."""
        if not v:
            raise ValueError("session_id must not be empty")
        if not re.match(r'^[a-zA-Z0-9_\-]+$', v):
            raise ValueError(f"session_id contains invalid characters: '{v}'")
        if "." in v:
            raise ValueError(f"session_id must not contain dots: '{v}'")
        return v


class BusinessContextParams(BaseRequestParams):
    """Request params with business context (bu/platform/category)."""
    business_unit: str = Field(..., min_length=1, description="Business unit identifier")
    platform: str = Field(..., min_length=1, description="Platform identifier")
    category: str = Field(..., min_length=1, description="Category identifier")


class FetchPlatformRequest(BaseRequestParams):
    """Request for fetching platforms."""
    business_unit: str = Field(..., min_length=1)


class FetchCategoryRequest(BaseRequestParams):
    """Request for fetching categories."""
    business_unit: str = Field(..., min_length=1)
    platform: str = Field(..., min_length=1)


class CategoryMutationRequest(BusinessContextParams):
    """Request for inserting/deleting categories."""
    pass


class FileUploadRequest(BusinessContextParams):
    """Request for file upload."""
    file_role: str = Field(..., description="Role of file: input_reference_files, output_reference_files, input_files")
    filename: str = Field(..., min_length=1, description="Original filename")
    
    @field_validator("file_role")
    @classmethod
    def validate_file_role(cls, v: str) -> str:
        valid_roles = {"input_reference_files", "output_reference_files", "input_files"}
        if v not in valid_roles:
            raise ValueError(f"file_role must be one of: {valid_roles}")
        return v
    
    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        if not v.endswith((".xlsx", ".xls")):
            raise ValueError("Only .xlsx/.xls files are supported")
        return v


class ConfirmUploadRequest(BaseRequestParams):
    """Request for confirming upload."""
    document_id: str = Field(..., min_length=1)
    file_type: str = Field(..., description="File type: INPUT_REF, OUTPUT_REF, INPUT_FILE")
    
    @field_validator("file_type")
    @classmethod
    def validate_file_type(cls, v: str) -> str:
        valid_types = {"INPUT_REF", "OUTPUT_REF", "INPUT_FILE"}
        if v not in valid_types:
            raise ValueError(f"file_type must be one of: {valid_types}")
        return v


class GetConfigurationRequest(BusinessContextParams):
    """Request for getting configuration."""
    pass


class ExecuteTransformationRequest(BusinessContextParams):
    """Request for executing transformation."""
    document_id: Optional[str] = Field(None, description="Optional specific INPUT_FILE document_id")


class EditConfigurationRequest(BusinessContextParams):
    """Request for editing configuration via chat."""
    user_query: str = Field(..., min_length=1, description="User's edit request")
    current_yaml: Optional[str] = Field(None, description="Current YAML config (optional)")
    config_version: str = Field("", description="Config version for tracking")
