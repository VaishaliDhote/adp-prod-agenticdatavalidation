# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 7 - Complete config generation orchestration (tasks 1, 16, 22). REUSABLE.
# Rework: retarget assembled output from YAML to the exact JSON contract; replace the
# LLM free-rewrite with a DETERMINISTIC JSON compiler; drop any pandas execution path.
# ============================================================================
"""
Configuration Generation Service

Handles the generation of transformation configurations from reference files.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from graph_builder import run_full_pipeline
from storage.s3.config_repository import save_draft_config, save_config_summary
from utils.serializer import make_serializable

logger = logging.getLogger(__name__)


@dataclass
class ConfigurationResult:
    """Result of configuration generation."""
    success: bool
    session_id: str
    business_unit: str
    platform: str
    category: str
    summary: str
    timings: dict[str, Any] | None
    draft_s3_key: str | None
    error: str | None = None


class ConfigurationGenerationService:
    """Service for generating transformation configurations."""
    
    def __init__(
        self,
        config_bucket: str | None = None,
    ) -> None:
        self.config_bucket = config_bucket or os.getenv(
            "S3_CONFIGS_BUCKET",
            "agentic-dvts-configs"
        )
    
    async def generate_configuration(
        self,
        session_id: str,
        business_unit: str,
        platform: str,
        category: str,
    ) -> ConfigurationResult:
        """
        Generate transformation configuration by running the full analysis pipeline.
        
        This runs the pipeline that:
        1. Loads input and output reference files
        2. Analyzes transformations between them
        3. Generates a configuration YAML
        4. Saves draft to S3
        
        Args:
            session_id: Session identifier
            business_unit: Business unit
            platform: Platform
            category: Category
            
        Returns:
            ConfigurationResult with generation details
        """
        try:
            # Run the analysis pipeline
            analysis_state = await run_full_pipeline(session_id=session_id)
            logger.info(f"Pipeline completed. State keys: {list(analysis_state.keys())}")
            
            # Extract corrected_config from phase_transformation_result
            pt = analysis_state.get("phase_transformation_result", {})
            corrected_config = pt.get("corrected_config")
            
            if not corrected_config:
                logger.warning("No corrected_config found in phase_transformation_result")
                corrected_config = {}
            
            # Save config as YAML draft
            draft_s3_key = None
            yaml_content = yaml.safe_dump(
                make_serializable(corrected_config),
                sort_keys=False,
                allow_unicode=True,
            )
            
            if yaml_content:
                try:
                    draft_s3_key = save_draft_config(
                        bu=business_unit,
                        platform=platform,
                        category=category,
                        session_id=session_id,
                        yaml_content=yaml_content,
                        version="default",
                    )
                    logger.info(f"Saved draft config to S3: {draft_s3_key}")
                except Exception as e:
                    logger.warning(f"Failed to save draft config: {e}")
            
            # Save summary for later retrieval
            summary = pt.get("summary", "")
            if summary:
                try:
                    save_config_summary(
                        bu=business_unit,
                        platform=platform,
                        category=category,
                        summary=summary,
                    )
                    logger.info("Saved config summary to S3")
                except Exception as e:
                    logger.warning(f"Failed to save config summary: {e}")
            
            return ConfigurationResult(
                success=True,
                session_id=session_id,
                business_unit=business_unit,
                platform=platform,
                category=category,
                summary=summary,
                timings=pt.get("timings"),
                draft_s3_key=draft_s3_key,
            )
            
        except Exception as e:
            logger.error(f"Configuration generation failed: {e}")
            return ConfigurationResult(
                success=False,
                session_id=session_id,
                business_unit=business_unit,
                platform=platform,
                category=category,
                summary="",
                timings=None,
                draft_s3_key=None,
                error=str(e),
            )
