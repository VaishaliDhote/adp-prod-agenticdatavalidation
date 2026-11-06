# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Column mapping detector (direct/rename mappings, tasks 5-6). REUSABLE.
# Rework: attach source/target column UUIDs and feed the rule inventory.
# ============================================================================
import pandas as pd
from typing import Dict

def detect_column_mapping(input_df: pd.DataFrame, output_df: pd.DataFrame) -> Dict[str, str]:
    """Detect column name mappings between input and output."""
    print("\n🔍 Detecting column mappings...\n")
    
    # Check if columns match by name
    if set(output_df.columns) == set(input_df.columns):
        print("✅ Columns match by name")
        column_mapping = {col: col for col in input_df.columns if col in output_df.columns}
    else:
        print("⚠️ Columns don't match by name, attempting to match by data content...")
        column_mapping = {}
        
        for in_col in input_df.columns:
            for out_col in output_df.columns:
                if out_col not in column_mapping.values():
                    try:
                        in_values = set(input_df[in_col].dropna().astype(str).unique())
                        out_values = set(output_df[out_col].dropna().astype(str).unique())
                        
                        if len(out_values) > 0 and out_values.issubset(in_values):
                            sample_size = min(5, len(output_df))
                            in_sample = set(input_df[in_col].dropna().astype(str).head(sample_size))
                            out_sample = set(output_df[out_col].dropna().astype(str).head(sample_size))
                            
                            if len(in_sample.intersection(out_sample)) > 0:
                                column_mapping[in_col] = out_col
                                print(f"  ✓ Matched '{in_col}' → '{out_col}'")
                                break
                    except Exception as e:
                        continue
    
    print(f"\n📊 Column Mapping: {column_mapping}")
    return column_mapping

# Detect column mapping
# column_mapping = detect_column_mapping(input_df, output_df)

