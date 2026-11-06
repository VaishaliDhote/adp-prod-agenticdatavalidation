# ============================================================================
# REUSABLE AS-IS - no rework required. Shared path/filename security helpers.
# ============================================================================
"""
utils/path_utils.py
====================
Shared path / filename security helpers reused across the API layer
and service layer.  Extracted from main.py so that both route handlers
and service classes can import them without circular dependencies.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path


def sanitize_session_id(session_id: str) -> str:
    """Validate that session_id is a safe alphanumeric/UUID string.

    Raises ValueError if the session_id contains path traversal sequences
    or any characters outside the allowed set.
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    # Allow only alphanumerics, dashes, and underscores (covers UUID format)
    if not re.match(r'^[a-zA-Z0-9_\-]+$', session_id):
        raise ValueError(f"session_id contains invalid characters: '{session_id}'")
    # Extra guard: reject any dot sequences that could indicate traversal
    if "." in session_id:
        raise ValueError(f"session_id must not contain dots: '{session_id}'")
    return session_id


def sanitize_path_component(value: str, field_name: str = "value") -> str:
    """Validate that a string is safe to use as a path component.

    Prevents path traversal attacks by rejecting:
    - Empty values
    - Path traversal sequences (../, .., etc.)
    - Directory separators (/, \\)
    - Any characters outside alphanumerics, dashes, underscores, and spaces

    Args:
        value: The string to validate
        field_name: Name of the field for error messages

    Raises:
        ValueError: If the value contains unsafe characters or patterns
    """
    if not value:
        raise ValueError(f"{field_name} must not be empty")

    # Reject path traversal sequences
    if ".." in value:
        raise ValueError(f"{field_name} must not contain path traversal sequences: '{value}'")

    # Reject directory separators
    if "/" in value or "\\" in value:
        raise ValueError(f"{field_name} must not contain directory separators: '{value}'")

    # Allow only alphanumerics, dashes, underscores, and spaces
    if not re.match(r'^[a-zA-Z0-9_\- ]+$', value):
        raise ValueError(f"{field_name} contains invalid characters: '{value}'")

    # Reject values that are just dots
    if value.strip() in (".", ".."):
        raise ValueError(f"{field_name} must not be a dot sequence: '{value}'")

    return value


def create_data_folder(base_dir: Path, data_id: str = None) -> Path:
    """Create a new folder with unique data_id for organizing uploads."""
    if data_id is None:
        data_id = str(uuid.uuid4())

    folder_path = base_dir / data_id
    # Boundary check — resolved path must stay inside base_dir
    folder_path.resolve().relative_to(base_dir.resolve())
    folder_path.mkdir(parents=True, exist_ok=True)
    return folder_path


def sanitize_filename(filename: str) -> str:
    """Strip directory components and reject traversal sequences.

    Raises ValueError if the result is empty, suspicious, or has an
    unsupported extension.
    """
    # Remove any directory component – defeats ../../../ attacks
    safe = Path(filename).name
    if not safe or safe in (".", ".."):
        raise ValueError(f"Invalid filename: '{filename}'")
    # Reject explicit traversal sequences (parentheses/brackets are allowed below).
    if ".." in safe:
        raise ValueError(f"Filename must not contain path traversal sequences: '{filename}'")
    # Allow word chars, dash, dot, space, and parentheses/brackets so real-world
    # exports like "lohnkontendaten(1)(1).csv" are accepted.
    if not re.match(r'^[\w\-.()\[\] ]+$', safe):
        raise ValueError(f"Filename contains invalid characters: '{filename}'")
    # Supported ingestion formats: Excel, CSV, JSON.
    if not safe.lower().endswith((".xlsx", ".xls", ".csv", ".json")):
        raise ValueError(f"Unsupported file extension: '{filename}'")
    return safe
