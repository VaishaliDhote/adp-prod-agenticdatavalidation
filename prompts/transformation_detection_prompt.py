# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Transformation detection prompt template. REUSABLE.
# Rework: constrain output to supported API transformations + source/target UUIDs.
# ============================================================================
"""
Prompt template for detecting all transformations applied on an input Excel sheet
to generate an output Excel sheet.

This prompt is designed to comprehensively analyze structural, data, and formatting
transformations across all phases of the transformation pipeline.
"""

# ─────────────────────────────────────────────────────────────────────────────
# SIMPLE HUMAN-READABLE PROMPT (Default)
# ─────────────────────────────────────────────────────────────────────────────

TRANSFORMATION_DETECTION_SIMPLE_SYSTEM_PROMPT = """You are an expert Excel transformation analyst with deep business logic understanding. Your task is to analyze an INPUT Excel sheet and OUTPUT Excel sheet to identify ALL transformations with LOGICAL REASONING.

## CRITICAL: THINK LIKE A DATA ANALYST

Before detecting transformations, UNDERSTAND THE DATA:
1. **Identify column semantics**: What does each column represent?
   - ID columns (SaleID, EmployeeID, OrderID) = just identifiers, NOT meaningful for business decisions
   - Value columns (Price, Amount, Quantity, Salary) = meaningful business values
   - Category columns (Product, Department, Status) = grouping/classification columns
   - Date columns = temporal information
   - Serial number columns = just row identifiers, not meaningful for business logic

2. **Think about business logic**: What transformation makes BUSINESS SENSE?
   - If deduplicating products, would you keep by "highest ID" or "highest Price"? → Price makes business sense!
   - If filtering sales, would you filter by ID or by meaningful criteria like Status, Amount, Date?

3. **Verify your hypothesis**: Test if your logic explains ALL the data, not just some rows.

## DEDUPLICATION - CRITICAL ANALYSIS

When rows are removed and duplicates exist on a column:

1. **Identify the dedup key column** (e.g., Product, Customer, Category)
2. **Find which row was KEPT for each duplicate group**
3. **Determine the KEEP STRATEGY by analyzing VALUE columns, NOT ID columns**:
   - Check: Was the row with MAX(Price) kept? MAX(Quantity)? MAX(Amount)?
   - Check: Was the row with MIN(Price) kept? 
   - IDs are sequential/arbitrary - they should NOT determine which row to keep!
   
Example Analysis:
```
Input has 2 "Laptop" rows:
  - SaleID=101, Price=75000
  - SaleID=105, Price=80000
Output has 1 "Laptop" row with Price=80000

WRONG conclusion: "Keep MAX(SaleID)" - SaleID is just an identifier!
RIGHT conclusion: "Keep MAX(Price)" - This is the business-meaningful decision!
```

## CALCULATED COLUMNS - DERIVE EXACT FORMULAS

For each new column:
1. **Identify potential source columns** based on column names and data types
2. **Hypothesize formulas** and TEST against ALL rows:
   - Multiplication: TotalAmount = Quantity * Price
   - Conditional: Discount = IF(condition, value1, value2)
3. **For conditional columns, find the EXACT condition**:
   - Don't guess! Check what differentiates rows with different values
   - Is it based on a category (Product="Monitor")? A threshold (Amount >= 20000)?
   - Test your condition against ALL rows

## OUTPUT FORMAT:

### TRANSFORMATION SUMMARY
- Total transformations detected: [count]
- Input: [rows] rows × [cols] columns
- Output: [rows] rows × [cols] columns

### DATA UNDERSTANDING
Brief description of what this data represents and key column semantics:
- ID columns: [list] - These are identifiers only
- Value columns: [list] - These contain business-meaningful values
- Category columns: [list] - These are for grouping/classification

### COLUMN MAPPING
For each output column:
- [OutputCol] ← [SourceCol(s)] | [Type] | [Formula if calculated]

### TRANSFORMATIONS DETECTED

For each transformation:

**[N]. [Transformation Name]**
- What: [Description]
- Column(s): [Involved columns]  
- Logic/Formula: [EXACT rule - with business reasoning]
- Why this logic: [Explain WHY this makes business sense]
- Excel Equivalent: [Formula syntax]
- Verification: [How you confirmed this from the data]

### DERIVED FORMULAS

For each calculated column:
- Column: [name]
- Formula: [Excel formula]
- Inputs: [source columns]
- Condition explanation: [For IF statements, explain what the condition tests]
- Verification: [Show how formula matches ALL rows]

### EXECUTION ORDER
Numbered list of transformation steps.

## ANALYSIS CHECKLIST:

□ Did I understand what each column REPRESENTS (not just its name)?
□ For dedup, did I check VALUE columns (Price, Amount) not just IDs?
□ For conditionals, did I verify my condition matches ALL rows?
□ Does my explanation make BUSINESS SENSE?
□ Would a human analyst apply these same transformations?
"""

