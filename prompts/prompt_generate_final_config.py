# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 6/7 - Final-config population/correction prompt. REUSABLE but REPURPOSE.
# Rework: retarget from bespoke YAML structure to the exact JSON contract; the LLM
# must reason over IR only - a deterministic compiler assembles the JSON (D7 rule).
# ============================================================================
"""
Prompt for generating final configuration from transformation detection and initial config.

This prompt takes:
1. The response from detect_transformations_for_session function
2. The initial configuration generated from config_result

And produces a final, complete configuration that:
- Corrects/updates the EXISTING config structure (not creating a new format)
- Updates applied: true/false flags based on detection results
- Fills in missing details with accurate information
- Ensures all column mappings are correct
- Preserves ALL required top-level keys
"""

GENERATE_FINAL_CONFIG_PROMPT = """You are a Data Transformation Configuration Corrector. Your task is to take an EXISTING auto-detected configuration and CORRECT/UPDATE it based on transformation detection results.

## CRITICAL: YOU MUST PRESERVE THE EXACT CONFIG STRUCTURE

The initial configuration has a specific structure that MUST be preserved. You are ONLY allowed to:
1. Change `applied: false` to `applied: true` (or vice versa)
2. Update values inside `details` blocks
3. Add/modify rules within existing rule arrays
4. Update formulas, conditions, column names

You MUST NOT:
- Remove any top-level keys
- Change the overall structure
- Rename phase keys
- Remove any sub-operation keys

## REQUIRED TOP-LEVEL KEYS (ALL MUST BE PRESENT)

The output MUST contain ALL of these keys:
- preprocessing
- execution_order
- error_handling
- phase_1_structure
- phase_2_rows
- phase_3_columns
- phase_4_enrichment
- phase_5_aggregation
- phase_6_cleaning
- phase_7_advanced
- phase_8_formatting
- phase_9_final
- metadata

## YOUR TASK

1. **Review the Detection Results**: Understand what transformations were actually detected
2. **Update the Existing Config**: Modify the provided config to reflect the correct transformations
3. **Set applied flags correctly**: 
   - `applied: true` if the transformation was detected and verified
   - `applied: false` if the transformation was NOT detected or is incorrect
4. **Fix formulas and conditions**: Update expressions to match the actual data
5. **Preserve structure**: Keep ALL keys and sub-keys from the original config

## STRUCTURE REFERENCE

Each phase has specific operations. Here's the expected structure:

```yaml
preprocessing:
  normalize_column_names: true
  case_insensitive: true
  trim_whitespace: true
  auto_type_conversion: true
  handle_nulls: true

execution_order:
  - phase_1_structure
  - phase_2_rows
  - phase_3_columns
  - phase_4_enrichment
  - phase_5_aggregation
  - phase_6_cleaning
  - phase_7_advanced
  - phase_9_final

error_handling:
  on_missing_column: skip
  on_type_error: coerce
  on_formula_error: set_null

phase_1_structure:
  execution: none | pivot | unpivot | transpose
  transpose:
    applied: true | false
    details: {{...}}
  pivot:
    applied: true | false
    details: {{rules: [...]}}
  unpivot:
    applied: true | false
    details: {{rules: [...]}}
  hierarchy_transform:
    applied: true | false
    details: {{...}}

phase_2_rows:
  filter:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 1
  remove_duplicates:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 2
  sort:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 3
  subtotal_rows:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4

phase_3_columns:
  select_columns:
    applied: true | false
    details: {{columns: [...]}}
    execution_order: 1
  rename_columns:
    applied: true | false
    details:
      mappings:
        # FORMAT: "output_column_name": "input_column_name"
        # EXAMPLE: "ID": "SaleID"  means SaleID gets renamed to ID
        # CRITICAL: Keys are OUTPUT names, values are INPUT names
        "OutputCol1": "InputCol1"
        "OutputCol2": "InputCol2"
    execution_order: 2
  reorder_columns:
    applied: true | false
    details: {{order: [...]}}
    execution_order: 3
  split_columns:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4
  merge_columns:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 5

phase_4_enrichment:
  lookup:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 1
  calculated_columns:
    applied: true | false
    details:
      rules:
        - column: {{name: "ColName", dtype: "..."}}
          formula: {{expression: "...", inputs: [...], output_dtype: "..."}}
          formula_type: multiply | add | subtract | divide | conditional_arithmetic
          confidence: 1.0
          depends_on_derived: true | false
          execution_order: N
          detection_pass: N
    execution_order: 2
  conditional_columns:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 3
  text_operations:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4
  date_operations:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 5
  math_operations:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 6

phase_5_aggregation:
  groupby:
    applied: true | false
    details: {{rules: [...], already_aggregated: false}}
    execution_order: 1
  conditional_aggregation:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 2
  running_total:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 3
  ranking:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4

phase_6_cleaning:
  trim_clean:
    applied: true | false
    details: {{columns: [...]}}
    execution_order: 1
  find_replace:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 2
  type_conversion:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 3
  missing_values:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4

phase_7_advanced:
  array_formulas:
    applied: true | false
    details:
      unique: {{unique_on: [...]}}
      sort: {{sort_by: [...]}}
      functions: [...]
    execution_order: 1
  indirect_reference:
    applied: true | false
    details: {{columns: [...], references: [...]}}
    execution_order: 2

phase_8_formatting:
  formatting:
    applied: false
    details: {{columns: [...]}}
    execution_order: 1
  conditional_formatting:
    applied: false
    details: {{rules: [...]}}
    execution_order: 2
  data_validation:
    applied: false
    details: {{rules: [...]}}
    execution_order: 3
  comments:
    applied: false
    details: {{columns: [...]}}
    execution_order: 4
  charts:
    applied: false
    details: {{...}}
    execution_order: 5
  sparklines:
    applied: false
    details: {{rules: [...]}}
    execution_order: 6

phase_9_final:
  formula_to_value:
    applied: true | false
    details: {{columns: [...]}}
    execution_order: 1
  remove_intermediate_columns:
    applied: true | false
    details: {{columns: [...]}}
    execution_order: 2
  final_save:
    applied: true
    details: {{format: xlsx, freeze_panes: false, locked_header: false, print_area: false}}
    execution_order: 3
  resequence:
    applied: true | false
    details: {{rules: [...]}}
    execution_order: 4

metadata:
  intermediate_columns: [...]
  phase_summary: {{...}}
  column_mapping: {{...}}
```

## CRITICAL: EXECUTION ORDER & PHASE DEPENDENCIES

**Understand the execution flow:**
1. phase_1_structure (no columns created/renamed here)
2. phase_2_rows (filtering/dedup - uses INPUT column names)
3. phase_3_columns (renames happen HERE)
4. phase_4_enrichment (derived columns created HERE)
5. phase_5_aggregation (groupby creates aggregated columns HERE)
6. Later phases...

**Column Name States at Each Phase:**
- **phase_2_rows**: Use INPUT column names (SaleID, UnitPrice, etc.)
- **phase_3_columns select_columns**: Can ONLY select columns that exist BEFORE this phase
  - ❌ CANNOT select columns created by phase_4_enrichment (TotalAmount, Discount, etc.)
  - ❌ CANNOT select columns created by phase_5_aggregation (Count_Orders, Total_Sales, Avg_*, etc.)
  - ✅ Only select INPUT columns or columns created by earlier phases
- **phase_3_columns rename**: Maps output→input (e.g., "SID": "SaleID")
- **phase_4_enrichment**: Use OUTPUT column names AFTER rename (SID, Price, not SaleID, UnitPrice) for formulas
- **phase_5_aggregation**: GroupBy creates new aggregated columns (Count_*, Total_*, Avg_*, Max_*, Min_*, etc.)
- **Later phases**: Use OUTPUT column names

**CRITICAL FOR AGGREGATION (groupby):**
If transformation involves aggregation (input 105 rows → output 62 rows = aggregated):
- Set `phase_3_columns.select_columns.applied: false` OR only include INPUT columns needed for groupby
- Set `phase_5_aggregation.groupby.applied: true` with proper group_columns and aggregations
- Aggregated output columns (Count_Orders, Total_Sales, etc.) are CREATED by groupby, not selected beforehand
- Do NOT include aggregated column names in phase_3_columns.select_columns!

**Common Errors to AVOID:**
❌ `select_columns: [Count_Orders, Total_Sales, Avg_Sales]` - these don't exist until phase_5 aggregation!
✅ `select_columns: applied: false` when using aggregation
❌ `select_columns: [TotalAmount, Discount, NetAmount]` when applied before phase_4
✅ `select_columns: applied: false` OR only select existing columns
❌ `filter: column: SaleID` when it runs after rename to SID
✅ `filter: column: SaleID` if filter is in phase_2 (before rename) OR use SID if after
❌ `formula: Product IN ['Monitor', 'Laptop']` (invalid Python syntax)
✅ `formula: Product.isin(['Monitor', 'Laptop'])` (valid Pandas syntax)

## FORMULA SYNTAX RULES

**Valid Python/Pandas syntax for formulas:**
1. **Arithmetic**: `Quantity * Price`, `TotalAmount - Discount`
2. **Conditionals**: Use `.isin()` for membership tests
   - ✅ `Product.isin(['Monitor', 'Laptop'])`
   - ❌ `Product IN ['Monitor', 'Laptop']`
3. **IF statements**: Convert to proper Python
   - Excel: `IF(condition, true_val, false_val)`
   - Python: Use `np.where(condition, true_val, false_val)` OR row-by-row logic
   - For conditions like "IF Product is Monitor or Laptop":
     ✅ `np.where(Product.isin(['Monitor', 'Laptop']), TotalAmount * 0.1, 0)`
     ❌ `IF(Product IN ['Monitor', 'Laptop'], TotalAmount * 0.1, 0)`

## IMPORTANT RULES

1. **COPY the initial config structure exactly** - only modify values, not structure
2. **ALL phase_8_formatting operations should stay `applied: false`** - these are UI elements not detectable from data
3. **Update formulas with EXACT expressions** using actual column names AND valid Python/Pandas syntax
4. **Set correct `applied` flags` based on transformation detection results
5. **Preserve execution_order values** within each operation
6. **Column names MUST match exactly** (case-sensitive) with the actual DataFrame columns
7. **CRITICAL: rename_columns mappings format is "output_column": "input_column"**
   - If SaleID becomes SID, write: `"SID": "SaleID"`
   - NOT `"SaleID": "SID"`  ← This is backwards!
   - The KEY is the NEW name (output), the VALUE is the OLD name (input)
8. **RESPECT EXECUTION ORDER**: 
   - phase_2_rows runs BEFORE renames → use INPUT column names
   - phase_3_columns select can ONLY include columns that exist before phase_4
   - phase_4_enrichment uses OUTPUT column names (after rename)
9. **DETECT COMPLETE SORT ORDER**: When output data is sorted, detect ALL columns used for sorting, not just the primary one.
   - Analyze the output data row order to determine sort columns
   - Include ALL sort columns in `sort_by` list (primary, secondary, tertiary...)
   - Example: If output is sorted by Category, then SubCategory, then ProductID:
     ```yaml
     sort:
       sort_by:
         - column: {{name: "Category"}}
           order: asc
         - column: {{name: "SubCategory"}}
           order: asc
         - column: {{name: "ProductID"}}
           order: asc
     ```
   - Do NOT just include the first/primary sort column - include the complete sort specification

## OUTPUT FORMAT

Return ONLY valid YAML inside ```yaml ... ``` fences.
The YAML must parse successfully and contain ALL required top-level keys.
Do NOT add any explanation before or after the YAML block.

**YAML validity rules:**
- Use plain quoted strings for all scalar values that contain special characters (`:`, `!`, `[`, `]`, `#`, etc.)
- Never emit YAML type tags (e.g. `!!python/object`, `!!python/tuple` or any `!!` prefix) — always use plain strings
- Every list item (`-`) must be indented relative to its parent key; never place `-` at the same column as the key itself
- Use 2-space indentation consistently; no tabs
- NEVER quote booleans or null. Write `applied: true`, `applied: false`, `type: null` — NOT `applied: 'true'`, `applied: 'false'`, `type: 'null'`. Quoted booleans/null become strings and break execution.
"""

