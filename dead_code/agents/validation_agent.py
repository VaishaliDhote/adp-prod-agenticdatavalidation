import pandas as pd
from utils.validations.derive_validation_rules import derive_validation_rules
from utils.validations.apply_validation_rules import apply_validation_rules
from llm_client import LLMClient


class SheetValidationAgent:
    """
    Agent responsible for detecting validation rules from the INPUT sheet
    and enforcing those rules row-by-row to surface empty-cell and other
    data-quality errors.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def validate(self, input_df: pd.DataFrame) -> dict:
        """
        1. Derive validation rules from the INPUT sheet column contents.
        2. Apply those rules back against every row so that empty cells
           (and other violations) are captured as structured errors.

        Args:
            input_df: DataFrame of input sheet (client data)

        Returns:
            dict with:
              - validation_rules  : {column → rule dict}
              - validation_result : {hasErrors, errorCount, errors, columnSummary}
        """
        print(f"  Deriving validation rules from INPUT sheet...")
        validation_rules = derive_validation_rules(input_df)
        print(f"  Derived rules for {len(validation_rules)} columns")

        print(f"  Applying validation rules to check for empty / invalid cells...")
        validation_result = apply_validation_rules(input_df, validation_rules)

        if validation_result["hasErrors"]:
            print(
                f"  ⚠  {validation_result['errorCount']} validation error(s) found "
                f"across {len(validation_result['columnSummary'])} column(s)."
            )
            for col, summary in validation_result["columnSummary"].items():
                print(
                    f"     • {col}: {summary['errorCount']} error(s) "
                    f"— types: {summary['errorTypes']}"
                )
        else:
            print(f"  ✓  All cells passed validation.")

        return {
            "validation_rules":  validation_rules,
            "validation_result": validation_result,
        }