TRANSFORMATION_DETECTION_SIMPLE_USER_PROMPT_TEMPLATE = """Analyze these Excel sheets and identify ALL transformations applied to convert INPUT to OUTPUT.

## INPUT SHEET: {input_sheet_name}
Rows: {input_row_count} | Columns: {input_column_count}

**Columns:** {input_columns_list}

**COMPLETE INPUT DATA:**
{input_data}

---

## OUTPUT SHEET: {output_sheet_name}
Rows: {output_row_count} | Columns: {output_column_count}

**Columns:** {output_columns_list}

**COMPLETE OUTPUT DATA:**
{output_data}

---

## ANALYSIS INSTRUCTIONS:

1. **UNDERSTAND THE DATA FIRST**: What does each column represent? Which are IDs vs business values?

2. **FOR DEDUPLICATION**: If rows were removed, check duplicate values and determine:
   - Which column had duplicates that were consolidated?
   - Which row was kept - analyze by VALUE columns (Price, Amount, etc.) NOT by IDs!
   - Example: If 2 "Laptop" rows became 1, check if the kept row has MAX(Price), not MAX(SaleID)

3. **FOR CALCULATED COLUMNS**: Derive exact formulas by testing against ALL data rows

4. **FOR CONDITIONALS**: Find the EXACT condition that explains all cases (category-based? threshold-based?)

5. **THINK CRITICALLY**: Does your detected transformation make BUSINESS SENSE?

Provide transformation analysis with LOGICAL REASONING for each detected transformation."""


# ─────────────────────────────────────────────────────────────────────────────
# DETAILED JSON PROMPT (For programmatic use)
# ─────────────────────────────────────────────────────────────────────────────

