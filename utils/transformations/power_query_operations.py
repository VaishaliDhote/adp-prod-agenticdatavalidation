# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detector (build_*_config). REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs.
# ============================================================================
"""
Power Query Operations Module
==============================

Generic functions class for all Power Query-related transformations.
Supports column-level transformations including:
  - SELECT / PROJECT: Choose specific columns
  - REORDER: Change column sequence
  - RENAME: Modify column headers
  - ADD CALCULATED: Create new columns with formulas

Input: Input DataFrame, Output DataFrame, Column Mappings
Output: Transformed DataFrames with applied operations
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple, Union, Callable
from dataclasses import dataclass
from enum import Enum
import json
import re
from datetime import datetime


class TransformationType(Enum):
    """Enumeration of transformation types."""
    SELECT = "select"
    PROJECT = "project"
    REORDER = "reorder"
    RENAME = "rename"
    CALCULATED = "calculated"
    FILTER = "filter"
    SORT = "sort"
    AGGREGATE = "aggregate"
    JOIN = "join"


@dataclass
class ColumnMapping:
    """Represents mapping of a single output column."""
    output_column: str
    mapping_type: str  # 'single_source', 'multiple_source', 'calculated'
    confidence: float
    source_groups: List[Dict[str, Any]]
    is_join_key_candidate: bool = False
    transformation_type: Optional[str] = None


class PowerQueryOperations:
    """
    Generic Power Query operations class for data transformations.
    
    Handles all standard PQ operations: select, reorder, rename, and calculated columns.
    Supports complex transformations with multiple source columns and formula expressions.
    """

    def __init__(self, input_df: pd.DataFrame, output_df: pd.DataFrame, 
                 column_mappings: Optional[Dict[str, Any]] = None):
        """
        Initialize Power Query operations.
        
        Args:
            input_df: Source DataFrame
            output_df: Target DataFrame (for reference/validation)
            column_mappings: Column mapping configuration (JSON dict)
        """
        self.input_df = input_df.copy()
        self.output_df = output_df.copy()
        self.column_mappings = column_mappings or {}
        self.transformation_history = []
        self.result_df = input_df.copy()

    # =========================================================================
    # A) SELECT / PROJECT COLUMNS
    # =========================================================================

    def select_columns(self, columns: List[str], keep_order: bool = True) -> pd.DataFrame:
        """
        Select and keep only specific columns (projection).
        
        Args:
            columns: List of column names to keep
            keep_order: Whether to preserve column order
            
        Returns:
            DataFrame with only selected columns
            
        Example:
            >>> ops.select_columns(['Name', 'Email', 'Salary'])
        """
        available_cols = [col for col in columns if col in self.result_df.columns]
        
        if not available_cols:
            raise ValueError(f"None of the requested columns exist in DataFrame: {columns}")
        
        self.result_df = self.result_df[available_cols]
        
        self.transformation_history.append({
            "type": TransformationType.SELECT.value,
            "columns": available_cols,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    def project_columns(self, columns: List[str]) -> pd.DataFrame:
        """Alias for select_columns. Project specific columns."""
        return self.select_columns(columns)

    def drop_columns(self, columns: List[str]) -> pd.DataFrame:
        """
        Drop (remove) specific columns.
        
        Args:
            columns: List of column names to drop
            
        Returns:
            DataFrame without the dropped columns
            
        Example:
            >>> ops.drop_columns(['City', 'State', 'Country'])
        """
        cols_to_drop = [col for col in columns if col in self.result_df.columns]
        
        self.result_df = self.result_df.drop(columns=cols_to_drop)
        
        self.transformation_history.append({
            "type": TransformationType.SELECT.value,
            "operation": "drop",
            "columns": cols_to_drop,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    # =========================================================================
    # B) REORDER COLUMNS
    # =========================================================================

    def reorder_columns(self, column_order: List[str]) -> pd.DataFrame:
        """
        Change the sequence of columns.
        
        Args:
            column_order: List of column names in desired order
            
        Returns:
            DataFrame with columns reordered
            
        Example:
            >>> ops.reorder_columns(['OrderID', 'CustomerID', 'OrderDate', 'Amount'])
        """
        # Validate that all columns exist
        missing_cols = [col for col in column_order if col not in self.result_df.columns]
        if missing_cols:
            raise ValueError(f"Columns not found: {missing_cols}")
        
        # Keep only the columns specified in the order
        self.result_df = self.result_df[column_order]
        
        self.transformation_history.append({
            "type": TransformationType.REORDER.value,
            "column_order": column_order,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    def move_column_after(self, column: str, after_column: str) -> pd.DataFrame:
        """
        Move a column to appear after a specific column.
        
        Args:
            column: Column to move
            after_column: Column after which to place it
            
        Returns:
            DataFrame with reordered columns
            
        Example:
            >>> ops.move_column_after('Full_Name', 'FirstName')
        """
        if column not in self.result_df.columns or after_column not in self.result_df.columns:
            raise ValueError("Column not found")
        
        cols = list(self.result_df.columns)
        cols.remove(column)
        
        insert_idx = cols.index(after_column) + 1
        cols.insert(insert_idx, column)
        
        return self.reorder_columns(cols)

    def move_column_before(self, column: str, before_column: str) -> pd.DataFrame:
        """Move a column to appear before a specific column."""
        if column not in self.result_df.columns or before_column not in self.result_df.columns:
            raise ValueError("Column not found")
        
        cols = list(self.result_df.columns)
        cols.remove(column)
        
        insert_idx = cols.index(before_column)
        cols.insert(insert_idx, column)
        
        return self.reorder_columns(cols)

    # =========================================================================
    # C) RENAME COLUMNS
    # =========================================================================

    def rename_columns(self, rename_map: Dict[str, str]) -> pd.DataFrame:
        """
        Rename column headers.
        
        Args:
            rename_map: Dict mapping old names to new names
            
        Returns:
            DataFrame with renamed columns
            
        Example:
            >>> ops.rename_columns({
            ...     'Order_ID': 'OrderID',
            ...     'First_Name': 'FirstName',
            ...     'Unit_Price': 'UnitPrice'
            ... })
        """
        self.result_df = self.result_df.rename(columns=rename_map)
        
        self.transformation_history.append({
            "type": TransformationType.RENAME.value,
            "rename_map": rename_map,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    def rename_with_pattern(self, pattern: str, replacement: str) -> pd.DataFrame:
        """
        Rename columns using regex pattern.
        
        Args:
            pattern: Regex pattern to match in column names
            replacement: Replacement string (can use regex groups)
            
        Returns:
            DataFrame with renamed columns
            
        Example:
            >>> ops.rename_with_pattern(r'_', '')  # Remove underscores
            >>> ops.rename_with_pattern(r'^([a-z])', r'\1')  # Capitalize first letter
        """
        rename_map = {}
        for col in self.result_df.columns:
            new_col = re.sub(pattern, replacement, col)
            if new_col != col:
                rename_map[col] = new_col
        
        return self.rename_columns(rename_map)

    def to_camel_case(self) -> pd.DataFrame:
        """Convert all column names to camelCase."""
        def to_camel(name: str) -> str:
            parts = name.split('_')
            return parts[0].lower() + ''.join(word.capitalize() for word in parts[1:])
        
        rename_map = {col: to_camel(col) for col in self.result_df.columns}
        return self.rename_columns(rename_map)

    def to_pascal_case(self) -> pd.DataFrame:
        """Convert all column names to PascalCase."""
        def to_pascal(name: str) -> str:
            return ''.join(word.capitalize() for word in name.split('_'))
        
        rename_map = {col: to_pascal(col) for col in self.result_df.columns}
        return self.rename_columns(rename_map)

    def to_snake_case(self) -> pd.DataFrame:
        """Convert all column names to snake_case."""
        def to_snake(name: str) -> str:
            s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
        
        rename_map = {col: to_snake(col) for col in self.result_df.columns}
        return self.rename_columns(rename_map)

    # =========================================================================
    # D) ADD CALCULATED COLUMNS
    # =========================================================================

    def add_calculated_column(self, output_column: str, formula: Callable[[pd.DataFrame], pd.Series],
                            source_columns: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Add a new column with calculated values using a formula function.
        
        Args:
            output_column: Name of the new column
            formula: Function that takes DataFrame and returns Series
            source_columns: List of source columns used (for documentation)
            
        Returns:
            DataFrame with new calculated column
            
        Example:
            >>> ops.add_calculated_column(
            ...     'Full_Name',
            ...     lambda df: df['First_Name'] + ' ' + df['Last_Name'],
            ...     source_columns=['First_Name', 'Last_Name']
            ... )
        """
        self.result_df[output_column] = formula(self.result_df)
        
        self.transformation_history.append({
            "type": TransformationType.CALCULATED.value,
            "output_column": output_column,
            "source_columns": source_columns or [],
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    def add_concatenation_column(self, output_column: str, columns: List[str], 
                                separator: str = '') -> pd.DataFrame:
        """
        Create a new column by concatenating multiple columns.
        
        Args:
            output_column: Name of the new column
            columns: List of columns to concatenate
            separator: String to insert between values
            
        Returns:
            DataFrame with concatenated column
            
        Example:
            >>> ops.add_concatenation_column('Full_Name', 
            ...                              ['First_Name', 'Last_Name'], 
            ...                              separator=' ')
        """
        def concat_formula(df):
            return df[columns].astype(str).agg(separator.join, axis=1)
        
        return self.add_calculated_column(output_column, concat_formula, columns)

    def add_arithmetic_column(self, output_column: str, columns: List[str], 
                             operation: str = 'sum', round_decimals: int = 2) -> pd.DataFrame:
        """
        Create a new column using arithmetic operations on multiple columns.
        
        Args:
            output_column: Name of the new column
            columns: List of numeric columns
            operation: 'sum', 'product', 'difference', 'mean', 'max', 'min'
            round_decimals: Number of decimals to round to
            
        Returns:
            DataFrame with calculated column
            
        Example:
            >>> ops.add_arithmetic_column('Total_Amount', 
            ...                          ['UnitPrice', 'Quantity'], 
            ...                          operation='product')
        """
        operations = {
            'sum': lambda df, cols: df[cols].sum(axis=1),
            'product': lambda df, cols: df[cols].prod(axis=1),
            'difference': lambda df, cols: df[cols[0]] - df[cols[1]],
            'mean': lambda df, cols: df[cols].mean(axis=1),
            'max': lambda df, cols: df[cols].max(axis=1),
            'min': lambda df, cols: df[cols].min(axis=1),
        }
        
        if operation not in operations:
            raise ValueError(f"Unknown operation: {operation}")
        
        def formula(df):
            result = operations[operation](df, columns)
            return result.round(round_decimals) if round_decimals is not None else result
        
        return self.add_calculated_column(output_column, formula, columns)

    def add_conditional_column(self, output_column: str, 
                             conditions: List[Tuple[str, Any]], 
                             default_value: Any = None) -> pd.DataFrame:
        """
        Create a new column with conditional logic (IF/ELSE).
        
        Args:
            output_column: Name of the new column
            conditions: List of (condition_function, value) tuples
            default_value: Value if no conditions match
            
        Returns:
            DataFrame with conditional column
            
        Example:
            >>> ops.add_conditional_column('High_Value_Flag',
            ...     conditions=[
            ...         (lambda df: df['Total_Amount'] >= 1000, 'High'),
            ...         (lambda df: df['Total_Amount'] >= 500, 'Medium'),
            ...     ],
            ...     default_value='Regular'
            ... )
        """
        def formula(df):
            result = pd.Series([default_value] * len(df), index=df.index)
            for condition, value in reversed(conditions):
                mask = condition(df)
                result[mask] = value
            return result
        
        return self.add_calculated_column(output_column, formula)

    def add_date_part_column(self, output_column: str, date_column: str, 
                            part: str = 'year') -> pd.DataFrame:
        """
        Extract date parts into new column.
        
        Args:
            output_column: Name of the new column
            date_column: Name of date column
            part: 'year', 'month', 'day', 'quarter', 'week', 'day_of_week'
            
        Returns:
            DataFrame with date part column
            
        Example:
            >>> ops.add_date_part_column('Year', 'Order_Date', part='year')
            >>> ops.add_date_part_column('Month', 'Order_Date', part='month')
            >>> ops.add_date_part_column('Quarter', 'Order_Date', part='quarter')
        """
        if date_column not in self.result_df.columns:
            raise ValueError(f"Column '{date_column}' not found")
        
        date_parts = {
            'year': lambda dt: dt.dt.year,
            'month': lambda dt: dt.dt.month,
            'day': lambda dt: dt.dt.day,
            'quarter': lambda dt: dt.dt.quarter,
            'week': lambda dt: dt.dt.isocalendar().week,
            'day_of_week': lambda dt: dt.dt.day_name(),
            'weekday': lambda dt: dt.dt.dayofweek,
        }
        
        if part not in date_parts:
            raise ValueError(f"Unknown date part: {part}")
        
        # Convert to datetime if needed
        if not pd.api.types.is_datetime64_any_dtype(self.result_df[date_column]):
            self.result_df[date_column] = pd.to_datetime(self.result_df[date_column])
        
        self.result_df[output_column] = date_parts[part](self.result_df[date_column])
        
        self.transformation_history.append({
            "type": TransformationType.CALCULATED.value,
            "output_column": output_column,
            "source_columns": [date_column],
            "formula_type": "date_part",
            "date_part": part,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    def add_text_function_column(self, output_column: str, source_column: str,
                                function: str, *args) -> pd.DataFrame:
        """
        Apply text functions to create new column.
        
        Args:
            output_column: Name of the new column
            source_column: Source column name
            function: 'upper', 'lower', 'title', 'left', 'right', 'mid', 'len'
            *args: Additional arguments for the function
            
        Returns:
            DataFrame with text function result
            
        Example:
            >>> ops.add_text_function_column('FirstName_Upper', 'FirstName', 'upper')
            >>> ops.add_text_function_column('Area_Code', 'Phone', 'left', 3)
        """
        text_functions = {
            'upper': lambda s: s.str.upper(),
            'lower': lambda s: s.str.lower(),
            'title': lambda s: s.str.title(),
            'len': lambda s: s.str.len(),
            'left': lambda s, n: s.str[:n],
            'right': lambda s, n: s.str[-n:],
            'mid': lambda s, start, length: s.str[start:start+length],
        }
        
        if function not in text_functions:
            raise ValueError(f"Unknown text function: {function}")
        
        if source_column not in self.result_df.columns:
            raise ValueError(f"Column '{source_column}' not found")
        
        self.result_df[output_column] = text_functions[function](
            self.result_df[source_column].astype(str), *args
        )
        
        self.transformation_history.append({
            "type": TransformationType.CALCULATED.value,
            "output_column": output_column,
            "source_columns": [source_column],
            "formula_type": "text_function",
            "function": function,
            "timestamp": datetime.now().isoformat(),
        })
        
        return self.result_df

    # =========================================================================
    # E) APPLY COLUMN MAPPINGS
    # =========================================================================

    def apply_column_mappings(self, mappings: Dict[str, Any]) -> pd.DataFrame:
        """
        Apply all transformations from a column mapping configuration.
        
        Args:
            mappings: Column mapping dict with structure:
                {
                    "output_sheets": [
                        {
                            "output_sheet": "sheet_name",
                            "column_mappings": [...]
                        }
                    ]
                }
            
        Returns:
            Transformed DataFrame
        """
        if not mappings or 'output_sheets' not in mappings:
            return self.result_df
        
        for sheet_mapping in mappings['output_sheets']:
            column_mappings = sheet_mapping.get('column_mappings', [])
            
            for col_map in column_mappings:
                output_col = col_map.get('output_column')
                mapping_type = col_map.get('mapping_type', 'single_source')
                
                if mapping_type == 'calculated':
                    self._apply_calculated_mapping(col_map)
                elif mapping_type == 'single_source':
                    self._apply_single_source_mapping(col_map)
                elif mapping_type == 'multiple_source':
                    self._apply_multiple_source_mapping(col_map)
        
        return self.result_df

    def _apply_single_source_mapping(self, col_map: Dict[str, Any]) -> None:
        """Apply single source column mapping (select/rename)."""
        output_col = col_map.get('output_column')
        source_groups = col_map.get('source_groups', [])
        
        if not source_groups:
            return
        
        source_info = source_groups[0]['columns'][0]
        source_col = source_info.get('column')
        
        if source_col in self.result_df.columns:
            if source_col != output_col:
                self.result_df = self.result_df.rename(columns={source_col: output_col})

    def _apply_multiple_source_mapping(self, col_map: Dict[str, Any]) -> None:
        """Apply multiple source column mapping."""
        output_col = col_map.get('output_column')
        source_groups = col_map.get('source_groups', [])
        
        # Collect all source columns from all groups
        source_cols = []
        for group in source_groups:
            for col_info in group.get('columns', []):
                col_name = col_info.get('column')
                if col_name in self.result_df.columns:
                    source_cols.append(col_name)
        
        if source_cols:
            # Default: concatenate columns
            self.add_concatenation_column(output_col, source_cols, separator=' ')

    def _apply_calculated_mapping(self, col_map: Dict[str, Any]) -> None:
        """Apply calculated column mapping."""
        output_col = col_map.get('output_column')
        source_groups = col_map.get('source_groups', [])
        
        source_cols = []
        for group in source_groups:
            for col_info in group.get('columns', []):
                col_name = col_info.get('column')
                if col_name in self.result_df.columns:
                    source_cols.append(col_name)
        
        if not source_cols:
            return
        
        # Infer transformation type from column name
        if any(kw in output_col.lower() for kw in ['full', 'complete', 'name']):
            self.add_concatenation_column(output_col, source_cols, separator=' ')
        elif any(kw in output_col.lower() for kw in ['total', 'sum', 'amount', 'cost']):
            if len(source_cols) >= 2:
                self.add_arithmetic_column(output_col, source_cols, operation='sum')
        elif any(kw in output_col.lower() for kw in ['year', 'month', 'day', 'quarter']):
            if len(source_cols) > 0:
                date_part = output_col.lower().replace('_', '').replace('of', '')
                for part in ['year', 'month', 'day', 'quarter', 'week']:
                    if part in date_part:
                        self.add_date_part_column(output_col, source_cols[0], part=part)
                        break

    # =========================================================================
    # F) UTILITY & VALIDATION
    # =========================================================================

    def get_transformation_history(self) -> List[Dict]:
        """Get list of all transformations applied."""
        return self.transformation_history

    def reset(self) -> None:
        """Reset to original input DataFrame."""
        self.result_df = self.input_df.copy()
        self.transformation_history = []

    def get_result(self) -> pd.DataFrame:
        """Get the current result DataFrame."""
        return self.result_df

    def validate_against_output(self) -> Dict[str, Any]:
        """
        Validate current result against output DataFrame.
        
        Returns:
            Validation report dict
        """
        report = {
            "row_count_match": len(self.result_df) == len(self.output_df),
            "result_rows": len(self.result_df),
            "output_rows": len(self.output_df),
            "result_columns": len(self.result_df.columns),
            "output_columns": len(self.output_df.columns),
            "result_col_names": list(self.result_df.columns),
            "output_col_names": list(self.output_df.columns),
            "column_names_match": set(self.result_df.columns) == set(self.output_df.columns),
            "transformations_applied": len(self.transformation_history),
        }
        
        return report

    def save_to_excel(self, filepath: str, sheet_name: str = 'Sheet1') -> None:
        """Save result DataFrame to Excel file."""
        self.result_df.to_excel(filepath, sheet_name=sheet_name, index=False)

    def to_json(self, orient: str = 'records') -> str:
        """Convert result DataFrame to JSON string."""
        return self.result_df.to_json(orient=orient)

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"PowerQueryOperations("
            f"rows={len(self.result_df)}, "
            f"cols={len(self.result_df.columns)}, "
            f"transformations={len(self.transformation_history)})"
        )


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_pq_pipeline(input_df: pd.DataFrame, output_df: pd.DataFrame) -> PowerQueryOperations:
    """Create a Power Query operations pipeline."""
    return PowerQueryOperations(input_df, output_df)


def apply_transformation_sequence(input_df: pd.DataFrame, 
                                  transformations: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Apply a sequence of transformations.
    
    Args:
        input_df: Input DataFrame
        transformations: List of transformation dicts with 'type' and params
        
    Returns:
        Transformed DataFrame
        
    Example:
        >>> transformations = [
        ...     {'type': 'select', 'columns': ['Order_ID', 'Customer_ID']},
        ...     {'type': 'rename', 'rename_map': {'Order_ID': 'OrderID'}},
        ...     {'type': 'add_calculated', 'output_column': 'Full_Name', ...}
        ... ]
        >>> result = apply_transformation_sequence(input_df, transformations)
    """
    ops = PowerQueryOperations(input_df, input_df)
    
    for transform in transformations:
        transform_type = transform.get('type', '').lower()
        
        if transform_type == 'select':
            ops.select_columns(transform['columns'])
        elif transform_type == 'drop':
            ops.drop_columns(transform['columns'])
        elif transform_type == 'reorder':
            ops.reorder_columns(transform['column_order'])
        elif transform_type == 'rename':
            ops.rename_columns(transform['rename_map'])
        elif transform_type == 'concatenation':
            ops.add_concatenation_column(
                transform['output_column'],
                transform['columns'],
                transform.get('separator', '')
            )
        elif transform_type == 'arithmetic':
            ops.add_arithmetic_column(
                transform['output_column'],
                transform['columns'],
                transform.get('operation', 'sum')
            )
        elif transform_type == 'conditional':
            ops.add_conditional_column(
                transform['output_column'],
                transform['conditions'],
                transform.get('default_value')
            )
        elif transform_type == 'date_part':
            ops.add_date_part_column(
                transform['output_column'],
                transform['date_column'],
                transform.get('part', 'year')
            )
    
    return ops.get_result()


__all__ = [
    'PowerQueryOperations',
    'TransformationType',
    'ColumnMapping',
    'create_pq_pipeline',
    'apply_transformation_sequence',
]