# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - File Loader Agent. REUSABLE.
# Rework: support multiple input/output files per session and structured logging.
# ============================================================================
import pandas as pd
import traceback
from typing import Dict, Any
from pathlib import Path

from services.file_loader_service import build_file_state_for_session
from states.filestate import FileState


class FileLoaderAgent:
    """Agent responsible for loading files and constructing FileState.

    This wraps the lower-level tool functions and provides a simple
    interface for callers that only need to pass a session_id.
    """

    def __init__(self) -> None:
        # Placeholder for future dependencies (config, logger, etc.)
        pass

    async def load_session_files(self, session_id: str) -> FileState:
        """Create and return the FileState for the given session_id.

        It scans:
          data/input_files/<session_id>/*.xlsx
          data/output_files/<session_id>/*.xlsx

        and returns a JSON-serializable FileState describing all
        discovered files, sheets and columns.
        """
        try:
            print("***"*40)
            print('before build_file_state_for_session ',session_id)
            print("***"*40)
            return await build_file_state_for_session(session_id)
        except Exception:
            # You may replace this with structured logging as needed
            traceback.print_exc()
            # On error, return an empty state so the caller can decide what to do
            return {"input_files": {}, "output_files": {}}

