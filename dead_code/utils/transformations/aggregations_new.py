import pandas as pd
import numpy as np
import re
from typing import Any

# ─────────────────────────────────────────────
# Keyword patterns for aggregate detection
# ─────────────────────────────────────────────
AGGREGATE_PREFIXES = re.compile(
    r"^(total|sum|avg|average|mean|count|count_of|number_of|num|"
    r"max|maximum|min|minimum|stdev|std|median|mode|grand_total|subtotal)_",
    re.IGNORECASE,
)

AGGREGATE_SUFFIXES = re.compile(
    r"_(total|sum|avg|average|mean|count|max|min|stdev|std|median|mode)$",
    re.IGNORECASE,
)

EXACT_AGGREGATE_NAMES = re.compile(
    r"^(total|grand_total|subtotal|count|sum|average|mean|minimum|maximum)$",
    re.IGNORECASE,
)

AGGREGATE_FUNCTION_MAP = {
    re.compile(r"^(total|sum|grand_total|subtotal)", re.IGNORECASE): "SUM",
    re.compile(r"^(avg|average|mean)", re.IGNORECASE): "AVERAGE",
    re.compile(r"^(count|count_of|number_of|num)", re.IGNORECASE): "COUNT",
    re.compile(r"^(max|maximum)", re.IGNORECASE): "MAX",
    re.compile(r"^(min|minimum)", re.IGNORECASE): "MIN",
    re.compile(r"^(stdev|std)", re.IGNORECASE): "STDEV",
    re.compile(r"^(median)", re.IGNORECASE): "MEDIAN",
    re.compile(r"^(mode)", re.IGNORECASE): "MODE",
}


def _infer_aggregate_function(col_name: str) -> str | None:
    """Infer the aggregate function from a column name."""
    col_clean = col_name.strip().lower()

    # Check prefix match
    for pattern, func in AGGREGATE_FUNCTION_MAP.items():
        if pattern.match(col_clean):
            return func

    # Check suffix match (e.g., Sales_Total, Revenue_Sum)
    for pattern, func in AGGREGATE_FUNCTION_MAP.items():
        suffix_str = pattern.pattern.strip("^()")
        if re.search(rf"_({suffix_str})$", col_clean, re.IGNORECASE):
            return func

    return None


def _is_aggregate_column(col_name: str) -> bool:
    """Return True if column name suggests an aggregated value."""
    return bool(
        AGGREGATE_PREFIXES.match(col_name)
        or AGGREGATE_SUFFIXES.search(col_name)
        or EXACT_AGGREGATE_NAMES.match(col_name)
    )


def _identify_grouping_columns(
    input_df: pd.DataFrame, output_df: pd.DataFrame
) -> list[str]:
    """
    Identify grouping columns: output columns that also exist in input
    and are NOT aggregate columns. These are the GROUP BY keys.
    """
    input_cols = set(input_df.columns.str.strip())
    output_cols = [c for c in output_df.columns if not _is_aggregate_column(c)]
    return [c for c in output_cols if c.strip() in input_cols]


def _row_count_reduction_ratio(
    input_df: pd.DataFrame, output_df: pd.DataFrame
) -> float:
    """Calculate how much the row count was reduced (0.0 – 1.0)."""
    if len(input_df) == 0:
        return 0.0
    return round(1 - (len(output_df) / len(input_df)), 4)


def _verify_aggregation_numerically(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
    grouping_cols: list[str],
    aggregate_cols: list[str],
) -> dict[str, Any]:
    """
    Spot-check aggregation by re-computing the expected aggregate values
    from the input DataFrame and comparing to the output DataFrame.

    Returns a dict of {output_col: {"function": str, "verified": bool}}.
    """
    results: dict[str, Any] = {}

    if not grouping_cols or not aggregate_cols:
        return results

    # Only verify when grouping columns exist in input
    if not all(c in input_df.columns for c in grouping_cols):
        return results

    for out_col in aggregate_cols:
        func = _infer_aggregate_function(out_col)
        if func is None:
            continue

        # Find a plausible source column in input (same name stripped of prefix/suffix)
        candidate_src = _find_source_column(out_col, input_df.columns.tolist())
        if candidate_src is None or candidate_src not in input_df.columns:
            results[out_col] = {"function": func, "verified": False, "reason": "no source column found"}
            continue

        if not pd.api.types.is_numeric_dtype(input_df[candidate_src]):
            results[out_col] = {"function": func, "verified": False, "reason": "source column not numeric"}
            continue

        # Re-compute aggregate
        try:
            func_map = {
                "SUM": "sum",
                "AVERAGE": "mean",
                "COUNT": "count",
                "MAX": "max",
                "MIN": "min",
                "STDEV": "std",
                "MEDIAN": "median",
            }
            pandas_func = func_map.get(func)
            if pandas_func is None:
                results[out_col] = {"function": func, "verified": False, "reason": "unsupported function for verification"}
                continue

            recomputed = (
                input_df.groupby(grouping_cols)[candidate_src]
                .agg(pandas_func)
                .reset_index()
                .rename(columns={candidate_src: out_col})
            )

            # Merge and compare
            merged = output_df[grouping_cols + [out_col]].merge(
                recomputed, on=grouping_cols, suffixes=("_out", "_recomputed")
            )

            if merged.empty:
                results[out_col] = {"function": func, "verified": False, "reason": "merge produced no rows"}
                continue

            match_col_out = f"{out_col}_out"
            match_col_rec = f"{out_col}_recomputed"

            if match_col_out not in merged.columns:
                # Single column (exact name match)
                match_col_out = out_col
                match_col_rec = out_col  # same → means column was not duplicated
                verified = True
            else:
                verified = bool(
                    np.allclose(
                        merged[match_col_out].fillna(0),
                        merged[match_col_rec].fillna(0),
                        rtol=1e-3,
                        atol=1e-3,
                    )
                )

            results[out_col] = {
                "function": func,
                "source_column": candidate_src,
                "verified": verified,
            }

        except Exception as exc:
            results[out_col] = {"function": func, "verified": False, "reason": str(exc)}

    return results


