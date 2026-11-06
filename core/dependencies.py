# ============================================================================
# REUSABLE AS-IS - no rework required. FastAPI dependency-injection providers.
# ============================================================================
"""
core.dependencies
=================
FastAPI dependency-injection providers.

Routes call these via ``Depends(...)`` so service construction stays in one
place and can be swapped out (e.g. for tests) without touching route code.
"""
from __future__ import annotations

from core.paths import AppPaths
from ingestion.ingestion_service import IngestionService
from services.accuracy_loop_service import AccuracyLoopService
from services.business_entity_service import BusinessEntityService
from services.config_finalization_service import ConfigFinalizationService
from services.new_input_execution_service import NewInputExecutionService
from services.complete_config_service import CompleteConfigService
from services.configuration_generation_service import ConfigurationGenerationService
from services.fetch_configuration_service import FetchConfigurationService
from services.file_upload_service import FileUploadService
from services.fps_execution_service import FpsExecutionService
from services.pipeline_service import PipelineService
from services.transformation_execution_service import TransformationExecutionService
from services.transformation_analysis_service import TransformationAnalysisService
from services.transformation_ir_service import TransformationIRService
from services.validation_analysis_service import ValidationAnalysisService
from services.validation_ir_service import ValidationIRService


def get_business_service() -> BusinessEntityService:
    return BusinessEntityService()


def get_file_service() -> FileUploadService:
    return FileUploadService()


def get_config_generation_service() -> ConfigurationGenerationService:
    return ConfigurationGenerationService()


def get_fetch_config_service() -> FetchConfigurationService:
    return FetchConfigurationService()


def get_execution_service() -> TransformationExecutionService:
    return TransformationExecutionService(
        input_base_dir=AppPaths.INPUT_BASE_DIR,
        output_base_dir=AppPaths.OUTPUT_BASE_DIR,
        config_base_dir=AppPaths.CONFIG_BASE_DIR,
        executed_output_dir=AppPaths.EXECUTED_OUTPUT_DIR,
    )


def get_fps_execution_service() -> FpsExecutionService:
    """Provide the real ADP File Processing Service execution orchestrator."""
    return FpsExecutionService()


def get_ingestion_service() -> IngestionService:
    """Provide the Deliverable 1 canonical-model ingestion service."""
    return IngestionService(
        input_base_dir=AppPaths.INPUT_BASE_DIR,
        output_base_dir=AppPaths.OUTPUT_BASE_DIR,
    )


def get_validation_analysis_service() -> ValidationAnalysisService:
    """Provide the Deliverable 3 validation rule identification service."""
    return ValidationAnalysisService(
        ingestion_service=get_ingestion_service(),
    )


def get_validation_ir_service() -> ValidationIRService:
    """Provide the Deliverable 4 validation IR + JSON generation service."""
    return ValidationIRService(
        analysis_service=get_validation_analysis_service(),
    )


def get_transformation_analysis_service() -> TransformationAnalysisService:
    """Provide the Deliverable 5 transformation rule identification service."""
    return TransformationAnalysisService(
        ingestion_service=get_ingestion_service(),
    )


def get_transformation_ir_service() -> TransformationIRService:
    """Provide the Deliverable 6 transformation IR + JSON generation service."""
    return TransformationIRService(
        analysis_service=get_transformation_analysis_service(),
    )


def get_complete_config_service() -> CompleteConfigService:
    """Provide the Deliverable 7 complete-configuration generation service."""
    return CompleteConfigService(
        validation_ir_service=get_validation_ir_service(),
        transformation_ir_service=get_transformation_ir_service(),
        ingestion_service=get_ingestion_service(),
    )


def get_accuracy_loop_service() -> AccuracyLoopService:
    """Provide the Deliverable 8 execution + development accuracy-loop service."""
    return AccuracyLoopService(
        complete_config_service=get_complete_config_service(),
        ingestion_service=get_ingestion_service(),
    )


def get_finalization_service() -> ConfigFinalizationService:
    """Provide the Deliverable 10 configuration finalization (approve) service."""
    return ConfigFinalizationService()


def get_pipeline_service() -> PipelineService:
    """Provide the end-to-end pipeline (generate -> process-API verify -> save)."""
    return PipelineService(
        complete_config_service=get_complete_config_service(),
        fps_execution_service=get_fps_execution_service(),
        finalization_service=get_finalization_service(),
    )


def get_new_input_execution_service() -> NewInputExecutionService:
    """Provide the Deliverable 10 new-input (future) execution service."""
    return NewInputExecutionService()
