# ============================================================================
# REUSABLE AS-IS - no rework required. FastAPI composition root / entry point.
# ============================================================================
"""
FastAPI Application — Composition Root
======================================
Thin entry point.  This module only:
  - Configures logging.
  - Builds the FastAPI app.
  - Registers middleware, global exception handlers, and routers.
  - Boots uvicorn under ``__main__``.

Everything else (routes, error contract, DI, paths, services) lives in
dedicated modules so this file stays small and the structure is obvious:

    core/      → app infrastructure (paths, errors, DI, validation)
    routes/    → HTTP route modules (one per domain)
    services/  → business logic
"""
from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

# Load environment variables from .env BEFORE importing any module that reads
# them at import time (e.g. middleware.auth_middleware reads GRAPH_CA_BUNDLE /
# GRAPH_SSL_VERIFY when it is first imported).
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from core.errors import install_error_handlers
from core.paths import AppPaths  # noqa: F401  (import ensures directories exist)
from middleware.auth_middleware import GraphAuthMiddleware
from routes import register_routes


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    app = FastAPI(title="Single Sheet DVTS POC", version="2.0")

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GraphAuthMiddleware)

    # Unified error contract: {detail: {code, message, ...}} for every error.
    install_error_handlers(app)

    # All HTTP routes live in the routes/ package.
    register_routes(app)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
    # import uvicorn
    # BASE_DIR = Path(__file__).parent  # always the script's own folder

    # uvicorn.run(
    #     "app:app",
    #     host="0.0.0.0",
    #     port=8001,
    #     # reload=True,
    #     ssl_keyfile=str(BASE_DIR / "key.key"),
    #     ssl_certfile=str(BASE_DIR / "ss.cer"),
    # )