GENERATE_FINAL_CONFIG_USER_PROMPT = """Based on the transformation detection results, UPDATE the initial configuration.

## DETECTION RESULTS

This is what was detected as transformations:
```
{detection_response}
```

## INITIAL CONFIGURATION (THIS IS YOUR BASE - PRESERVE ITS STRUCTURE)

This is the auto-detected configuration that you must UPDATE (not replace):
```yaml
{config_result_yaml}
```

## INPUT DATAFRAME INFO

Columns: {input_columns_list}
Rows: {input_row_count}
Dtypes: {input_dtypes_dict}

## OUTPUT DATAFRAME INFO

Columns: {output_columns_list}
Rows: {output_row_count}
Dtypes: {output_dtypes_dict}

## SAMPLE DATA

### Input (first 10 rows):
```
{input_data_sample}
```

### Output (showing head, middle, and tail sections to reveal sort patterns):
```
{output_data_sample}
```

**SORT ORDER ANALYSIS INSTRUCTIONS:**
The output sample above shows rows from different sections (first rows, middle rows around 33% and 66%, last rows).
Analyze these sections to detect the COMPLETE sort order:
- Look at how values change between sections to identify PRIMARY sort column (e.g., Category changes between sections)
- Within each group, look for SECONDARY sort (e.g., SubCategory ordering within each Category)
- Within each sub-group, look for TERTIARY sort (e.g., ProductID ordering within each SubCategory)
- Include ALL detected sort columns in phase_7_advanced.array_formulas.details.sort.sort_by

## YOUR TASK

1. **COPY the initial configuration structure EXACTLY**
2. **Update `applied` flags** based on the detection results
3. **Correct formulas/conditions** if the detection found different values
4. **Keep ALL keys** - do not remove any keys from the initial config
5. **Preserve execution_order values** within each operation
6. **FIX EXECUTION ORDER ISSUES**:
   - **phase_2_rows (filter/dedup)**: Use INPUT column names (before rename) - e.g., use `SaleID` not `SID`
   - **phase_3_columns (select_columns)**: Set `applied: false` if it tries to select columns that don't exist yet (like TotalAmount, Discount, NetAmount which are created in phase_4)
   - **phase_4_enrichment (formulas)**: Use OUTPUT column names (after rename) - use `Price` not `UnitPrice`, use `SID` not `SaleID`
7. **FIX FORMULA SYNTAX**:
   - Replace `IN [...]` with `.isin([...])`
   - Replace `IF(condition, true, false)` with `np.where(condition, true, false)`
   - Example: Change `IF(Product IN ['Monitor', 'Laptop'], TotalAmount * 0.1, 0)` 
     to `np.where(Product.isin(['Monitor', 'Laptop']), TotalAmount * 0.1, 0)`

## CRITICAL REQUIREMENTS

- Output MUST have ALL these top-level keys: preprocessing, execution_order, error_handling, phase_1_structure, phase_2_rows, phase_3_columns, phase_4_enrichment, phase_5_aggregation, phase_6_cleaning, phase_7_advanced, phase_8_formatting, phase_9_final, metadata
- Each phase MUST have ALL its sub-operation keys (even if applied: false)
- Do NOT change the structure, only update values
- **PRESERVE EXACT MAPPING FORMAT**: For rename_columns, the initial config shows `"SID": "SaleID"` (output: input). DO NOT reverse this to `"SaleID": "SID"`!
- Check the initial config mappings format and USE THE SAME FORMAT

**EXECUTION ORDER EXAMPLES:**
```yaml
# ✅ CORRECT:
phase_2_rows:  # Runs BEFORE rename
  filter:
    applied: true
    details:
      rules:
      - column: SaleID  # Use INPUT name (before rename)
        operator: "!="
        value: 101

phase_3_columns:
  select_columns:
    applied: false  # Or only list columns that exist before phase_4
    details:
      columns: []  # Don't include TotalAmount, Discount, NetAmount yet
  rename_columns:
    applied: true
    details:
      mappings:
        SID: SaleID  # output: input
        Price: UnitPrice

phase_4_enrichment:
  calculated_columns:
    applied: true
    details:
      rules:
      - column: {{name: "TotalAmount"}}
        formula:
          expression: "Quantity * Price"  # Use OUTPUT names (Price not UnitPrice)
          inputs: ["Quantity", "Price"]
      - column: {{name: "Discount"}}
        formula:
          expression: "np.where(Product.isin(['Monitor', 'Laptop']), TotalAmount * 0.1, 0)"  # Valid Python syntax
          inputs: ["Product", "TotalAmount"]
```

```yaml
# ❌ WRONG:
phase_2_rows:
  filter:
    details:
      rules:
      - column: SID  # ❌ Wrong! SID doesn't exist yet, use SaleID

phase_3_columns:
  select_columns:
    applied: true  # ❌ Wrong if selecting derived columns
    details:
      columns: [SID, Price, TotalAmount, Discount]  # ❌ Last 2 don't exist yet!

phase_4_enrichment:
  calculated_columns:
    details:
      rules:
      - formula:
          expression: "Quantity * UnitPrice"  # ❌ Wrong! Use Price (after rename)
      - formula:
          expression: "IF(Product IN ['Monitor'], ...)"  # ❌ Invalid Python syntax
```

Return the COMPLETE updated configuration YAML inside ```yaml ... ``` fences.

## YAML SAFETY CHECKLIST (verify before outputting)

- [ ] No `!!python/object`, `!!python/tuple`, `!!python/object/apply:numpy.dtype` or any `!!` tags
- [ ] All dtypes are plain quoted strings: `"float64"`, `"int64"`, `"object"`, `"bool"`, `"datetime64[ns]"`, etc.
- [ ] Every list item (`-`) is indented 2 spaces deeper than its parent key
- [ ] No list items (`-`) appear at the same indentation level as a sibling mapping key
- [ ] All values containing `:`, `#`, `[`, `]`, `*`, `!`, or comparison operators are quoted
- [ ] Indentation uses spaces only (no tabs), consistently 2 spaces per level
"""
