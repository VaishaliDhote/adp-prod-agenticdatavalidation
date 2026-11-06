"""
execution/execution_phase_9_formatting.py
==========================================
PHASE 9 — Formatting & Excel Features
---------------------------------------
Applies visual / Excel-specific formatting AFTER all data transformations.
These operations do NOT change data values — only presentation.

Operations (in order):
  36. Number / cell formatting      ← config["phase_8_formatting"]["formatting"]
  37. Conditional formatting        ← config["phase_8_formatting"]["conditional_formatting"]
  38. Data validation               ← config["phase_8_formatting"]["data_validation"]
  39. Comments / annotations        ← config["phase_8_formatting"]["comments"]
  40. Charts                        ← config["phase_8_formatting"]["charts"]

All formatting is applied via openpyxl onto an in-memory Workbook.
The DataFrame is written to the workbook first, then formatting is layered on.

Config key : "phase_8_formatting"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from utils.execution_helpers import col_name

try:
    import openpyxl
    from openpyxl import Workbook
    from openpyxl.styles import (
        Font, PatternFill, Alignment, Border, Side, numbers
    )
    from openpyxl.formatting.rule import (
        ColorScaleRule, DataBarRule, IconSetRule, CellIsRule, FormulaRule
    )
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.worksheet.datavalidation import DataValidation
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    config: Dict[str, Any],
    workbook: Optional["Workbook"] = None,
    sheet_name: str = "Sheet1",
) -> Tuple[pd.DataFrame, Optional["Workbook"]]:
    """
    Apply formatting to the DataFrame / Workbook.

    Args:
        df         : Finalized DataFrame from Phase 8.
        config     : Full merged config dict.
        workbook   : Existing openpyxl Workbook to format, or None to create one.
        sheet_name : Target worksheet name.

    Returns:
        (df, workbook) — DataFrame unchanged; workbook has formatting applied.
        workbook is None when openpyxl is not available.
    """
    phase = config.get("phase_8_formatting", {})

    if not phase:
        print("[Phase 9] No formatting config found — skipping.")
        return df, workbook

    if not _OPENPYXL_AVAILABLE:
        print("[Phase 9] openpyxl not available — formatting skipped (data intact).")
        return df, None

    # Column mapping: input_name → output_name (from metadata)
    col_mapping: Dict[str, str] = config.get("metadata", {}).get("column_mapping", {})

    # Write DataFrame into workbook
    if workbook is None:
        workbook = Workbook()
        ws = workbook.active
        ws.title = sheet_name
    else:
        ws = workbook[sheet_name] if sheet_name in workbook.sheetnames else workbook.active

    # Write headers + data
    _write_df_to_ws(df, ws)

    # ── 36. Number / cell formatting ─────────────────────────────────────────
    fmt_cfg = phase.get("formatting", {})
    if fmt_cfg.get("applied"):
        _apply_formatting(ws, df, fmt_cfg, col_mapping)

    # ── 37. Conditional formatting ────────────────────────────────────────────
    cf_cfg = phase.get("conditional_formatting", {})
    if cf_cfg.get("applied"):
        _apply_conditional_formatting(ws, df, cf_cfg, col_mapping)

    # ── 38. Data validation ───────────────────────────────────────────────────
    dv_cfg = phase.get("data_validation", {})
    if dv_cfg.get("applied"):
        _apply_data_validation(ws, df, dv_cfg, col_mapping)

    # ── 39. Comments ──────────────────────────────────────────────────────────
    comments_cfg = phase.get("comments", {})
    if comments_cfg.get("applied"):
        _apply_comments(ws, df, comments_cfg, col_mapping)

    # ── 40. Charts ────────────────────────────────────────────────────────────
    charts_cfg = phase.get("charts", {})
    if charts_cfg.get("applied"):
        _apply_charts(ws, df, charts_cfg, col_mapping)

    print(f"[Phase 9] Formatting complete on sheet '{ws.title}'.")
    return df, workbook


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_col(
    col: str,
    df: pd.DataFrame,
    col_mapping: Dict[str, str],
) -> Optional[str]:
    """
    Resolve a column name (which may be an input-namespace or output-namespace
    name) to an actual column present in df.

    Resolution order:
      1. Exact match in df.columns
      2. Lookup via col_mapping values (output names) — col IS an output name
      3. Lookup via col_mapping keys  (input names)  — translate to output name
      4. Case-insensitive match against df.columns
    """
    if col in df.columns:
        return col
    # col might be an input name — translate to output name via mapping
    if col in col_mapping and col_mapping[col] in df.columns:
        return col_mapping[col]
    # Case-insensitive fallback
    lower_map = {c.lower(): c for c in df.columns}
    return lower_map.get(col.lower())


def _col_letter(
    df: pd.DataFrame,
    col: str,
    col_mapping: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return the Excel column letter for a DataFrame column, resolving via mapping."""
    resolved = _resolve_col(col, df, col_mapping or {})
    if resolved is None:
        return None
    idx = list(df.columns).index(resolved) + 1   # 1-based
    return get_column_letter(idx)


