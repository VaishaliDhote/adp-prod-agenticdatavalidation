# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs;
# strip any pandas data-mutation helpers.
# ============================================================================
"""
Generic Row Operations Module
==============================

This module provides generic, data-agnostic functions for common Excel row operations:
- FILTER/WHERE: Filter rows based on conditions
- SORT: Sort rows by columns
- REMOVE DUPLICATES: Identify and remove duplicate rows
- ADD ROWS (TOTALS/SUBTOTALS): Add summary rows with aggregations

Each function is designed to work with ANY dataset and accepts:
- DataFrames with any schema
- Generic filter specifications (column names, operators, values)
- Generic aggregation specifications (group columns, aggregate functions)
- Edge case handling for null values, type conversions, etc.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Callable, Tuple, Union
from enum import Enum
from datetime import datetime, date


class FilterOperator(Enum):
    """Supported filter operators"""
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    BEGINS_WITH = "begins_with"
    ENDS_WITH = "ends_with"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    TOP_N = "top_n"


class AggregateFunction(Enum):
    """Supported aggregate functions"""
    SUM = "sum"
    AVG = "avg"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    MIN = "min"
    MAX = "max"
    FIRST = "first"
    LAST = "last"
    CONCAT = "concat"


# ─────────────────────────────────────────────────────────────────────────────
# J. FILTER / WHERE Operations
# ─────────────────────────────────────────────────────────────────────────────

def filter_rows(
    df: pd.DataFrame,
    conditions: Union[Dict, List[Dict]],
    logic: str = "AND",
    invert: bool = False
) -> pd.DataFrame:
    """
    Filter rows based on one or more conditions.
    
    Args:
        df: Input DataFrame
        conditions: Single condition dict or list of condition dicts
                   Each dict: {"column": str, "operator": str, "value": Any}
                   operator: "equals", "gt", "contains", "between", etc.
        logic: "AND" (all conditions) or "OR" (any condition)
        invert: If True, inverts the final filter (NOT logic)
    
    Returns:
        Filtered DataFrame
    
    Examples:
        # Single condition: column equals value
        filter_rows(df, {"column": "Region", "operator": "equals", "value": "West"})
        
        # Multiple conditions with AND logic
        filter_rows(df, [
            {"column": "Region", "operator": "equals", "value": "West"},
            {"column": "Sales", "operator": "gt", "value": 1000}
        ], logic="AND")
        
        # Text filter: contains
        filter_rows(df, {"column": "CustomerName", "operator": "contains", "value": "an"})
        
        # Date filter: between
        filter_rows(df, {
            "column": "OrderDate",
            "operator": "between",
            "value": [datetime(2025, 1, 1), datetime(2025, 6, 30)]
        })
        
        # Top N by column value
        filter_rows(df, {"column": "Sales", "operator": "top_n", "value": 10})
    """
    if df.empty:
        return df.copy()
    
    # Normalize to list of conditions
    if isinstance(conditions, dict):
        conditions = [conditions]
    
    if not conditions:
        return df.copy()
    
    # Apply each condition and combine with logic
    masks = []
    for condition in conditions:
        mask = _apply_single_filter(df, condition)
        masks.append(mask)
    
    # Combine masks
    if logic.upper() == "AND":
        combined_mask = pd.Series([True] * len(df), index=df.index)
        for mask in masks:
            combined_mask = combined_mask & mask
    elif logic.upper() == "OR":
        combined_mask = pd.Series([False] * len(df), index=df.index)
        for mask in masks:
            combined_mask = combined_mask | mask
    else:
        raise ValueError(f"logic must be 'AND' or 'OR', got {logic}")
    
    # Invert if requested
    if invert:
        combined_mask = ~combined_mask
    
    result = df[combined_mask].copy()
    return result.reset_index(drop=True)


def _apply_single_filter(df: pd.DataFrame, condition: Dict) -> pd.Series:
    """
    Apply a single filter condition to DataFrame.
    Returns boolean Series where True means row matches the condition.
    """
    column = condition.get("column")
    operator = condition.get("operator", "equals").lower()
    value = condition.get("value")
    
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found in DataFrame")
    
    col_data = df[column]
    
    # Handle NULL checks first (work with any dtype)
    if operator == FilterOperator.IS_NULL.value or operator == "is_null":
        return col_data.isna()
    elif operator == FilterOperator.IS_NOT_NULL.value or operator == "is_not_null":
        return col_data.notna()
    
    # Handle numeric comparisons
    if operator in [FilterOperator.GREATER_THAN.value, "gt"]:
        return col_data > value
    elif operator in [FilterOperator.GREATER_THAN_OR_EQUAL.value, "gte"]:
        return col_data >= value
    elif operator in [FilterOperator.LESS_THAN.value, "lt"]:
        return col_data < value
    elif operator in [FilterOperator.LESS_THAN_OR_EQUAL.value, "lte"]:
        return col_data <= value
    
    # Handle equality (case-insensitive for strings)
    elif operator in [FilterOperator.EQUALS.value, "equals"]:
        if col_data.dtype == "object":
            return col_data.str.lower() == str(value).lower()
        else:
            return col_data == value
    
    elif operator in [FilterOperator.NOT_EQUALS.value, "not_equals"]:
        if col_data.dtype == "object":
            return col_data.str.lower() != str(value).lower()
        else:
            return col_data != value
    
    # Handle text filters (case-insensitive)
    elif operator in [FilterOperator.CONTAINS.value, "contains"]:
        if col_data.dtype != "object":
            col_data = col_data.astype(str)
        return col_data.str.contains(str(value), case=False, na=False, regex=False)
    
    elif operator in [FilterOperator.NOT_CONTAINS.value, "not_contains"]:
        if col_data.dtype != "object":
            col_data = col_data.astype(str)
        return ~col_data.str.contains(str(value), case=False, na=False, regex=False)
    
    elif operator in [FilterOperator.BEGINS_WITH.value, "begins_with"]:
        if col_data.dtype != "object":
            col_data = col_data.astype(str)
        return col_data.str.lower().str.startswith(str(value).lower())
    
    elif operator in [FilterOperator.ENDS_WITH.value, "ends_with"]:
        if col_data.dtype != "object":
            col_data = col_data.astype(str)
        return col_data.str.lower().str.endswith(str(value).lower())
    
    # Handle BETWEEN (for dates, numbers, etc.)
    elif operator in [FilterOperator.BETWEEN.value, "between"]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("BETWEEN operator requires value as [min, max]")
        return (col_data >= value[0]) & (col_data <= value[1])
    
    # Handle IN / NOT IN
    elif operator in [FilterOperator.IN.value, "in"]:
        if not isinstance(value, (list, tuple)):
            value = [value]
        if col_data.dtype == "object":
            return col_data.str.lower().isin([str(v).lower() for v in value])
        else:
            return col_data.isin(value)
    
    elif operator in [FilterOperator.NOT_IN.value, "not_in"]:
        if not isinstance(value, (list, tuple)):
            value = [value]
        if col_data.dtype == "object":
            return ~col_data.str.lower().isin([str(v).lower() for v in value])
        else:
            return ~col_data.isin(value)
    
    # Handle TOP N (only works with numeric columns)
    elif operator in [FilterOperator.TOP_N.value, "top_n"]:
        n = int(value)
        # Get top N indices based on column value (descending)
        top_indices = col_data.nlargest(n).index
        return df.index.isin(top_indices)
    
    else:
        raise ValueError(f"Unknown operator: {operator}")


# ─────────────────────────────────────────────────────────────────────────────
# K. SORT Operations
# ─────────────────────────────────────────────────────────────────────────────

def sort_rows(
    df: pd.DataFrame,
    sort_specs: Union[Dict, List[Dict]],
    nulls_position: str = "last"
) -> pd.DataFrame:
    """
    Sort rows by one or more columns.
    
    Args:
        df: Input DataFrame
        sort_specs: Single sort spec or list of sort specs
                    Each dict: {"column": str, "ascending": bool}
                    ascending: True for A→Z, False for Z→A
        nulls_position: "first" or "last" - where to place null values
    
    Returns:
        Sorted DataFrame
    
    Examples:
        # Single column sort (ascending)
        sort_rows(df, {"column": "OrderDate", "ascending": True})
        
        # Multi-column sort
        sort_rows(df, [
            {"column": "Region", "ascending": True},
            {"column": "Sales", "ascending": False}
        ])
        
        # Descending sort
        sort_rows(df, {"column": "Sales", "ascending": False})
    """
    if df.empty:
        return df.copy()
    
    # Normalize to list
    if isinstance(sort_specs, dict):
        sort_specs = [sort_specs]
    
    if not sort_specs:
        return df.copy()
    
    # Validate columns exist
    for spec in sort_specs:
        col = spec.get("column")
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found in DataFrame")
    
    # Extract sort info
    sort_columns = [spec.get("column") for spec in sort_specs]
    ascending_flags = [spec.get("ascending", True) for spec in sort_specs]
    
    # Handle nulls placement
    # Note: pandas sort_values doesn't directly support "nulls first/last"
    # so we use na_position parameter
    na_position = "first" if nulls_position.lower() == "first" else "last"
    
    result = df.sort_values(
        by=sort_columns,
        ascending=ascending_flags,
        na_position=na_position,
        ignore_index=True
    )
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# L. REMOVE DUPLICATES Operations
# ─────────────────────────────────────────────────────────────────────────────

def remove_duplicates(
    df: pd.DataFrame,
    subset: Optional[List[str]] = None,
    keep: str = "first"
) -> pd.DataFrame:
    """
    Remove duplicate rows from DataFrame.
    
    Args:
        df: Input DataFrame
        subset: List of column names to consider for duplicates.
                If None, considers all columns.
                If specified, duplicates are identified based on these columns only.
        keep: Which duplicate to keep - "first", "last", or False (remove all)
    
    Returns:
        DataFrame with duplicates removed
    
    Examples:
        # Remove exact duplicate rows (all columns)
        remove_duplicates(df)
        
        # Remove duplicates based on specific columns
        remove_duplicates(df, subset=["CustomerID", "OrderDate"])
        
        # Keep last occurrence instead of first
        remove_duplicates(df, subset=["CustomerID", "OrderDate"], keep="last")
        
        # Remove ALL duplicates (keep none)
        remove_duplicates(df, subset=["CustomerID", "OrderDate"], keep=False)
    """
    if df.empty:
        return df.copy()
    
    if not subset:
        # Consider all columns
        subset = list(df.columns)
    else:
        # Validate columns exist
        missing_cols = set(subset) - set(df.columns)
        if missing_cols:
            raise ValueError(f"Columns not found: {missing_cols}")
    
    if keep not in ["first", "last", False]:
        raise ValueError("keep must be 'first', 'last', or False")
    
    result = df.drop_duplicates(subset=subset, keep=keep, ignore_index=True)
    
    return result


def identify_duplicates(
    df: pd.DataFrame,
    subset: Optional[List[str]] = None,
    mark_type: str = "all"
) -> Dict[str, Any]:
    """
    Identify duplicate rows without removing them.
    Useful for analysis and reporting.
    
    Args:
        df: Input DataFrame
        subset: Columns to consider (if None, all columns)
        mark_type: "first", "last", or "all" - marks which occurrences are duplicates
    
    Returns:
        Dict with:
        - duplicated_df: DataFrame with duplicate_id column (0=unique, 1+=duplicate group id)
        - duplicate_count: Number of duplicate rows
        - duplicate_groups: List of duplicate groups
        - statistics: Summary statistics
    
    Examples:
        result = identify_duplicates(df, subset=["CustomerID", "OrderDate"])
        print(f"Total duplicates: {result['duplicate_count']}")
        print(f"Duplicate groups: {result['duplicate_groups']}")
    """
    if df.empty:
        return {
            "duplicated_df": df.copy(),
            "duplicate_count": 0,
            "duplicate_groups": [],
            "statistics": {"total_rows": 0, "unique_rows": 0, "duplicate_rows": 0}
        }
    
    if not subset:
        subset = list(df.columns)
    else:
        missing_cols = set(subset) - set(df.columns)
        if missing_cols:
            raise ValueError(f"Columns not found: {missing_cols}")
    
    result_df = df.copy()
    
    if mark_type == "all":
        # Mark all occurrences of duplicates
        is_duplicate = result_df.duplicated(subset=subset, keep=False)
    elif mark_type == "first":
        # Mark duplicates except first
        is_duplicate = result_df.duplicated(subset=subset, keep="first")
    elif mark_type == "last":
        # Mark duplicates except last
        is_duplicate = result_df.duplicated(subset=subset, keep="last")
    else:
        raise ValueError("mark_type must be 'all', 'first', or 'last'")
    
    # Add duplicate marker
    result_df["__duplicate_marker"] = is_duplicate.astype(int)
    
    # Create duplicate groups
    duplicate_groups = []
    if is_duplicate.any():
        # Group by subset columns
        grouped = result_df[is_duplicate].groupby(subset)
        for group_key, group_df in grouped:
            duplicate_groups.append({
                "key": dict(zip(subset, [group_key] if isinstance(group_key, (str, int, float)) else group_key)),
                "count": len(group_df),
                "row_indices": group_df.index.tolist()
            })
    
    statistics = {
        "total_rows": len(result_df),
        "unique_rows": len(result_df[~is_duplicate]),
        "duplicate_rows": is_duplicate.sum(),
        "duplicate_groups_count": len(duplicate_groups)
    }
    
    return {
        "duplicated_df": result_df,
        "duplicate_count": is_duplicate.sum(),
        "duplicate_groups": duplicate_groups,
        "statistics": statistics
    }


# ─────────────────────────────────────────────────────────────────────────────
# M. ADD ROWS (TOTALS/SUBTOTALS) Operations
# ─────────────────────────────────────────────────────────────────────────────

def add_totals_row(
    df: pd.DataFrame,
    aggregations: List[Dict[str, Any]],
    row_label: str = "TOTAL",
    label_column: Optional[str] = None
) -> pd.DataFrame:
    """
    Add a totals row (Grand Total) to DataFrame.
    
    Args:
        df: Input DataFrame
        aggregations: List of aggregation specs
                     Each dict: {
                         "column": str,
                         "function": str (sum, avg, count, min, max, etc.)
                         "as": str (optional, column name for result)
                     }
        row_label: Label for the totals row
        label_column: Column to put the row_label in (usually first column)
    
    Returns:
        DataFrame with totals row appended
    
    Examples:
        df_with_total = add_totals_row(
            df,
            aggregations=[
                {"column": "Quantity", "function": "sum"},
                {"column": "Sales", "function": "sum"},
                {"column": "Profit", "function": "sum"}
            ],
            row_label="TOTAL",
            label_column="OrderID"
        )
    """
    if df.empty:
        return df.copy()
    
    aggregations = aggregations or []
    if not aggregations:
        return df.copy()
    
    # Calculate aggregations
    total_row = {}
    
    for agg in aggregations:
        col = agg.get("column")
        func = agg.get("function", "sum").lower()
        result_col = agg.get("as", col)
        
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found in DataFrame")
        
        col_data = df[col]
        
        if func in [AggregateFunction.SUM.value, "sum"]:
            value = pd.to_numeric(col_data, errors="coerce").sum()
        elif func in [AggregateFunction.AVG.value, "avg", "average"]:
            value = pd.to_numeric(col_data, errors="coerce").mean()
        elif func in [AggregateFunction.COUNT.value, "count"]:
            value = col_data.notna().sum()
        elif func in [AggregateFunction.COUNT_DISTINCT.value, "count_distinct"]:
            value = col_data.nunique()
        elif func in [AggregateFunction.MIN.value, "min"]:
            value = pd.to_numeric(col_data, errors="coerce").min()
        elif func in [AggregateFunction.MAX.value, "max"]:
            value = pd.to_numeric(col_data, errors="coerce").max()
        elif func in [AggregateFunction.FIRST.value, "first"]:
            value = col_data.iloc[0] if len(col_data) > 0 else None
        elif func in [AggregateFunction.LAST.value, "last"]:
            value = col_data.iloc[-1] if len(col_data) > 0 else None
        elif func in [AggregateFunction.CONCAT.value, "concat"]:
            # For concat, join all non-null values with comma
            value = ", ".join(col_data.astype(str).dropna())
        else:
            raise ValueError(f"Unknown aggregation function: {func}")
        
        total_row[result_col] = value
    
    # Add label
    if label_column:
        total_row[label_column] = row_label
    
    # Fill missing columns with empty string
    for col in df.columns:
        if col not in total_row:
            total_row[col] = ""
    
    # Create totals DataFrame
    total_df = pd.DataFrame([total_row])
    
    # Reorder columns to match original
    total_df = total_df[df.columns]
    
    # Append to original
    result = pd.concat([df, total_df], ignore_index=True)
    
    return result


def add_subtotals_rows(
    df: pd.DataFrame,
    group_by_columns: List[str],
    aggregations: List[Dict[str, Any]],
    include_grand_total: bool = True,
    group_label_column: Optional[str] = None,
    label_template: str = "{group_key} SUBTOTAL"
) -> pd.DataFrame:
    """
    Add subtotal rows grouped by one or more columns.
    
    Args:
        df: Input DataFrame
        group_by_columns: Columns to group by
        aggregations: List of aggregation specs
        include_grand_total: If True, add a grand total row at the end
        group_label_column: Column to put subtotal labels
        label_template: Template for subtotal labels (can use {group_key})
    
    Returns:
        DataFrame with subtotal rows inserted after each group
    
    Examples:
        df_with_subtotals = add_subtotals_rows(
            df,
            group_by_columns=["Region"],
            aggregations=[
                {"column": "Sales", "function": "sum"},
                {"column": "Quantity", "function": "sum"},
                {"column": "Profit", "function": "sum"}
            ],
            include_grand_total=True,
            group_label_column="Region",
            label_template="{group_key} SUBTOTAL"
        )
    """
    if df.empty:
        return df.copy()
    
    if not group_by_columns:
        raise ValueError("group_by_columns cannot be empty")
    
    # Validate columns
    for col in group_by_columns:
        if col not in df.columns:
            raise ValueError(f"Group column '{col}' not found in DataFrame")
    
    aggregations = aggregations or []
    if not aggregations:
        return df.copy()
    
    result_rows = []
    grouped = df.groupby(group_by_columns, sort=False)
    
    for group_key, group_df in grouped:
        # Add all rows from this group
        result_rows.extend(group_df.values.tolist())
        
        # Calculate subtotal for this group
        subtotal_row = {}
        
        for agg in aggregations:
            col = agg.get("column")
            func = agg.get("function", "sum").lower()
            result_col = agg.get("as", col)
            
            col_data = group_df[col]
            
            if func in [AggregateFunction.SUM.value, "sum"]:
                value = pd.to_numeric(col_data, errors="coerce").sum()
            elif func in [AggregateFunction.AVG.value, "avg", "average"]:
                value = pd.to_numeric(col_data, errors="coerce").mean()
            elif func in [AggregateFunction.COUNT.value, "count"]:
                value = col_data.notna().sum()
            elif func in [AggregateFunction.COUNT_DISTINCT.value, "count_distinct"]:
                value = col_data.nunique()
            elif func in [AggregateFunction.MIN.value, "min"]:
                value = pd.to_numeric(col_data, errors="coerce").min()
            elif func in [AggregateFunction.MAX.value, "max"]:
                value = pd.to_numeric(col_data, errors="coerce").max()
            elif func in [AggregateFunction.FIRST.value, "first"]:
                value = col_data.iloc[0] if len(col_data) > 0 else None
            elif func in [AggregateFunction.LAST.value, "last"]:
                value = col_data.iloc[-1] if len(col_data) > 0 else None
            elif func in [AggregateFunction.CONCAT.value, "concat"]:
                value = ", ".join(col_data.astype(str).dropna())
            else:
                raise ValueError(f"Unknown aggregation function: {func}")
            
            subtotal_row[result_col] = value
        
        # Add group label
        if group_label_column:
            # Format group key
            if isinstance(group_key, tuple):
                group_key_str = " | ".join(str(k) for k in group_key)
            else:
                group_key_str = str(group_key)
            
            label = label_template.format(group_key=group_key_str)
            subtotal_row[group_label_column] = label
        
        # Fill missing columns
        for col in df.columns:
            if col not in subtotal_row:
                subtotal_row[col] = ""
        
        # Add subtotal row
        result_rows.append(subtotal_row)
    
    # Create result DataFrame
    result = pd.DataFrame(result_rows)
    
    # Reorder columns
    result = result[df.columns]
    
    # Add grand total if requested
    if include_grand_total:
        result = add_totals_row(
            result,
            aggregations=aggregations,
            row_label="GRAND TOTAL",
            label_column=group_label_column
        )
    
    return result.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def apply_row_operations_pipeline(
    df: pd.DataFrame,
    operations: List[Dict[str, Any]],
    verbose: bool = False
) -> Tuple[pd.DataFrame, List[Dict]]:
    """
    Apply a sequence of row operations in order.
    
    Args:
        df: Input DataFrame
        operations: List of operation dicts, each with:
                   {
                       "operation": "filter|sort|deduplicate|totals|subtotals",
                       "params": {...},
                       "name": "optional description"
                   }
        verbose: If True, print operation details
    
    Returns:
        Tuple of (result_df, operation_results)
    
    Example:
        pipeline = [
            {
                "operation": "filter",
                "name": "Filter West Region",
                "params": {
                    "conditions": {"column": "Region", "operator": "equals", "value": "West"},
                    "logic": "AND"
                }
            },
            {
                "operation": "sort",
                "name": "Sort by Sales Descending",
                "params": {
                    "sort_specs": {"column": "Sales", "ascending": False}
                }
            }
        ]
        
        result_df, results = apply_row_operations_pipeline(df, pipeline, verbose=True)
    """
    result_df = df.copy()
    operation_results = []
    
    for i, op in enumerate(operations):
        op_type = op.get("operation", "").lower()
        op_name = op.get("name", f"Operation {i+1}")
        params = op.get("params", {})
        
        try:
            if op_type == "filter":
                input_rows = len(result_df)
                result_df = filter_rows(result_df, **params)
                output_rows = len(result_df)
                status = "success"
                message = f"Filtered {input_rows} → {output_rows} rows"
            
            elif op_type == "sort":
                result_df = sort_rows(result_df, **params)
                status = "success"
                message = f"Sorted {len(result_df)} rows"
            
            elif op_type == "deduplicate" or op_type == "remove_duplicates":
                input_rows = len(result_df)
                result_df = remove_duplicates(result_df, **params)
                output_rows = len(result_df)
                status = "success"
                message = f"Deduplicated {input_rows} → {output_rows} rows ({input_rows - output_rows} removed)"
            
            elif op_type == "totals":
                result_df = add_totals_row(result_df, **params)
                status = "success"
                message = f"Added totals row"
            
            elif op_type == "subtotals":
                result_df = add_subtotals_rows(result_df, **params)
                status = "success"
                message = f"Added subtotal rows"
            
            else:
                status = "error"
                message = f"Unknown operation: {op_type}"
            
        except Exception as e:
            status = "error"
            message = str(e)
        
        operation_results.append({
            "operation": op_type,
            "name": op_name,
            "status": status,
            "message": message,
            "output_rows": len(result_df)
        })
        
        if verbose:
            print(f"  [{i+1}] {op_name} ({op_type})")
            print(f"       → {message}")
    
    return result_df, operation_results
