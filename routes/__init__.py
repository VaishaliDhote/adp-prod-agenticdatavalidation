"""HTTP route modules. Each submodule exposes a ``router: APIRouter``."""
from fastapi import FastAPI

from routes import (
    accuracy_loop,
    business_entity,
    chat,
    complete_config,
    configuration,
    contract,
    execution,
    file_upload,
    finalization,
    fps_execution,
    health,
    ingestion,
    llm_validation,
    pipeline,
    transformation_analysis,
    transformation_ir,
    validation_analysis,
    validation_ir,
)


def register_routes(app: FastAPI) -> None:
    """Attach every route module's router to the given FastAPI app."""
    app.include_router(business_entity.router)
    app.include_router(file_upload.router)
    app.include_router(configuration.router)
    app.include_router(execution.router)
    app.include_router(fps_execution.router)
    app.include_router(chat.router)
    app.include_router(health.router)
    app.include_router(ingestion.router)
    app.include_router(contract.router)
    app.include_router(validation_analysis.router)
    app.include_router(validation_ir.router)
    app.include_router(transformation_analysis.router)
    app.include_router(transformation_ir.router)
    app.include_router(complete_config.router)
    app.include_router(accuracy_loop.router)
    app.include_router(finalization.router)
    app.include_router(pipeline.router)
    app.include_router(llm_validation.router)