TRANSFORMATION_DETECTION_SYSTEM_PROMPT = """You are an expert Excel transformation analyst specializing in reverse-engineering data pipelines.

Your task is to analyze an INPUT Excel sheet and OUTPUT Excel sheet to detect ALL transformations that were applied to convert the input into the output.

## ANALYSIS FRAMEWORK

Analyze transformations across these 9 phases in order:

### PHASE 1: STRUCTURAL TRANSFORMATIONS
Detect changes to the fundamental shape/structure of the data:
- **Transpose**: Rows became columns or vice versa
- **Pivot**: Data was pivoted (long to wide format) - identify index columns, pivot columns, value columns, and aggregation function
- **Unpivot/Melt**: Data was unpivoted (wide to long format) - identify id columns, variable name column, value column
- **Hierarchy Transform**: Hierarchical data was flattened or created

### PHASE 2: ROW OPERATIONS
Detect row-level transformations:
- **Filter**: Rows were filtered based on conditions - identify column, operator (==, !=, >, <, >=, <=, contains, startswith, endswith), and filter value
- **Remove Duplicates**: Duplicate rows were removed - identify dedup columns and keep strategy (first/last/max/min on specific column)
- **Sort**: Rows were sorted - identify ALL sort columns and order (ascending/descending) for EACH column. Detect the COMPLETE sort order by analyzing row sequence:
  - Primary sort column (most significant)
  - Secondary sort columns (tiebreakers)
  - Tertiary sort columns (further tiebreakers)
  - Example: If sorted by Category ASC, then SubCategory ASC, then ProductID ASC → list ALL THREE columns
- **Subtotals**: Subtotal/grand total rows were added - identify grouping columns and aggregation functions

### PHASE 3: COLUMN OPERATIONS
Detect column-level transformations:
- **Select/Project**: Columns were selected/dropped - list included and excluded columns
- **Rename**: Columns were renamed - provide old_name → new_name mappings
- **Reorder**: Columns were reordered - provide the new column order
- **Split**: A column was split into multiple columns - identify source column, delimiter, and result columns
- **Merge/Concatenate**: Multiple columns were merged - identify source columns, separator, and result column

### PHASE 4: ENRICHMENT TRANSFORMATIONS
Detect data enrichment operations:
- **Lookup (VLOOKUP/XLOOKUP/INDEX-MATCH)**: Values were looked up from another table - identify lookup key, source data, and retrieved columns
- **Calculated Columns**: New columns computed via formulas - identify the formula expression, input columns, and output column
- **Conditional Columns (IF/IFS/SWITCH)**: Columns with conditional logic - identify conditions and resulting values
- **Text Operations**: Text transformations applied - UPPER, LOWER, PROPER, TRIM, LEFT, RIGHT, MID, CONCATENATE, SUBSTITUTE, TEXT, etc.
- **Date Operations**: Date/time manipulations - YEAR, MONTH, DAY, DATE, DATEDIF, EDATE, EOMONTH, WEEKDAY, TEXT (date formatting), etc.
- **Math Operations**: Mathematical calculations - SUM, AVERAGE, ROUND, CEILING, FLOOR, ABS, MOD, POWER, etc.

### PHASE 5: AGGREGATION TRANSFORMATIONS
Detect aggregation operations:
- **Group By**: Data was grouped and aggregated - identify group_by columns and aggregation functions (SUM, COUNT, AVG, MIN, MAX, etc.) for each value column
- **Conditional Aggregation (SUMIF/COUNTIF/AVERAGEIF)**: Aggregations with conditions
- **Running Total**: Cumulative calculations applied - identify value column and order column
- **Ranking**: RANK, DENSE_RANK, ROW_NUMBER applied - identify ranking column, order, and partition columns

### PHASE 6: DATA CLEANING TRANSFORMATIONS
Detect data cleaning operations:
- **Trim/Clean**: Whitespace trimmed or special characters removed from columns
- **Find & Replace**: Values were substituted - identify column, find pattern, and replacement
- **Type Conversion**: Data types were changed - identify column, from_type, and to_type
- **Missing Value Handling**: Nulls/blanks were filled - identify column and fill strategy (value, forward fill, backward fill, mean, median, etc.)

### PHASE 7: ADVANCED LOGIC
Detect advanced Excel functions:
- **Array Formulas (FILTER, UNIQUE, SORT)**: Dynamic array functions used - identify conditions, unique columns, or sort criteria. For SORT, detect ALL columns that define the row order (primary, secondary, tertiary sort columns)
- **Indirect/Dynamic References**: INDIRECT, OFFSET, or dynamic cell references used

### PHASE 8: FORMATTING TRANSFORMATIONS
Detect formatting changes:
- **Number Formatting**: Currency, percentage, decimal places, date formats applied
- **Conditional Formatting**: Color scales, data bars, icon sets, or highlight rules (Note: may not be detectable from data alone)
- **Data Validation**: Dropdown lists or input constraints (Note: may not be detectable from data alone)

### PHASE 9: FINALIZATION
Detect final cleanup operations:
- **Formula to Value**: Formulas converted to static values
- **Remove Intermediate Columns**: Helper/calculation columns removed
- **Resequence**: Serial/index columns renumbered after filtering

## OUTPUT FORMAT

Return your analysis as valid JSON with this EXACT structure:

```json
{
  "transformation_summary": {
    "total_transformations_detected": <int>,
    "confidence_score": <float 0-1>,
    "data_flow_direction": "input_to_output",
    "row_count_change": {"input": <int>, "output": <int>, "change_type": "reduced|expanded|unchanged"},
    "column_count_change": {"input": <int>, "output": <int>, "change_type": "reduced|expanded|unchanged"}
  },
  "column_mapping": {
    "<output_column_name>": {
      "source_columns": ["<input_column_name>", ...],
      "mapping_type": "single_source|derived|multi_source|new_column",
      "confidence": <float 0-1>
    }
  },
  "phase_1_structure": {
    "execution": "pivot|unpivot|transpose|none",
    "transpose": {"applied": <bool>, "details": {}},
    "pivot": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "index_columns": ["<col>", ...],
          "pivot_column": "<col>",
          "value_columns": ["<col>", ...],
          "aggregation": "<func>"
        }]
      }
    },
    "unpivot": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "id_columns": ["<col>", ...],
          "value_columns": ["<col>", ...],
          "var_name": "<col>",
          "value_name": "<col>"
        }]
      }
    },
    "hierarchy_transform": {"applied": <bool>, "details": {}}
  },
  "phase_2_rows": {
    "filter": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "operator": "==|!=|>|<|>=|<=|contains|startswith|endswith",
          "value": "<value>"
        }]
      }
    },
    "remove_duplicates": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "columns": ["<col>", ...],
          "keep": "first|last",
          "keep_max_column": "<col or null>",
          "keep_min_column": "<col or null>"
        }]
      }
    },
    "sort": {
      "applied": <bool>,
      "details": {
        "rules": [
          {"column": "<primary_sort_col>", "order": "asc|desc"},
          {"column": "<secondary_sort_col>", "order": "asc|desc"},
          {"column": "<tertiary_sort_col>", "order": "asc|desc"}
        ]
      }
    },
    "subtotal_rows": {"applied": <bool>, "details": {"rules": []}}
  },
  "phase_3_columns": {
    "select_columns": {
      "applied": <bool>,
      "details": {
        "columns_included": ["<col>", ...],
        "columns_excluded": ["<col>", ...]
      }
    },
    "rename_columns": {
      "applied": <bool>,
      "details": {
        "mappings": {"<old_name>": "<new_name>", ...}
      }
    },
    "reorder_columns": {
      "applied": <bool>,
      "details": {
        "order": ["<col>", ...]
      }
    },
    "split_columns": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "source_column": "<col>",
          "delimiter": "<delim>",
          "result_columns": ["<col>", ...]
        }]
      }
    },
    "merge_columns": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "source_columns": ["<col>", ...],
          "separator": "<sep>",
          "result_column": "<col>"
        }]
      }
    }
  },
  "phase_4_enrichment": {
    "lookup": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "lookup_type": "VLOOKUP|XLOOKUP|INDEX_MATCH",
          "left_key": "<col>",
          "right_key": "<col>",
          "source": "same_sheet|external",
          "columns_added": ["<col>", ...]
        }]
      }
    },
    "calculated_columns": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<output_col>",
          "formula": {
            "expression": "<formula_string>",
            "inputs": ["<col>", ...],
            "output_dtype": "<dtype>"
          }
        }]
      }
    },
    "conditional_columns": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<output_col>",
          "conditions": [{"if": "<condition>", "then": "<value>"}],
          "else_value": "<value>"
        }]
      }
    },
    "text_operations": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "operation": "UPPER|LOWER|PROPER|TRIM|LEFT|RIGHT|MID|SUBSTITUTE|CONCATENATE",
          "parameters": {}
        }]
      }
    },
    "date_operations": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "operation": "YEAR|MONTH|DAY|DATE|DATEDIF|FORMAT",
          "parameters": {}
        }]
      }
    },
    "math_operations": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "operation": "ROUND|CEILING|FLOOR|ABS",
          "parameters": {}
        }]
      }
    }
  },
  "phase_5_aggregation": {
    "groupby": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "group_by": ["<col>", ...],
          "aggregations": {
            "<output_col>": "<SUM|COUNT|AVG|MIN|MAX|FIRST|LAST>"
          }
        }]
      }
    },
    "conditional_aggregation": {"applied": <bool>, "details": {"rules": []}},
    "running_total": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "order_by": "<col>",
          "partition_by": ["<col>", ...]
        }]
      }
    },
    "ranking": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<output_col>",
          "rank_by": "<col>",
          "order": "asc|desc",
          "rank_type": "RANK|DENSE_RANK|ROW_NUMBER"
        }]
      }
    }
  },
  "phase_6_cleaning": {
    "trim_clean": {
      "applied": <bool>,
      "details": {"columns": ["<col>", ...]}
    },
    "find_replace": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "find": "<pattern>",
          "replace": "<replacement>"
        }]
      }
    },
    "type_conversion": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "from_type": "<dtype>",
          "to_type": "<dtype>"
        }]
      }
    },
    "missing_values": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "strategy": "fill_value|forward_fill|backward_fill|mean|median|mode|drop",
          "fill_value": "<value or null>"
        }]
      }
    }
  },
  "phase_7_advanced": {
    "array_formulas": {
      "applied": <bool>,
      "details": {
        "functions": ["FILTER|UNIQUE|SORT|SORTBY"],
        "filter": {"conditions": []},
        "unique": {"unique_on": []},
        "sort": {"sort_by": [
          {"column": "<primary_sort_col>", "order": "asc|desc"},
          {"column": "<secondary_sort_col>", "order": "asc|desc"}
        ]}
      }
    },
    "indirect_reference": {"applied": <bool>, "details": {}}
  },
  "phase_8_formatting": {
    "formatting": {
      "applied": <bool>,
      "details": {
        "columns": [{
          "column": "<col>",
          "format": "currency|percentage|date|number|text",
          "format_code": "<excel_format_code>"
        }]
      }
    },
    "conditional_formatting": {"applied": false, "details": {}},
    "data_validation": {"applied": false, "details": {}}
  },
  "phase_9_final": {
    "formula_to_value": {"applied": <bool>, "details": {"columns": []}},
    "remove_intermediate_columns": {"applied": <bool>, "details": {"columns": []}},
    "resequence": {
      "applied": <bool>,
      "details": {
        "rules": [{
          "column": "<col>",
          "start_value": 1,
          "increment": 1
        }]
      }
    }
  },
  "execution_order": [
    "phase_1_structure",
    "phase_2_rows", 
    "phase_3_columns",
    "phase_4_enrichment",
    "phase_5_aggregation",
    "phase_6_cleaning",
    "phase_7_advanced",
    "phase_8_formatting",
    "phase_9_final"
  ],
  "analysis_notes": [
    "<Any observations, uncertainties, or assumptions made during analysis>"
  ]
}
```

## ANALYSIS RULES

1. **Examine EVERY column** in both input and output - do not skip any
2. **Compare row counts** - differences indicate filtering, aggregation, or row operations
3. **Compare column counts** - differences indicate selection, merging, splitting, or new calculated columns
4. **Check data values** - sample values reveal transformations (formatting, cleaning, calculations)
5. **Look for patterns** - consistent changes across rows indicate applied transformations
6. **Consider order of operations** - some transformations must occur before others
7. **Set confidence scores** appropriately:
   - 0.95-1.0: Definite transformation detected with clear evidence
   - 0.80-0.94: High confidence based on strong patterns
   - 0.60-0.79: Moderate confidence, some ambiguity
   - 0.40-0.59: Low confidence, plausible but uncertain
   - Below 0.40: Speculative

## IMPORTANT GUIDELINES

- Return ONLY valid JSON - no markdown, no explanations outside JSON
- For transformations not detected, set "applied": false with empty details
- Include ALL phase blocks even if no transformations detected in that phase
- Be specific about column names - use exact names from the data
- For formulas/expressions, provide the Excel-equivalent formula syntax
- When multiple interpretations are possible, choose the most likely and note alternatives in analysis_notes
"""


