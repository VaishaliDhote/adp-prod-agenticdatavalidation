# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2 - Pydantic model PATTERN reference for JSON data models (task 21).
# REUSABLE AS A TEMPLATE for building the exact JSON contract models.
# ============================================================================
"""
Response DTOs - Pydantic models for API responses.
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class BaseResponse(BaseModel):
    """Base response with status and session_id."""
    status: str = Field(..., description="Response status: success, error, in_progress, etc.")
    session_id: str = Field(..., description="Session identifier")


class UserContextResponse(BaseResponse):
    """Response with user context."""
    user_id: str = Field("", description="Authenticated user ID")
    user_name: str = Field("", description="Authenticated user name")


class FetchBUResponse(UserContextResponse):
    """Response for fetching business units."""
    business_units: list[str] = Field(default_factory=list)


class FetchPlatformResponse(UserContextResponse):
    """Response for fetching platforms."""
    business_unit: str
    platforms: list[str] = Field(default_factory=list)


class FetchCategoryResponse(UserContextResponse):
    """Response for fetching categories."""
    categories: list[str] = Field(default_factory=list)


class CategoryMutationResponse(UserContextResponse):
    """Response for category insert/delete."""
    message: str


class FileUploadResponse(BaseResponse):
    """Response for file upload."""
    presigned_url: str = Field(..., description="S3 presigned URL for upload")
    s3_key: str = Field(..., description="S3 object key")
    document_id: str = Field(..., description="Document identifier")
    content_type: str = Field(..., description="Content type for upload")
    message: str = Field("", description="Instructions for upload")


class ConfirmUploadResponse(BaseResponse):
    """Response for confirming upload."""
    document_id: str
    upload_status: str


class GetConfigurationResponse(BaseResponse):
    """Response for get_configuration."""
    business_unit: str
    platform: str
    category: str
    summary: str = Field("", description="Configuration summary")
    timings: Optional[dict[str, Any]] = Field(None, description="Pipeline timing info")
    draft_s3_key: Optional[str] = Field(None, description="S3 key for draft config")


class EditConfigurationResponse(BaseResponse):
    """Response for edit_configuration_chat (exact JSON contract editing)."""
    reply: str = Field("", description="LLM reply to show user")
    intent: str = Field("edit", description="Classified user intent")
    has_changes: bool = Field(False, description="Whether draft was updated")
    changes: list[dict[str, Any]] = Field(default_factory=list, description="Structured field changes")
    changes_display: str = Field("", description="Human-readable change list")
    affected_section: Optional[str] = Field(None, description="Contract section edited")
    affected_uuid: Optional[str] = Field(None, description="Column UUID anchor edited")
    affected_columns: list[str] = Field(default_factory=list, description="Column names edited")
    validation_warnings: list[str] = Field(default_factory=list, description="Non-blocking contract warnings")
    summary: Optional[str] = Field(None, description="Config summary (review / first message)")
    reapply: Optional[dict[str, Any]] = Field(None, description="Execution-API reapply result (on confirm)")
    hint: str = Field("", description="Hint for next action")
    message: Optional[str] = Field(None, description="Success message (confirmed only)")


class HealthCheckResponse(BaseModel):
    """Response for health check."""
    status: str = "healthy"
    service: str = "Single Sheet DVTS POC"
    input_dir: str
    output_dir: str
    input_dir_exists: bool
    output_dir_exists: bool
