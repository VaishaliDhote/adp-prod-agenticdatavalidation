# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 2 - Contract validation PATTERN (task 23). REUSABLE AS A TEMPLATE ONLY.
# This validates the bespoke YAML phase structure - NOT the API JSON contract.
# Rework: build an equivalent validator for the exact JSON contract keys
# (column-relations, configuration-data, dataset-transformation-rules,
# transformation-rules, validation-rules).
# ============================================================================
"""
utils/yaml_validator.py
========================
Validate LLM-generated YAML config edits before merging.
"""

from __future__ import annotations

from typing import Any
import yaml


# ─────────────────────────────────────────────────────────────────────────────
# Schema Definitions (lightweight, no external dependency)
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_KEYS_BY_PHASE: dict[str, dict[str, list[str]]] = {
    "phase_2_rows": {
        "filter": ["applied", "details"],
        "remove_duplicates": ["applied", "details"],
        "sort": ["applied", "details"],
    },
    "phase_3_columns": {
        "select_columns": ["applied", "details"],
        "rename_columns": ["applied", "details"],
        "reorder_columns": ["applied", "details"],
    },
    "phase_4_enrichment": {
        "calculated_columns": ["applied", "details"],
        "conditional_columns": ["applied", "details"],
        "lookup": ["applied", "details"],
    },
    "phase_5_aggregation": {
        "groupby": ["applied", "details"],
        "running_total": ["applied", "details"],
        "ranking": ["applied", "details"],
    },
    "phase_6_cleaning": {
        "trim_clean": ["applied", "details"],
        "find_replace": ["applied", "details"],
        "type_conversion": ["applied", "details"],
    },
    "phase_7_advanced": {
        "array_formulas": ["applied", "details"],
    },
    "phase_9_final": {
        "resequence": ["applied", "details"],
        "final_save": ["applied", "details"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Validation Functions
# ─────────────────────────────────────────────────────────────────────────────

def validate_yaml_syntax(yaml_string: str) -> tuple[bool, str, dict | None]:
    """
    Validate YAML syntax.
    
    Returns:
        (is_valid, error_message, parsed_config)
    """
    try:
        config = yaml.safe_load(yaml_string)
        if config is None:
            return False, "YAML is empty or None", None
        if not isinstance(config, dict):
            return False, f"YAML root must be a dict, got {type(config).__name__}", None
        return True, "", config
    except yaml.YAMLError as e:
        return False, f"Invalid YAML syntax: {e}", None


def validate_phase_structure(config: dict, affected_phases: list[str]) -> tuple[bool, list[str]]:
    """
    Validate that edited phases have correct structure.
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    
    for phase in affected_phases:
        if phase not in config:
            continue  # Phase not in edit, skip
            
        phase_data = config[phase]
        if not isinstance(phase_data, dict):
            errors.append(f"{phase} must be a dict, got {type(phase_data).__name__}")
            continue
        
        # Check sub-operations have required keys
        required = REQUIRED_KEYS_BY_PHASE.get(phase, {})
        for operation, keys in required.items():
            if operation in phase_data:
                op_data = phase_data[operation]
                if not isinstance(op_data, dict):
                    errors.append(f"{phase}.{operation} must be a dict")
                    continue
                for key in keys:
                    if key not in op_data:
                        errors.append(f"{phase}.{operation} missing required key: {key}")
    
    return len(errors) == 0, errors


def validate_column_references(
    config: dict,
    valid_input_columns: list[str],
    valid_output_columns: list[str],
    affected_phases: list[str],
) -> tuple[bool, list[str]]:
    """
    Validate that column references in the config are valid.
    
    Args:
        config: Parsed YAML config
        valid_input_columns: Columns available before rename (for phase_2_rows)
        valid_output_columns: Columns available after rename (for enrichment phases)
        affected_phases: Which phases are being edited
    
    Returns:
        (is_valid, list_of_warnings)
    """
    warnings = []
    
    # Columns used in phase_2_rows should be input columns
    if "phase_2_rows" in config:
        p2 = config["phase_2_rows"]
        
        # Check filter rules
        filter_rules = p2.get("filter", {}).get("details", {}).get("rules", [])
        for i, rule in enumerate(filter_rules):
            col = rule.get("column", {})
            col_name = col.get("name") if isinstance(col, dict) else col
            if col_name and valid_input_columns and col_name not in valid_input_columns:
                warnings.append(
                    f"phase_2_rows.filter.rules[{i}].column '{col_name}' not in input columns: {valid_input_columns}"
                )
        
        # Check sort rules
        sort_rules = p2.get("sort", {}).get("details", {}).get("rules", [])
        for i, rule in enumerate(sort_rules):
            col = rule.get("column", {})
            col_name = col.get("name") if isinstance(col, dict) else col
            if col_name and valid_input_columns and col_name not in valid_input_columns:
                warnings.append(
                    f"phase_2_rows.sort.rules[{i}].column '{col_name}' not in input columns"
                )
    
    # Columns used in enrichment phases should be output columns
    for phase in ["phase_4_enrichment", "phase_5_aggregation"]:
        if phase not in config:
            continue
        # Just check that calculated_columns reference existing columns
        # This is a lightweight check - full validation would be more complex
    
    return len(warnings) == 0, warnings


def validate_yaml_edit(
    edited_yaml: str,
    affected_phases: list[str],
    valid_input_columns: list[str] | None = None,
    valid_output_columns: list[str] | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Full validation of LLM-generated YAML edit.
    
    Args:
        edited_yaml: The YAML string returned by LLM
        affected_phases: Which phases should be in the edit
        valid_input_columns: Optional list of valid input column names
        valid_output_columns: Optional list of valid output column names
    
    Returns:
        (is_valid, error_message, warnings)
        - is_valid: False if there's a blocking error
        - error_message: Description of blocking error (empty if valid)
        - warnings: Non-blocking issues (e.g., unknown column names)
    """
    # 1. Syntax validation
    is_valid, error, config = validate_yaml_syntax(edited_yaml)
    if not is_valid:
        return False, error, []
    
    # 2. Structure validation
    is_valid, errors = validate_phase_structure(config, affected_phases)
    if not is_valid:
        return False, "; ".join(errors), []
    
    # 3. Column reference validation (warnings only, not blocking)
    warnings = []
    if valid_input_columns or valid_output_columns:
        _, col_warnings = validate_column_references(
            config,
            valid_input_columns or [],
            valid_output_columns or [],
            affected_phases,
        )
        warnings.extend(col_warnings)
    
    return True, "", warnings


# ─────────────────────────────────────────────────────────────────────────────
# Helper to extract column lists from full config
# ─────────────────────────────────────────────────────────────────────────────

def extract_valid_columns(full_yaml: str) -> tuple[list[str], list[str]]:
    """
    Extract input and output column names from full config.
    
    Returns:
        (input_columns, output_columns)
    """
    try:
        config = yaml.safe_load(full_yaml) or {}
    except yaml.YAMLError:
        return [], []
    
    # Handle nested config structure (full_state.yaml)
    if "phase_transformation_result" in config:
        nested = config.get("phase_transformation_result", {})
        if "corrected_config" in nested:
            config = nested["corrected_config"]
    
    input_cols = []
    output_cols = []
    
    # Input columns from phase_3_columns.select_columns
    p3 = config.get("phase_3_columns", {})
    select_details = p3.get("select_columns", {}).get("details", {})
    for col_obj in select_details.get("columns", []):
        # Handle both string columns and dict columns with name key
        if isinstance(col_obj, str):
            input_cols.append(col_obj)
        elif isinstance(col_obj, dict):
            name = col_obj.get("name", "")
            if name:
                input_cols.append(name)
    
    # Output columns from phase_3_columns.rename_columns
    rename_mappings = p3.get("rename_columns", {}).get("details", {}).get("mappings", {}) or {}
    output_cols = list(rename_mappings.keys())
    
    # Fallback to metadata.column_mapping
    if not input_cols and not output_cols:
        col_mapping = config.get("metadata", {}).get("column_mapping", {})
        output_cols = list(col_mapping.keys())
        input_cols = [v for v in col_mapping.values() if v and "DERIVED" not in str(v).upper()]
    
    return input_cols, output_cols
