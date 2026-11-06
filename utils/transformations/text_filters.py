# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Text filter detector. REUSABLE (detection only).
# Rework: retarget output to rule inventory + API mapping + source/target UUIDs;
# strip any pandas data-mutation helpers.
# ============================================================================
import pandas as pd
import numpy as np
import json
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path
from datetime import datetime


class TextFilter:
    """
    Complete implementation of Text-based filter detection and application.
    
    Excel Features Detected:
    - EQUALS: column = 'value'
    - NOT_EQUALS: column != 'value'
    - BEGINS_WITH: column starts with 'prefix'
    - ENDS_WITH: column ends with 'suffix'
    - CONTAINS: column contains 'substring'
    - NOT_CONTAINS: column does not contain 'substring'
    
    Detection Approach:
    - Compare unique values in input vs output for each column
    - Identify patterns that match all output values
    - Test filter candidates and validate by applying them
    - Accept only filters that reduce extra rows and improve column mismatches
    """
    
    def __init__(self, column_mapping: Optional[Dict[str, str]] = None, threshold: float = 0.5):
        """
        Initialize TextFilter detector.
        
        Args:
            column_mapping: Mapping of input to output column names
            threshold: Exclusion rate threshold for pattern detection (0.0 to 1.0)
        """
        self.column_mapping = column_mapping or {}
        self.threshold = threshold
        self.detection_results = []
        self.config = None
        self.current_df = None
        
    # ─────────────────────────────────────────────────────────────────────────
    # PATTERN DETECTION HELPER FUNCTIONS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _detect_ends_with_pattern(self, kept_values: List[str], 
                                   excluded_values: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect ENDS_WITH patterns from kept values."""
        if len(kept_values) == 0:
            return []
        
        suffix_candidates = {}
        
        for val in kept_values:
            val_str = str(val).strip()
            if len(val_str) < 3:
                continue
            
            # Priority 1: Email domain patterns
            if '@' in val_str:
                parts = val_str.split('@')
                if len(parts) == 2 and len(parts[1]) > 0:
                    email_suffix = '@' + parts[1]
                    if email_suffix not in suffix_candidates:
                        suffix_candidates[email_suffix] = {'values': [], 'is_email': True}
                    suffix_candidates[email_suffix]['values'].append(val_str)
                    
                    domain_only = parts[1]
                    if len(domain_only) >= 3:
                        if domain_only not in suffix_candidates:
                            suffix_candidates[domain_only] = {'values': [], 'is_email': True}
                        suffix_candidates[domain_only]['values'].append(val_str)
            
            # Priority 2: General suffixes
            for length in range(min(len(val_str), 15), 2, -1):
                suffix = val_str[-length:]
                if suffix not in suffix_candidates:
                    suffix_candidates[suffix] = {'values': [], 'is_email': False}
                if val_str not in suffix_candidates[suffix]['values']:
                    suffix_candidates[suffix]['values'].append(val_str)
        
        valid_patterns = []
        
        for suffix, info in suffix_candidates.items():
            matching_vals = info['values']
            is_email = info['is_email']
            
            # Must match ALL kept values
            if len(matching_vals) != len(kept_values):
                continue
            
            # Check exclusion rate
            excluded_with_suffix = [v for v in excluded_values if str(v).endswith(suffix)]
            
            if len(excluded_values) > 0:
                exclusion_rate = 1 - (len(excluded_with_suffix) / len(excluded_values))
            else:
                exclusion_rate = 1.0
            
            # Email domains get preferential treatment
            email_threshold = 0.2
            threshold = email_threshold if is_email else self.threshold
            
            if exclusion_rate >= threshold and len(suffix) >= 3:
                priority_score = len(matching_vals) * 1000 + len(suffix) * 10
                if is_email:
                    priority_score += 10000
                
                valid_patterns.append((suffix, matching_vals, priority_score))
        
        # Sort by priority score
        valid_patterns.sort(key=lambda x: x[2], reverse=True)
        return [(suffix, vals) for suffix, vals, score in valid_patterns]
    
    def _detect_begins_with_pattern(self, kept_values: List[str], 
                                     excluded_values: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect BEGINS_WITH patterns from kept values."""
        common_prefixes = {}
        
        for val in kept_values:
            if len(val) >= 3:
                for length in range(min(len(val), 15), 2, -1):
                    prefix = val[:length]
                    if prefix not in common_prefixes:
                        common_prefixes[prefix] = []
                    common_prefixes[prefix].append(val)
        
        sorted_prefixes = sorted(
            common_prefixes.items(), 
            key=lambda x: (len(x[1]), len(x[0])), 
            reverse=True
        )
        
        valid_patterns = []
        for prefix, matching_vals in sorted_prefixes:
            if len(matching_vals) != len(kept_values):
                continue
            
            excluded_with_prefix = [v for v in excluded_values if str(v).startswith(prefix)]
            if len(excluded_values) > 0:
                exclusion_rate = 1 - (len(excluded_with_prefix) / len(excluded_values))
            else:
                exclusion_rate = 1
            
            if exclusion_rate >= self.threshold and len(prefix) >= 3:
                valid_patterns.append((prefix, matching_vals))
        
        return valid_patterns
    
    def _detect_contains_pattern(self, kept_values: List[str], 
                                  excluded_values: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect CONTAINS patterns - prefers shortest substring."""
        common_substrings = {}
        
        for val in kept_values:
            if len(val) >= 3:
                for length in range(3, min(len(val), 15) + 1):
                    for start in range(len(val) - length + 1):
                        substring = val[start:start + length]
                        if substring not in common_substrings:
                            common_substrings[substring] = []
                        if val not in common_substrings[substring]:
                            common_substrings[substring].append(val)
        
        # Sort by most matches first, then SHORTEST length
        sorted_substrings = sorted(
            common_substrings.items(),
            key=lambda x: (len(x[1]), -len(x[0])),
            reverse=True
        )
        
        valid_patterns = []
        for substring, matching_vals in sorted_substrings:
            if len(matching_vals) != len(kept_values):
                continue
            
            excluded_with_substring = [v for v in excluded_values if substring in str(v)]
            if len(excluded_values) > 0:
                exclusion_rate = 1 - (len(excluded_with_substring) / len(excluded_values))
            else:
                exclusion_rate = 1
            
            if exclusion_rate >= self.threshold and len(substring) >= 3:
                valid_patterns.append((substring, matching_vals))
        
        return valid_patterns
    
    def _detect_not_contains_pattern(self, kept_values: List[str], 
                                      excluded_values: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect NOT_CONTAINS patterns - prefers LONGEST meaningful keyword."""
        if len(excluded_values) == 0:
            return []
        
        excluded_substrings = {}
        
        for val in excluded_values:
            if len(val) >= 3:
                for length in range(3, min(len(val), 15) + 1):
                    for start in range(len(val) - length + 1):
                        substring = val[start:start + length]
                        # Skip substrings with leading/trailing punctuation
                        if substring != substring.strip(' ,;:-'):
                            continue
                        if substring not in excluded_substrings:
                            excluded_substrings[substring] = []
                        if val not in excluded_substrings[substring]:
                            excluded_substrings[substring].append(val)
        
        # Sort by: 1) most matches first, 2) LONGEST length (not shortest!)
        sorted_substrings = sorted(
            excluded_substrings.items(),
            key=lambda x: (len(x[1]), len(x[0])),  # Changed: now longest patterns first
            reverse=True
        )
        
        valid_patterns = []
        for substring, matching_vals in sorted_substrings:
            if len(matching_vals) != len(excluded_values):
                continue
            
            kept_with_substring = [v for v in kept_values if substring in str(v)]
            if len(kept_values) > 0:
                inclusion_rate = 1 - (len(kept_with_substring) / len(kept_values))
            else:
                inclusion_rate = 1
            
            if inclusion_rate >= self.threshold and len(substring) >= 3:
                valid_patterns.append((substring, matching_vals))
        
        return valid_patterns
    
    # ─────────────────────────────────────────────────────────────────────────
    # VALIDATION HELPER FUNCTIONS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _create_row_tuples(self, df: pd.DataFrame, columns: List[str]) -> Set[Tuple]:
        """Create set of row tuples from DataFrame."""
        return set(
            tuple(str(row[col]) for col in columns)
            for _, row in df.iterrows()
        )
    
    def _validate_filter(self, test_df: pd.DataFrame, output_df: pd.DataFrame, 
                        index_cols: List[str]) -> Tuple[bool, int, set, set]:
        """Validate if output DataFrame is subset of filtered DataFrame."""
        comparison_cols = [col for col in output_df.columns if col in test_df.columns and col not in index_cols]
        
        output_tuples = self._create_row_tuples(output_df, comparison_cols)
        test_tuples = self._create_row_tuples(test_df, comparison_cols)
        
        is_subset = output_tuples.issubset(test_tuples)
        missing = output_tuples - test_tuples
        extra = test_tuples - output_tuples
        
        return is_subset, len(test_df), missing, extra
    
    def _compute_column_mismatches(self, current_df: pd.DataFrame, output_df: pd.DataFrame,
                                    index_cols: List[str]) -> int:
        """Count columns where unique value sets differ."""
        comparison_cols = [col for col in output_df.columns if col not in index_cols]
        
        mismatch_count = 0
        for col in comparison_cols:
            if col not in current_df.columns:
                continue
            curr_vals = set(current_df[col].dropna().astype(str).unique())
            out_vals = set(output_df[col].dropna().astype(str).unique())
            if curr_vals != out_vals:
                mismatch_count += 1
        
        return mismatch_count
    
    # ─────────────────────────────────────────────────────────────────────────
    # MAIN DETECTION FUNCTION
    # ─────────────────────────────────────────────────────────────────────────
    
    def detect(self, input_df: pd.DataFrame, output_df: pd.DataFrame, 
               verbose: bool = True) -> List[Dict[str, Any]]:
        """
        Detect text-based filters applied sequentially to transform input to output.
        
        Args:
            input_df: Input DataFrame
            output_df: Output DataFrame (after filters applied)
            verbose: Print detection details
            
        Returns:
            List of detected filters with operators and values
        """
        if verbose:
            print("\n" + "="*80)
            print("🔍 DETECTING TEXT-BASED FILTERS SEQUENTIALLY")
            print("="*80)
        
        # Detect index columns
        index_cols = []
        for col in output_df.columns:
            try:
                vals = output_df[col].dropna().astype(int).tolist()
                if len(vals) > 0:
                    sorted_vals = sorted(vals)
                    diffs = [sorted_vals[i+1] - sorted_vals[i] for i in range(len(sorted_vals)-1)]
                    if len(diffs) > 0 and diffs.count(1) / len(diffs) >= 0.7:
                        index_cols.append(col)
            except:
                pass
        
        if verbose:
            print(f"\n🔢 Detected index columns: {index_cols}")
        
        # Initialize
        self.current_df = input_df.copy()
        self.detection_results = []
        max_iterations = len(input_df.columns) * 3
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            if verbose:
                print(f"\n{'='*70}")
                print(f"ITERATION {iteration}: Current state")
                print(f"{'='*70}")
                print(f"Current rows: {len(self.current_df)}")
                print(f"Target rows: {len(output_df)}")
                print(f"Filters applied: {len(self.detection_results)}\n")
            
            # Check if we've reached the target
            is_valid, _, missing, extra = self._validate_filter(
                self.current_df, output_df, index_cols
            )
            mismatch_count = self._compute_column_mismatches(self.current_df, output_df, index_cols)
            
            if is_valid and len(extra) == 0 and len(missing) == 0 and \
               len(self.current_df) == len(output_df) and mismatch_count == 0:
                if verbose:
                    print("✅ PERFECT MATCH! All filters detected.\n")
                break
            
            if verbose:
                print(f"⏳ Not yet matched - Missing: {len(missing)}, Extra: {len(extra)} rows | Column mismatches: {mismatch_count}\n")
            
            current_extra_count = len(extra)
            current_mismatch_count = mismatch_count
            found_filter = False
            best_candidate = None
            best_score = float('inf')
            
            # Try each column
            for col in input_df.columns:
                if col in index_cols:
                    continue
                
                if any(f['input_column'] == col for f in self.detection_results):
                    continue
                
                # Skip non-string columns
                if not (pd.api.types.is_string_dtype(self.current_df[col]) or 
                        pd.api.types.is_object_dtype(self.current_df[col])):
                    continue
                
                try:
                    current_values = set(self.current_df[col].dropna().astype(str).unique())
                    output_values = set(output_df[col].dropna().astype(str).unique())
                    
                    if current_values == output_values:
                        continue
                    
                    kept_values = list(output_values)
                    excluded_values = list(current_values - output_values)
                    
                    if verbose:
                        print(f"\n{'─'*70}")
                        print(f"Testing column: '{col}'")
                        print(f"{'─'*70}")
                        print(f"Current unique: {len(current_values)}")
                        print(f"Target unique: {len(output_values)}")
                        print(f"Kept: {len(kept_values)}, Excluded: {len(excluded_values)}\n")
                    
                    # Test patterns in priority order and collect all valid results
                    filter_candidates = []
                    
                    # 1. Test EQUALS
                    if len(kept_values) == 1:
                        value = kept_values[0]
                        test_df = self.current_df[self.current_df[col].astype(str) == value]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "EQUALS", "value": value},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "EQUALS"
                            })
                    
                    # 2. Test NOT_EQUALS
                    if len(excluded_values) == 1 and len(self.current_df[col].unique()) > 2:
                        value = excluded_values[0]
                        test_df = self.current_df[self.current_df[col].astype(str) != value]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "NOT_EQUALS", "value": value},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "NOT_EQUALS"
                            })
                    
                    # 3. Test ENDS_WITH
                    patterns = self._detect_ends_with_pattern(kept_values, excluded_values)
                    for suffix, _ in patterns:
                        test_df = self.current_df[self.current_df[col].astype(str).str.endswith(suffix)]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "ENDS_WITH", "value": suffix},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "ENDS_WITH"
                            })
                    
                    # 4. Test BEGINS_WITH
                    patterns = self._detect_begins_with_pattern(kept_values, excluded_values)
                    for prefix, _ in patterns:
                        test_df = self.current_df[self.current_df[col].astype(str).str.startswith(prefix)]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "BEGINS_WITH", "value": prefix},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "BEGINS_WITH"
                            })
                    
                    # 5. Test CONTAINS
                    patterns = self._detect_contains_pattern(kept_values, excluded_values)
                    for substring, _ in patterns:
                        test_df = self.current_df[self.current_df[col].astype(str).str.contains(substring, regex=False)]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "CONTAINS", "value": substring},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "CONTAINS"
                            })
                    
                    # 6. Test NOT_CONTAINS
                    patterns = self._detect_not_contains_pattern(kept_values, excluded_values)
                    for substring, _ in patterns:
                        test_df = self.current_df[~self.current_df[col].astype(str).str.contains(substring, regex=False)]
                        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
                        if is_valid:
                            filter_candidates.append({
                                "config": {"column": col, "input_column": col, "operator": "NOT_CONTAINS", "value": substring},
                                "test_df": test_df,
                                "extra": len(extra),
                                "missing": len(missing),
                                "op": "NOT_CONTAINS"
                            })
                    
                    # Pick the best candidate for this column (fewest extra rows, then fewest missing)
                    if filter_candidates:
                        filter_candidates.sort(key=lambda x: (x["extra"], x["missing"]))
                        best = filter_candidates[0]
                        
                        if verbose:
                            print(f"  🧪 Testing {best['op']} filter: {best['config']['value']}")
                            print(f"     Extra rows: {best['extra']}, Missing rows: {best['missing']}")
                            print(f"  ✅ CONFIRMED: {col} {best['op']} '{best['config']['value']}'")
                            print(f"  🔄 Updated current state: {len(best['test_df'])} rows\n")
                        
                        self.detection_results.append(best["config"])
                        self.current_df = best["test_df"]
                        found_filter = True
                        break
                
                except Exception as e:
                    if verbose:
                        print(f"❌ Error processing column '{col}': {str(e)}")
                    continue
            
            if not found_filter:
                if verbose:
                    print("\n⚠️ No more filters found. Stopping.\n")
                break
        
        # Generate config
        self._generate_config()
        
        return self.detection_results
    
    # ─────────────────────────────────────────────────────────────────────────
    # FILTER TEST FUNCTIONS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _test_equals(self, col: str, kept_values: List[str], excluded_values: List[str],
                     output_df: pd.DataFrame, index_cols: List[str], 
                     verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test EQUALS filter."""
        if len(kept_values) != 1:
            return None
        
        value = kept_values[0]
        test_df = self.current_df[self.current_df[col].astype(str) == value]
        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
        
        if is_valid:
            if verbose:
                print(f"🧪 Testing EQUALS filter: = '{value}'")
                print(f"  ✅ CONFIRMED")
            return {
                "column": col,
                "input_column": col,
                "operator": "EQUALS",
                "value": value
            }, test_df
        
        return None
    
    def _test_not_equals(self, col: str, kept_values: List[str], excluded_values: List[str],
                        output_df: pd.DataFrame, index_cols: List[str],
                        verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test NOT_EQUALS filter."""
        if len(excluded_values) != 1 or len(self.current_df[col].unique()) <= 2:
            return None
        
        value = excluded_values[0]
        test_df = self.current_df[self.current_df[col].astype(str) != value]
        is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
        
        if is_valid:
            if verbose:
                print(f"🧪 Testing NOT_EQUALS filter: != '{value}'")
                print(f"  ✅ CONFIRMED")
            return {
                "column": col,
                "input_column": col,
                "operator": "NOT_EQUALS",
                "value": value
            }, test_df
        
        return None
    
    def _test_ends_with(self, col: str, kept_values: List[str], excluded_values: List[str],
                       output_df: pd.DataFrame, index_cols: List[str],
                       verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test ENDS_WITH filter."""
        patterns = self._detect_ends_with_pattern(kept_values, excluded_values)
        
        for suffix, _ in patterns:
            test_df = self.current_df[self.current_df[col].astype(str).str.endswith(suffix)]
            is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
            
            if is_valid and len(extra) < 20:  # Reasonable threshold
                if verbose:
                    print(f"🧪 Testing ENDS_WITH filter: '{suffix}'")
                    print(f"  ✅ CONFIRMED")
                return {
                    "column": col,
                    "input_column": col,
                    "operator": "ENDS_WITH",
                    "value": suffix
                }, test_df
        
        return None
    
    def _test_begins_with(self, col: str, kept_values: List[str], excluded_values: List[str],
                         output_df: pd.DataFrame, index_cols: List[str],
                         verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test BEGINS_WITH filter."""
        patterns = self._detect_begins_with_pattern(kept_values, excluded_values)
        
        for prefix, _ in patterns:
            test_df = self.current_df[self.current_df[col].astype(str).str.startswith(prefix)]
            is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
            
            if is_valid and len(extra) < 20:
                if verbose:
                    print(f"🧪 Testing BEGINS_WITH filter: '{prefix}'")
                    print(f"  ✅ CONFIRMED")
                return {
                    "column": col,
                    "input_column": col,
                    "operator": "BEGINS_WITH",
                    "value": prefix
                }, test_df
        
        return None
    
    def _test_contains(self, col: str, kept_values: List[str], excluded_values: List[str],
                      output_df: pd.DataFrame, index_cols: List[str],
                      verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test CONTAINS filter."""
        patterns = self._detect_contains_pattern(kept_values, excluded_values)
        
        for substring, _ in patterns:
            test_df = self.current_df[self.current_df[col].astype(str).str.contains(substring, regex=False)]
            is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
            
            if is_valid and len(extra) < 20:
                if verbose:
                    print(f"🧪 Testing CONTAINS filter: '{substring}'")
                    print(f"  ✅ CONFIRMED")
                return {
                    "column": col,
                    "input_column": col,
                    "operator": "CONTAINS",
                    "value": substring
                }, test_df
        
        return None
    
    def _test_not_contains(self, col: str, kept_values: List[str], excluded_values: List[str],
                          output_df: pd.DataFrame, index_cols: List[str],
                          verbose: bool = True) -> Optional[Tuple[Dict, pd.DataFrame]]:
        """Test NOT_CONTAINS filter."""
        patterns = self._detect_not_contains_pattern(kept_values, excluded_values)
        
        for substring, _ in patterns:
            test_df = self.current_df[~self.current_df[col].astype(str).str.contains(substring, regex=False)]
            is_valid, test_count, missing, extra = self._validate_filter(test_df, output_df, index_cols)
            
            if is_valid and len(extra) < 20:
                if verbose:
                    print(f"🧪 Testing NOT_CONTAINS filter: '{substring}'")
                    print(f"  ✅ CONFIRMED")
                return {
                    "column": col,
                    "input_column": col,
                    "operator": "NOT_CONTAINS",
                    "value": substring
                }, test_df
        
        return None
    
    # ─────────────────────────────────────────────────────────────────────────
    # CONFIG GENERATION
    # ─────────────────────────────────────────────────────────────────────────
    
    def _generate_config(self) -> Dict[str, Any]:
        """Generate transformation configuration from detected filters."""
        self.config = {
            "transformation": "text_filters",
            "description": "Sequential text-based filtering",
            "filters": self.detection_results,
            "filter_count": len(self.detection_results),
            "timestamp": datetime.now().isoformat()
        }
        return self.config
    
    def get_config(self) -> Dict[str, Any]:
        """Get the generated configuration."""
        return self.config
    
    def save_config(self, output_path: str, verbose: bool = True) -> None:
        """Save configuration to JSON file."""
        if self.config is None:
            raise ValueError("No configuration available. Run detect() first.")
        
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        
        if verbose:
            print(f"\n💾 Configuration saved to: {output_file}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # APPLICATION
    # ─────────────────────────────────────────────────────────────────────────
    
    def apply(self, df: pd.DataFrame, config: Optional[Dict[str, Any]] = None,
              verbose: bool = True) -> pd.DataFrame:
        """
        Apply detected filters to a DataFrame.
        
        Args:
            df: Input DataFrame
            config: Configuration dictionary (uses detected config if None)
            verbose: Print application details
            
        Returns:
            Filtered DataFrame
        """
        if config is None:
            config = self.config
        
        if config is None:
            raise ValueError("No configuration available. Run detect() first or provide config.")
        
        result_df = df.copy()
        
        if verbose:
            print("\n" + "="*80)
            print("🔄 APPLYING TEXT FILTERS")
            print("="*80)
            print(f"\nStarting rows: {len(result_df)}\n")
        
        for i, filter_config in enumerate(config.get('filters', []), 1):
            col = filter_config['column']
            op = filter_config['operator']
            val = filter_config['value']
            
            before = len(result_df)
            
            if op == 'EQUALS':
                result_df = result_df[result_df[col].astype(str) == val]
            elif op == 'NOT_EQUALS':
                result_df = result_df[result_df[col].astype(str) != val]
            elif op == 'BEGINS_WITH':
                result_df = result_df[result_df[col].astype(str).str.startswith(val)]
            elif op == 'ENDS_WITH':
                result_df = result_df[result_df[col].astype(str).str.endswith(val)]
            elif op == 'CONTAINS':
                result_df = result_df[result_df[col].astype(str).str.contains(val, regex=False)]
            elif op == 'NOT_CONTAINS':
                result_df = result_df[~result_df[col].astype(str).str.contains(val, regex=False)]
            
            after = len(result_df)
            
            if verbose:
                print(f"{i}. {col} {op} '{val}'")
                print(f"   {before} → {after} rows\n")
        
        if verbose:
            print(f"Final rows: {len(result_df)}")
            print("✅ Filters applied successfully!")
        
        return result_df
    
    def get_summary(self) -> str:
        """Get a summary of detected filters."""
        if not self.detection_results:
            return "No filters detected yet."
        
        summary = "\n" + "="*80 + "\n"
        summary += "📋 TEXT FILTERS SUMMARY\n"
        summary += "="*80 + "\n"
        summary += f"\nTotal filters detected: {len(self.detection_results)}\n"
        summary += "\nFilters (in application order):\n"
        
        for i, f in enumerate(self.detection_results, 1):
            summary += f"  {i}. {f['column']} {f['operator']} '{f['value']}'\n"
        
        return summary