def _find_source_column(agg_col: str, input_columns: list[str]) -> str | None:
    """
    Try to match an aggregate output column back to its source input column.

    Strategy:
      1. Strip known prefixes / suffixes to get a base name.
      2. Look for exact match (case-insensitive) in input columns.
      3. Look for substring match.
    """
    # Build base name by removing aggregate prefix
    base = AGGREGATE_PREFIXES.sub("", agg_col).strip("_")
    # Remove aggregate suffix
    base = AGGREGATE_SUFFIXES.sub("", base).strip("_")

    if not base:
        return None

    input_lower = {c.strip().lower(): c for c in input_columns}

    # Exact base match
    if base.lower() in input_lower:
        return input_lower[base.lower()]

    # Partial match: base is contained in an input column name
    for lower_col, orig_col in input_lower.items():
        if base.lower() in lower_col or lower_col in base.lower():
            return orig_col

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def detect_aggregation(
    input_df: pd.DataFrame,
    output_df: pd.DataFrame,
) -> dict[str, Any]:
    """
    Detect GROUP BY / SUMMARIZE (aggregation) transformations between
    an input DataFrame and an output DataFrame.

    Parameters
    ----------
    input_df  : Raw / transaction-level source data.
    output_df : Aggregated / summarized target data.

    Returns
    -------
    dict with the following structure:
    {
        "is_aggregation": bool,
        "confidence": float,          # 0.0 – 1.0
        "grouping_columns": [...],
        "aggregate_columns": [
            {
                "output_column": str,
                "inferred_function": str | None,
                "source_column": str | None,
                "verified": bool,
            },
            ...
        ],
        "row_reduction_ratio": float,
        "excel_features": [...],
        "notes": [...],
    }
    """
    result: dict[str, Any] = {
        "is_aggregation": False,
        "confidence": 0.0,
        "grouping_columns": [],
        "aggregate_columns": [],
        "row_reduction_ratio": 0.0,
        "excel_features": [],
        "notes": [],
    }

    if input_df is None or output_df is None:
        result["notes"].append("One or both DataFrames are None.")
        return result

    if input_df.empty or output_df.empty:
        result["notes"].append("One or both DataFrames are empty.")
        return result

    # ── Step 1: Detect aggregate output columns ───────────────────────────
    agg_output_cols = [c for c in output_df.columns if _is_aggregate_column(c)]

    # ── Step 2: Identify grouping columns ────────────────────────────────
    grouping_cols = _identify_grouping_columns(input_df, output_df)

    # ── Step 3: Row-count reduction ───────────────────────────────────────
    ratio = _row_count_reduction_ratio(input_df, output_df)
    result["row_reduction_ratio"] = ratio

    # ── Step 4: Numerical verification ───────────────────────────────────
    verification = _verify_aggregation_numerically(
        input_df, output_df, grouping_cols, agg_output_cols
    )

    # ── Step 5: Build aggregate_columns list ──────────────────────────────
    agg_columns_detail: list[dict[str, Any]] = []
    for col in agg_output_cols:
        func = _infer_aggregate_function(col)
        ver = verification.get(col, {})
        agg_columns_detail.append(
            {
                "output_column": col,
                "inferred_function": func,
                "source_column": ver.get("source_column"),
                "verified": ver.get("verified", False),
            }
        )

    result["grouping_columns"] = grouping_cols
    result["aggregate_columns"] = agg_columns_detail

    # ── Step 6: Confidence scoring ────────────────────────────────────────
    score = 0.0

    if agg_output_cols:
        score += 0.35  # aggregate keyword columns present
    if grouping_cols:
        score += 0.20  # grouping columns identified
    if ratio > 0.0:
        score += min(ratio, 0.25)  # up to 0.25 for row reduction
    verified_count = sum(1 for v in verification.values() if v.get("verified"))
    if agg_output_cols:
        score += 0.20 * (verified_count / len(agg_output_cols))

    result["confidence"] = round(min(score, 1.0), 4)
    result["is_aggregation"] = result["confidence"] >= 0.35

    # ── Step 7: Excel feature hints ───────────────────────────────────────
    if result["is_aggregation"]:
        result["excel_features"] = [
            "PivotTable",
            "Power Query → Group By",
            "SUBTOTAL function",
            "Consolidate feature",
        ]
        funcs_used = {d["inferred_function"] for d in agg_columns_detail if d["inferred_function"]}
        if funcs_used:
            result["notes"].append(f"Detected aggregation functions: {', '.join(sorted(funcs_used))}")
        if grouping_cols:
            result["notes"].append(f"GROUP BY columns: {grouping_cols}")
        if ratio > 0:
            result["notes"].append(
                f"Row count reduced from {len(input_df)} → {len(output_df)} "
                f"({ratio * 100:.1f}% reduction)"
            )

    return result