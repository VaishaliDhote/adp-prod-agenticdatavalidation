# ============================================================================
# REUSABLE AS-IS - no rework required. Centralized filesystem layout.
# ============================================================================
"""
core.paths
==========
Centralized filesystem layout for the application.

All routes/services consume these constants instead of recomputing paths.
Directories are created at import time, exactly once.
"""
from __future__ import annotations

from pathlib import Path


class AppPaths:
    """Application directory layout (singleton-by-module)."""

    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    INPUT_BASE_DIR: Path = DATA_DIR / "input_files"
    OUTPUT_BASE_DIR: Path = DATA_DIR / "output_files"
    CONFIG_BASE_DIR: Path = DATA_DIR / "generated_configs"
    EXECUTED_OUTPUT_DIR: Path = DATA_DIR / "executed_outputs"
    DEBUG_CONFIG_DIR: Path = DATA_DIR / "debug_configs"

    @classmethod
    def all_dirs(cls) -> list[Path]:
        return [
            cls.INPUT_BASE_DIR,
            cls.OUTPUT_BASE_DIR,
            cls.CONFIG_BASE_DIR,
            cls.EXECUTED_OUTPUT_DIR,
            cls.DEBUG_CONFIG_DIR,
        ]

    @classmethod
    def ensure_all(cls) -> None:
        """Create every managed directory if it doesn't already exist."""
        for d in cls.all_dirs():
            d.mkdir(parents=True, exist_ok=True)


# Eagerly create directories on import so every consumer sees them.
AppPaths.ensure_all()