def _write_df_to_ws(df: pd.DataFrame, ws: Any) -> None:
    """Write DataFrame headers and data rows into an openpyxl worksheet."""
    import numpy as np
    from datetime import datetime
    
    # Headers
    for col_idx, col_name in enumerate(df.columns, start=1):
        ws.cell(row=1, column=col_idx, value=str(col_name))

    # Data rows
    for row_idx, row in enumerate(df.itertuples(index=False), start=2):
        for col_idx, value in enumerate(row, start=1):
            # Convert pandas NA / NaT to None for openpyxl
            if pd.isna(value) if not isinstance(value, (list, dict)) else False:
                value = None
            # Convert pandas Timestamp / numpy datetime64 to native Python datetime
            # to prevent openpyxl from converting to numeric timestamp
            elif isinstance(value, pd.Timestamp):
                value = value.to_pydatetime()
            elif isinstance(value, np.datetime64):
                value = pd.Timestamp(value).to_pydatetime()
            ws.cell(row=row_idx, column=col_idx, value=value)


# ── 36. Number / cell formatting ─────────────────────────────────────────────

# Common Excel number format codes
_FORMAT_CODE_MAP: Dict[str, str] = {
    "currency":   '"$"#,##0.00',
    "percentage": "0.00%",
    "date":       "YYYY-MM-DD",
    "datetime":   "YYYY-MM-DD HH:MM:SS",
    "integer":    "#,##0",
    "float":      "#,##0.00",
    "text":       "@",
    "accounting": '_("$"* #,##0.00_);_("$"* (#,##0.00);_("$"* "-"??_);_(@_)',
}


def _apply_formatting(ws: Any, df: pd.DataFrame, cfg: Dict[str, Any], col_mapping: Dict[str, str]) -> None:
    columns: List[Dict] = cfg.get("details", {}).get("columns", [])
    n_rows = len(df) + 1   # +1 for header row

    for col_cfg in columns:
        col    = col_name(col_cfg.get("column", col_cfg))
        fmt    = col_cfg.get("format", "")
        letter = _col_letter(df, col, col_mapping)
        if not letter:
            continue

        # Resolve format code: explicit excel_format_code wins, then map lookup
        format_code = (
            col_cfg.get("excel_format_code")
            or _FORMAT_CODE_MAP.get(str(fmt).lower(), "")
        )

        for row in range(2, n_rows + 1):
            cell = ws[f"{letter}{row}"]
            if format_code:
                cell.number_format = format_code

    # Bold header row
    for col_idx in range(1, len(df.columns) + 1):
        ws.cell(row=1, column=col_idx).font = Font(bold=True)

    print(f"  [Phase 9] Number formatting applied to {len(columns)} columns.")


# ── 37. Conditional formatting ────────────────────────────────────────────────

def _apply_conditional_formatting(ws: Any, df: pd.DataFrame, cfg: Dict[str, Any], col_mapping: Dict[str, str]) -> None:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    n_rows = len(df) + 1

    for rule in rules:
        col    = col_name(rule.get("column", ""))
        letter = _col_letter(df, col, col_mapping)
        if not letter:
            continue

        cell_range = f"{letter}2:{letter}{n_rows}"
        rule_type  = str(rule.get("rule_type", "cell_value")).lower()
        operator   = rule.get("operator", "greaterThan")
        value      = rule.get("value", 0)
        color      = rule.get("color", "FFFF00")   # default yellow

        fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

        try:
            if rule_type in ("cell_value", "cell_is"):
                ws.conditional_formatting.add(
                    cell_range,
                    CellIsRule(operator=operator, formula=[str(value)], fill=fill),
                )
            elif rule_type == "color_scale":
                ws.conditional_formatting.add(
                    cell_range,
                    ColorScaleRule(
                        start_type="min",  start_color="FF0000",
                        mid_type="percentile", mid_value=50, mid_color="FFFF00",
                        end_type="max",    end_color="00FF00",
                    ),
                )
            elif rule_type == "data_bar":
                ws.conditional_formatting.add(
                    cell_range,
                    DataBarRule(start_type="min", end_type="max", color="638EC6"),
                )
            print(f"  [Phase 9] Conditional format on '{col}' ({rule_type})")
        except Exception as exc:
            print(f"  [Phase 9] Conditional format failed for '{col}': {exc}")


