# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Row Text Filter Utility
========================
Detects pattern-based text filters applied to string columns between
input_df and output_df.

Detection logic delegates to the existing TextFilter class
(utils/transformations/text_filters.py) which was ported from the
text_filter.ipynb notebook.

Supported operators (in priority order):
  EQUALS, NOT_EQUALS, ENDS_WITH, BEGINS_WITH, CONTAINS, NOT_CONTAINS

The detector works sequentially — each confirmed filter is applied before
the next column is evaluated.
"""

import pandas as pd
from typing import Any, Dict, List, Optional

from utils.transformations.text_filters import TextFilter


# ─────────────────────────────────────────────────────────────────────────────
# Operator normalisation
# ─────────────────────────────────────────────────────────────────────────────

# Map TextFilter operator strings → canonical phase_2_rows operator symbols
_OPERATOR_MAP: Dict[str, str] = {
    "EQUALS":       "==",
    "NOT_EQUALS":   "!=",
    "ENDS_WITH":    "ends_with",
    "BEGINS_WITH":  "begins_with",
    "CONTAINS":     "contains",
    "NOT_CONTAINS": "not_contains",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_text_filter_config(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    column_mapping: Optional[Dict[str, str]] = None,
    threshold: float = 0.5,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Detect text-pattern row filters and return the phase_2_rows 'filter' config
    block contribution from text operators.

    Args:
        input_df:       Input DataFrame.
        output_df:      Output DataFrame (after text filters applied).
        column_mapping: Optional {output_col: input_col} name mapping.
        threshold:      Exclusion-rate threshold passed to TextFilter (default 0.5).
        verbose:        Print per-column detection details.

    Returns:
        {
            "applied": bool,
            "details": {
                "rules": [
                    {"column": str, "operator": str, "value": str}
                ]
            }
        }
    """
    # Sentinel values for derived columns
    _DERIVED_SENTINELS = {"DERIVED COLUMN", "MULTISOURCE COLUMN", "UNKNOWN MAPPING TYPE"}
    
    # column_mapping is {output_col: input_col} - use directly to rename output→input
    if column_mapping:
        rename_map = {out: inp for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}
        out_df = output_df.rename(columns=rename_map)
        # Drop duplicate column names that arise when multiple output cols map to
        # the same input col (e.g. Full_Name + Reconstructed_Full_Name → Full_Name).
        out_df = out_df.loc[:, ~out_df.columns.duplicated()]
    else:
        out_df = output_df

    detector = TextFilter(threshold=threshold)
    raw_results = detector.detect(input_df, out_df, verbose=verbose)

    # Build inverse mapping {input_col: output_col} to translate detected names to output namespace
    inv_map = {}
    if column_mapping:
        inv_map = {inp: out for out, inp in column_mapping.items() if inp not in _DERIVED_SENTINELS}

    rules: List[Dict[str, Any]] = []
    for r in raw_results:
        # Resolve column name: prefer output column name when mapping provided
        col = r.get("column", r.get("input_column", ""))
        input_col = r.get("input_column", col)
        col = inv_map.get(input_col, col)

        raw_op = r.get("operator", "")
        operator = _OPERATOR_MAP.get(raw_op, raw_op.lower())

        rules.append({
            "column":   col,
            "operator": operator,
            "value":    r.get("value", ""),
        })

    return {
        "applied": len(rules) > 0,
        "details": {"rules": rules},
    }
