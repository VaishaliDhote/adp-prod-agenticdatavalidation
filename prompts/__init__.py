"""
Prompts package for LLM-based transformation detection and analysis.
"""

from prompts.transformation_detection_prompt import (
    TRANSFORMATION_DETECTION_SYSTEM_PROMPT,
    TRANSFORMATION_DETECTION_USER_PROMPT_TEMPLATE,
    TRANSFORMATION_DETECTION_SIMPLE_SYSTEM_PROMPT,
    TRANSFORMATION_DETECTION_SIMPLE_USER_PROMPT_TEMPLATE,
    build_transformation_detection_prompt,
    build_simple_transformation_prompt,
    detect_transformations_with_llm,
)

from prompts.prompt_generate_final_config import (
    GENERATE_FINAL_CONFIG_PROMPT,
    GENERATE_FINAL_CONFIG_USER_PROMPT,
)
from prompts.config_summary_combined import (
    GENERATE_FINAL_CONFIG_SUMMARY_PROMPT,
    GENERATE_FINAL_CONFIG_USER_PROMPT
)
from prompts.config_summary_regenerate_prompt import (
    CONFIG_SUMMARY_REGENERATE_PROMPT,
)
__all__ = [
    "TRANSFORMATION_DETECTION_SYSTEM_PROMPT",
    "TRANSFORMATION_DETECTION_USER_PROMPT_TEMPLATE",
    "TRANSFORMATION_DETECTION_SIMPLE_SYSTEM_PROMPT",
    "TRANSFORMATION_DETECTION_SIMPLE_USER_PROMPT_TEMPLATE",
    "build_transformation_detection_prompt",
    "build_simple_transformation_prompt",
    "detect_transformations_with_llm",
    "GENERATE_FINAL_CONFIG_PROMPT",
    "GENERATE_FINAL_CONFIG_USER_PROMPT",
    "GENERATE_FINAL_CONFIG_SUMMARY_PROMPT",
    "CONFIG_SUMMARY_REGENERATE_PROMPT",
]