# ── 38. Data validation ───────────────────────────────────────────────────────

def _apply_data_validation(ws: Any, df: pd.DataFrame, cfg: Dict[str, Any], col_mapping: Dict[str, str]) -> None:
    rules: List[Dict] = cfg.get("details", {}).get("rules", [])
    n_rows = len(df) + 1

    for rule in rules:
        col       = col_name(rule.get("column", ""))
        letter    = _col_letter(df, col, col_mapping)
        if not letter:
            continue

        cell_range  = f"{letter}2:{letter}{n_rows}"
        val_type    = rule.get("type", "list")
        formula1    = rule.get("formula1", "")
        formula2    = rule.get("formula2", "")
        error_msg   = rule.get("error_message", "Invalid value")
        prompt_msg  = rule.get("prompt_message", "")

        try:
            dv = DataValidation(
                type=val_type,
                formula1=str(formula1) if formula1 else None,
                formula2=str(formula2) if formula2 else None,
                allow_blank=rule.get("allow_blank", True),
                showErrorMessage=True,
                error=error_msg,
                errorTitle="Validation Error",
                showInputMessage=bool(prompt_msg),
                prompt=prompt_msg or None,
                promptTitle="Input" if prompt_msg else None,
            )
            ws.add_data_validation(dv)
            dv.add(cell_range)
            print(f"  [Phase 9] Data validation ({val_type}) on '{col}'")
        except Exception as exc:
            print(f"  [Phase 9] Data validation failed for '{col}': {exc}")


# ── 39. Comments ──────────────────────────────────────────────────────────────

def _apply_comments(ws: Any, df: pd.DataFrame, cfg: Dict[str, Any], col_mapping: Dict[str, str]) -> None:
    try:
        from openpyxl.comments import Comment
    except ImportError:
        print("  [Phase 9] Comments skipped: openpyxl.comments not available.")
        return

    columns: List[Any] = cfg.get("details", {}).get("columns", [])
    for col_ref in columns:
        col    = col_name(col_ref)
        letter = _col_letter(df, col, col_mapping)
        if not letter:
            continue
        comment_text = (
            col_ref.get("comment", f"Column: {col}")
            if isinstance(col_ref, dict) else f"Column: {col}"
        )
        try:
            cell = ws[f"{letter}1"]
            cell.comment = Comment(comment_text, "System")
            print(f"  [Phase 9] Comment added to header '{col}'")
        except Exception as exc:
            print(f"  [Phase 9] Comment failed for '{col}': {exc}")


# ── 40. Charts ────────────────────────────────────────────────────────────────

_CHART_TYPE_MAP = {
    "bar":    BarChart,
    "column": BarChart,
    "line":   LineChart,
    "pie":    PieChart,
}


def _apply_charts(ws: Any, df: pd.DataFrame, cfg: Dict[str, Any], col_mapping: Dict[str, str]) -> None:
    details      = cfg.get("details", {})
    chart_type   = str(details.get("chart_type", "bar")).lower()
    cat_col      = col_name(details.get("category_column", ""))
    val_cols_raw = details.get("value_columns", [])
    n_rows       = len(df) + 1   # includes header

    ChartClass = _CHART_TYPE_MAP.get(chart_type, BarChart)

    try:
        chart = ChartClass()
        chart.title  = details.get("title", "Chart")
        chart.style  = 10

        # Categories
        if cat_col and cat_col in df.columns:
            cat_letter = _col_letter(df, cat_col, col_mapping)
            cats = Reference(ws, min_col=list(df.columns).index(cat_col) + 1,
                             min_row=2, max_row=n_rows)
            chart.set_categories(cats)

        # Value series
        val_cols = [col_name(c) for c in (val_cols_raw if val_cols_raw else [])]
        if not val_cols:
            # Default: all numeric columns
            val_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

        for vc in val_cols:
            if vc not in df.columns:
                continue
            col_idx = list(df.columns).index(vc) + 1
            data = Reference(ws, min_col=col_idx, min_row=1, max_row=n_rows)
            chart.add_data(data, titles_from_data=True)

        # Place chart below data
        anchor_row = n_rows + 2
        ws.add_chart(chart, f"A{anchor_row}")
        print(f"  [Phase 9] {chart_type.title()} chart added at row {anchor_row}.")

    except Exception as exc:
        print(f"  [Phase 9] Chart creation failed: {exc}")
