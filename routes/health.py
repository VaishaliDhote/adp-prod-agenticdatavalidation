# ============================================================================
# REUSABLE AS-IS - no rework required. Infrastructure (health check route).
# ============================================================================
"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter

from core.paths import AppPaths

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Lightweight health check used by uptime probes."""
    return {
        "status": "healthy",
        "service": "Single Sheet DVTS POC",
        "input_dir": str(AppPaths.INPUT_BASE_DIR),
        "output_dir": str(AppPaths.OUTPUT_BASE_DIR),
        "input_dir_exists": AppPaths.INPUT_BASE_DIR.exists(),
        "output_dir_exists": AppPaths.OUTPUT_BASE_DIR.exists(),
    }