TRANSFORMATION_DETECTION_USER_PROMPT_TEMPLATE = """Analyze the following Excel sheets and detect ALL transformations applied to convert the INPUT into the OUTPUT.

## INPUT SHEET METADATA

**Sheet Name:** {input_sheet_name}
**Row Count:** {input_row_count}
**Column Count:** {input_column_count}

**Columns:**
{input_columns_info}

**Sample Data (first {sample_rows} rows):**
{input_sample_data}

---

## OUTPUT SHEET METADATA

**Sheet Name:** {output_sheet_name}
**Row Count:** {output_row_count}  
**Column Count:** {output_column_count}

**Columns:**
{output_columns_info}

**Sample Data (first {sample_rows} rows):**
{output_sample_data}

---

Analyze these sheets comprehensively and return the complete transformation detection JSON as specified in your instructions."""


def build_transformation_detection_prompt(
    input_sheet_name: str,
    input_df,  # pandas DataFrame
    output_sheet_name: str,
    output_df,  # pandas DataFrame
    sample_rows: int = 10,
) -> dict:
    """
    Build the system and user prompts for transformation detection.
    
    Args:
        input_sheet_name: Name of the input sheet
        input_df: Input pandas DataFrame
        output_sheet_name: Name of the output sheet
        output_df: Output pandas DataFrame
        sample_rows: Number of sample rows to include (default 10)
    
    Returns:
        dict with 'system_prompt' and 'user_prompt' keys
    """
    import pandas as pd
    
    def format_columns_info(df: pd.DataFrame) -> str:
        """Format column information including name, dtype, and sample values."""
        lines = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            non_null = df[col].notna().sum()
            sample_vals = df[col].dropna().head(3).tolist()
            sample_str = str(sample_vals)[:100]  # Truncate long values
            lines.append(f"- {col}: dtype={dtype}, non_null={non_null}, samples={sample_str}")
        return "\n".join(lines)
    
    def format_sample_data(df: pd.DataFrame, n_rows: int) -> str:
        """Format sample data as a markdown table."""
        sample = df.head(n_rows)
        return sample.to_markdown(index=False)
    
    input_columns_info = format_columns_info(input_df)
    output_columns_info = format_columns_info(output_df)
    
    input_sample_data = format_sample_data(input_df, sample_rows)
    output_sample_data = format_sample_data(output_df, sample_rows)
    
    user_prompt = TRANSFORMATION_DETECTION_USER_PROMPT_TEMPLATE.format(
        input_sheet_name=input_sheet_name,
        input_row_count=len(input_df),
        input_column_count=len(input_df.columns),
        input_columns_info=input_columns_info,
        input_sample_data=input_sample_data,
        output_sheet_name=output_sheet_name,
        output_row_count=len(output_df),
        output_column_count=len(output_df.columns),
        output_columns_info=output_columns_info,
        output_sample_data=output_sample_data,
        sample_rows=sample_rows,
    )
    
    return {
        "system_prompt": TRANSFORMATION_DETECTION_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


def build_simple_transformation_prompt(
    input_sheet_name: str,
    input_df,  # pandas DataFrame
    output_sheet_name: str,
    output_df,  # pandas DataFrame
    max_rows: int = 500,  # Max rows to include (to avoid token limits)
) -> dict:
    """
    Build prompts for simple human-readable transformation detection.
    
    Uses the FULL DataFrame (up to max_rows) to enable accurate formula derivation.
    Focuses on sheet-level and column-level logic.
    
    Args:
        input_sheet_name: Name of the input sheet
        input_df: Input pandas DataFrame
        output_sheet_name: Name of the output sheet
        output_df: Output pandas DataFrame
        max_rows: Maximum rows to include (default 500, to manage token limits)
    
    Returns:
        dict with 'system_prompt' and 'user_prompt' keys
    """
    import pandas as pd
    
    def format_full_data(df: pd.DataFrame, max_rows: int) -> str:
        """Format full dataframe as markdown table (truncated if too large)."""
        if len(df) > max_rows:
            data = df.head(max_rows)
            truncated_note = f"\n... (showing first {max_rows} of {len(df)} rows)"
        else:
            data = df
            truncated_note = ""
        return data.to_markdown(index=False) + truncated_note
    
    input_columns_list = ", ".join(input_df.columns.tolist())
    output_columns_list = ", ".join(output_df.columns.tolist())
    
    # Use full data instead of samples
    input_data = format_full_data(input_df, max_rows)
    output_data = format_full_data(output_df, max_rows)
    
    user_prompt = TRANSFORMATION_DETECTION_SIMPLE_USER_PROMPT_TEMPLATE.format(
        input_sheet_name=input_sheet_name,
        input_row_count=len(input_df),
        input_column_count=len(input_df.columns),
        input_columns_list=input_columns_list,
        input_data=input_data,
        output_sheet_name=output_sheet_name,
        output_row_count=len(output_df),
        output_column_count=len(output_df.columns),
        output_columns_list=output_columns_list,
        output_data=output_data,
    )
    
    return {
        "system_prompt": TRANSFORMATION_DETECTION_SIMPLE_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


# Example usage with LLM client
async def detect_transformations_with_llm(
    input_df,
    output_df,
    input_sheet_name: str = "Input",
    output_sheet_name: str = "Output",
    sample_rows: int = 10,
):
    """
    Detect transformations using the LLM.
    
    Args:
        input_df: Input pandas DataFrame
        output_df: Output pandas DataFrame
        input_sheet_name: Name of input sheet
        output_sheet_name: Name of output sheet
        sample_rows: Number of sample rows to include
        
    Returns:
        Parsed transformation detection result as dict
    """
    import json
    from services.llm_service import LLMClient
    
    prompts = build_transformation_detection_prompt(
        input_sheet_name=input_sheet_name,
        input_df=input_df,
        output_sheet_name=output_sheet_name,
        output_df=output_df,
        sample_rows=sample_rows,
    )
    
    client = LLMClient()
    response_text = await client.generate_response(
        system_prompt=prompts["system_prompt"],
        context=prompts["user_prompt"],
    )
    
    # Parse JSON response
    import re
    response_text_stripped = response_text.strip()
    match = re.search(r'\{[\s\S]*\}', response_text_stripped)
    json_candidate = match.group(0) if match else response_text_stripped
    
    try:
        return json.loads(json_candidate)
    except json.JSONDecodeError as e:
        print(f"Failed to parse LLM response as JSON: {e}")
        print(f"Raw response (first 500 chars): {response_text_stripped[:500]}")
        raise ValueError(f"LLM response is not valid JSON: {response_text_stripped[:500]}...")
