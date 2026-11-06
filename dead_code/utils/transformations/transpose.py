import pandas as pd
import json
from typing import Dict, Any, Optional, Tuple
from pathlib import Path


class TransposeTransformation:
    """
    Complete implementation of Transpose transformation detection and application.
    
    Excel Feature: Copy → Paste Special → Transpose
    Pattern: Rows become columns, columns become rows
    
    Detection Clues:
    - Difficult to detect from column names alone
    - Requires row count comparison
    - Dimension swap: input rows ≈ output columns AND input columns ≈ output rows
    """
    
    def __init__(self, threshold: float = 0.7):
        """
        Initialize TransposeTransformation.
        
        Args:
            threshold: Similarity threshold (0.0 to 1.0) to confirm transpose detection
        """
        self.threshold = threshold
        self.detection_result = None
        self.config = None
        
    def detect(self, input_df: pd.DataFrame, output_df: pd.DataFrame, verbose: bool = True) -> Dict[str, Any]:
        """
        Detect if a transpose transformation has been applied.
        
        Detection Logic:
        1. Dimension swap: input rows ≈ output columns AND input columns ≈ output rows
        2. Content verification: Check if transposed input matches output data
        3. Similarity score: Calculate how similar the transposed input is to output
        
        Args:
            input_df: Input DataFrame
            output_df: Output DataFrame
            verbose: Print detection details
            
        Returns:
            Dictionary with detection results and transformation config
        """
        result = {
            "transformation_detected": False,
            "transformation_type": "transpose",
            "confidence_score": 0.0,
            "detection_details": {},
            "transformation_config": None
        }
        
        if verbose:
            print("\n" + "="*80)
            print("🔄 TRANSPOSE TRANSFORMATION DETECTION")
            print("="*80)
        
        # Get dimensions
        input_rows, input_cols = input_df.shape
        output_rows, output_cols = output_df.shape
        
        if verbose:
            print(f"\n📊 Input dimensions:  {input_rows} rows × {input_cols} columns")
            print(f"📊 Output dimensions: {output_rows} rows × {output_cols} columns")
        
        # Check 1: Dimension swap detection
        dimension_swap = False
        dimension_tolerance = 0.1  # Allow 10% difference
        
        if input_rows > 0 and output_cols > 0:
            row_col_ratio = abs(input_rows - output_cols) / max(input_rows, output_cols)
            col_row_ratio = abs(input_cols - output_rows) / max(input_cols, output_rows)
            
            if row_col_ratio <= dimension_tolerance and col_row_ratio <= dimension_tolerance:
                dimension_swap = True
                if verbose:
                    print(f"\n✅ Dimension swap detected!")
                    print(f"   - Input rows ({input_rows}) ≈ Output columns ({output_cols})")
                    print(f"   - Input columns ({input_cols}) ≈ Output rows ({output_rows})")
            else:
                if verbose:
                    print(f"\n❌ No dimension swap detected")
                    print(f"   - Row-to-column ratio difference: {row_col_ratio:.1%}")
                    print(f"   - Column-to-row ratio difference: {col_row_ratio:.1%}")
        
        result["detection_details"]["dimension_swap"] = dimension_swap
        
        if not dimension_swap:
            if verbose:
                print(f"\n⚠️ Dimensions don't suggest a transpose operation.")
            self.detection_result = result
            return result
        
        # Check 2: Content similarity after transpose
        if verbose:
            print(f"\n🔍 Checking content similarity after transpose...")
        
        try:
            # Transpose the input
            transposed_input = input_df.T.reset_index()
            
            if verbose:
                print(f"\n📋 Transposed input shape: {transposed_input.shape}")
                print(f"📋 Output shape: {output_df.shape}")
            
            # Compare shapes
            shape_match = transposed_input.shape == output_df.shape
            result["detection_details"]["shape_match"] = shape_match
            
            if shape_match:
                if verbose:
                    print(f"✅ Shapes match after transpose!")
                
                # Calculate content similarity
                matching_cells = 0
                total_cells = 0
                
                for i in range(transposed_input.shape[0]):
                    for j in range(transposed_input.shape[1]):
                        total_cells += 1
                        try:
                            trans_val = str(transposed_input.iloc[i, j]).strip().lower()
                            out_val = str(output_df.iloc[i, j]).strip().lower()
                            
                            if trans_val == out_val or (pd.isna(transposed_input.iloc[i, j]) and pd.isna(output_df.iloc[i, j])):
                                matching_cells += 1
                        except:
                            pass
                
                if total_cells > 0:
                    similarity_score = matching_cells / total_cells
                    result["confidence_score"] = similarity_score
                    result["detection_details"]["matching_cells"] = matching_cells
                    result["detection_details"]["total_cells"] = total_cells
                    
                    if verbose:
                        print(f"\n📈 Content Similarity: {similarity_score:.1%}")
                        print(f"   - Matching cells: {matching_cells}/{total_cells}")
                    
                    if similarity_score >= self.threshold:
                        result["transformation_detected"] = True
                        if verbose:
                            print(f"\n✅ TRANSPOSE TRANSFORMATION CONFIRMED (score: {similarity_score:.1%})")
                    else:
                        if verbose:
                            print(f"\n⚠️ Similarity below threshold ({self.threshold:.1%})")
            else:
                if verbose:
                    print(f"❌ Shapes don't match after transpose")
                
                # Check for partial transpose
                min_rows = min(transposed_input.shape[0], output_df.shape[0])
                min_cols = min(transposed_input.shape[1], output_df.shape[1])
                
                if min_rows > 0 and min_cols > 0:
                    if verbose:
                        print(f"\n🔍 Checking partial transpose match ({min_rows}×{min_cols})...")
                    
                    matching_cells = 0
                    total_cells = min_rows * min_cols
                    
                    for i in range(min_rows):
                        for j in range(min_cols):
                            try:
                                trans_val = str(transposed_input.iloc[i, j]).strip().lower()
                                out_val = str(output_df.iloc[i, j]).strip().lower()
                                if trans_val == out_val:
                                    matching_cells += 1
                            except:
                                pass
                    
                    partial_similarity = matching_cells / total_cells if total_cells > 0 else 0
                    
                    if verbose:
                        print(f"📈 Partial similarity: {partial_similarity:.1%}")
                    
                    if partial_similarity >= self.threshold:
                        result["transformation_detected"] = True
                        result["confidence_score"] = partial_similarity
                        result["detection_details"]["partial_match"] = True
                        if verbose:
                            print(f"✅ Partial transpose detected!")
        
        except Exception as e:
            if verbose:
                print(f"\n❌ Error during content comparison: {str(e)}")
            result["detection_details"]["error"] = str(e)
        
        # Generate transformation config if detected
        if result["transformation_detected"]:
            result["transformation_config"] = self._generate_config(
                input_rows, input_cols, result["confidence_score"]
            )
        
        self.detection_result = result
        self.config = result.get("transformation_config")
        return result
    
    def _generate_config(self, input_rows: int, input_cols: int, confidence: float) -> Dict[str, Any]:
        """
        Generate transformation configuration.
        
        Args:
            input_rows: Number of rows in input
            input_cols: Number of columns in input
            confidence: Detection confidence score
            
        Returns:
            Configuration dictionary
        """
        return {
            "transformation": "transpose",
            "description": "Swap rows and columns (Excel: Copy → Paste Special → Transpose)",
            "parameters": {
                "operation": "transpose",
                "include_index": True,
                "reset_index": True
            },
            "input_shape": {
                "rows": input_rows,
                "columns": input_cols
            },
            "expected_output_shape": {
                "rows": input_cols,
                "columns": input_rows
            },
            "confidence": confidence
        }
    
    def generate_config(self, input_df: pd.DataFrame, output_df: pd.DataFrame, 
                       output_path: Optional[str] = None, verbose: bool = True) -> Optional[Dict[str, Any]]:
        """
        Detect transpose and generate configuration file.
        
        Args:
            input_df: Input DataFrame
            output_df: Output DataFrame
            output_path: Optional path to save configuration JSON file
            verbose: Print generation details
            
        Returns:
            Configuration dictionary if transpose detected, None otherwise
        """
        # Run detection if not already done
        if self.detection_result is None:
            self.detect(input_df, output_df, verbose=verbose)
        
        if not self.detection_result["transformation_detected"]:
            if verbose:
                print("\n⚠️ No transpose transformation detected. Cannot generate config.")
            return None
        
        config = self.detection_result["transformation_config"]
        
        if verbose:
            print("\n" + "="*80)
            print("📄 TRANSFORMATION CONFIGURATION")
            print("="*80)
            print(json.dumps(config, indent=2))
        
        # Save to file if path provided
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            if verbose:
                print(f"\n💾 Configuration saved to: {output_file}")
        
        return config
    
    def apply(self, df: pd.DataFrame, config: Optional[Dict[str, Any]] = None, 
              verbose: bool = True) -> pd.DataFrame:
        """
        Apply transpose transformation to a DataFrame.
        
        Args:
            df: Input DataFrame to transpose
            config: Configuration dictionary (uses detected config if None)
            verbose: Print application details
            
        Returns:
            Transposed DataFrame
        """
        # Use provided config or previously detected config
        if config is None:
            config = self.config
        
        if config is None:
            raise ValueError("No configuration available. Run detect() first or provide config.")
        
        if verbose:
            print("\n🔄 Applying transpose transformation...")
            print(f"Input shape: {df.shape}")
        
        # Transpose the dataframe
        transposed_df = df.T
        
        # Reset index if specified
        if config.get('parameters', {}).get('reset_index', True):
            transposed_df = transposed_df.reset_index()
        
        if verbose:
            print(f"Output shape: {transposed_df.shape}")
            print("✅ Transpose applied successfully!")
        
        return transposed_df
    
    def confirm_by_reapplying(self, input_df: pd.DataFrame, output_df: pd.DataFrame, 
                              verbose: bool = True) -> Tuple[bool, float, Optional[pd.DataFrame]]:
        """
        Confirm the transformation by applying it and comparing with expected output.
        
        Args:
            input_df: Input DataFrame
            output_df: Expected output DataFrame
            verbose: Print confirmation details
            
        Returns:
            Tuple of (confirmed: bool, similarity_score: float, applied_df: DataFrame)
        """
        if verbose:
            print("\n" + "="*80)
            print("🧪 CONFIRMING TRANSFORMATION BY REAPPLYING")
            print("="*80)
        
        # Run detection if not already done
        if self.detection_result is None:
            self.detect(input_df, output_df, verbose=False)
        
        if not self.detection_result["transformation_detected"]:
            if verbose:
                print("\n❌ No transpose transformation detected to confirm.")
            return False, 0.0, None
        
        # Apply the transformation
        applied_df = self.apply(input_df, verbose=verbose)
        
        if verbose:
            print("\n🔍 Comparing applied transformation with expected output...")
        
        # Compare shapes
        if applied_df.shape != output_df.shape:
            if verbose:
                print(f"❌ Shape mismatch:")
                print(f"   - Applied: {applied_df.shape}")
                print(f"   - Expected: {output_df.shape}")
            return False, 0.0, applied_df
        
        # Calculate similarity
        matching_cells = 0
        total_cells = applied_df.shape[0] * applied_df.shape[1]
        
        for i in range(applied_df.shape[0]):
            for j in range(applied_df.shape[1]):
                try:
                    applied_val = str(applied_df.iloc[i, j]).strip().lower()
                    expected_val = str(output_df.iloc[i, j]).strip().lower()
                    
                    if applied_val == expected_val or (pd.isna(applied_df.iloc[i, j]) and pd.isna(output_df.iloc[i, j])):
                        matching_cells += 1
                except:
                    pass
        
        similarity = matching_cells / total_cells if total_cells > 0 else 0
        confirmed = similarity >= self.threshold
        
        if verbose:
            print(f"\n📈 Similarity Score: {similarity:.1%}")
            print(f"   - Matching cells: {matching_cells}/{total_cells}")
            
            if confirmed:
                print(f"\n✅ TRANSFORMATION CONFIRMED! (threshold: {self.threshold:.1%})")
            else:
                print(f"\n❌ Confirmation failed (below threshold: {self.threshold:.1%})")
        
        return confirmed, similarity, applied_df
    
    def apply_to_new_input(self, new_input_df: pd.DataFrame, 
                           config_path: Optional[str] = None,
                           config_dict: Optional[Dict[str, Any]] = None,
                           output_path: Optional[str] = None,
                           verbose: bool = True) -> pd.DataFrame:
        """
        Apply transpose transformation to a new input file using saved configuration.
        
        Args:
            new_input_df: New input DataFrame to transform
            config_path: Path to configuration JSON file
            config_dict: Configuration dictionary (alternative to config_path)
            output_path: Optional path to save output Excel file
            verbose: Print application details
            
        Returns:
            Transformed DataFrame
        """
        if verbose:
            print("\n" + "="*80)
            print("🚀 APPLYING TRANSPOSE TO NEW INPUT")
            print("="*80)
        
        # Load configuration
        if config_path:
            with open(config_path, 'r') as f:
                config = json.load(f)
            if verbose:
                print(f"📂 Loaded configuration from: {config_path}")
        elif config_dict:
            config = config_dict
            if verbose:
                print(f"📋 Using provided configuration")
        elif self.config:
            config = self.config
            if verbose:
                print(f"📋 Using previously detected configuration")
        else:
            raise ValueError("No configuration provided. Specify config_path, config_dict, or run detect() first.")
        
        # Apply transformation
        output_df = self.apply(new_input_df, config=config, verbose=verbose)
        
        # Save to file if path provided
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_df.to_excel(output_file, index=False)
            if verbose:
                print(f"\n💾 Output saved to: {output_file}")
        
        return output_df
    
    def get_summary(self) -> str:
        """
        Get a summary of the detection results.
        
        Returns:
            Formatted summary string
        """
        if self.detection_result is None:
            return "No detection performed yet."
        
        summary = "\n" + "="*80 + "\n"
        summary += "📋 TRANSPOSE TRANSFORMATION SUMMARY\n"
        summary += "="*80 + "\n"
        summary += f"\nDetected: {self.detection_result['transformation_detected']}\n"
        summary += f"Confidence: {self.detection_result['confidence_score']:.1%}\n"
        summary += f"\nDetection Details:\n"
        summary += json.dumps(self.detection_result['detection_details'], indent=2)
        
        if self.detection_result['transformation_detected']:
            summary += "\n\n✅ Transpose transformation confirmed!"
            summary += "\n   Excel equivalent: Copy → Paste Special → Transpose"
            summary += "\n   Effect: Rows become columns, columns become rows"
        else:
            summary += "\n\n❌ No transpose transformation detected."
        
        return summary


# Convenience functions for backward compatibility
def transpose_dataframe(df: pd.DataFrame, config: Dict[str, Any] = None) -> pd.DataFrame:
    """
    Transpose the DataFrame: swap rows and columns.
    
    Args:
        df: Input DataFrame to transpose
        config: Optional configuration dictionary
        
    Returns:
        Transposed DataFrame
    """
    transformer = TransposeTransformation()
    return transformer.apply(df, config=config, verbose=False)

def detect_transpose(input_df: pd.DataFrame, output_df: pd.DataFrame, threshold: float = 0.7) -> bool:
    """
    Detect if a transpose transformation has been applied.
    
    Args:
        input_df: Input DataFrame
        output_df: Output DataFrame
        threshold: Similarity threshold to confirm transpose
        
    Returns:
        True if transpose detected, False otherwise
    """
    transformer = TransposeTransformation(threshold=threshold)
    result = transformer.detect(input_df, output_df, verbose=False)
    return result["transformation_detected"]