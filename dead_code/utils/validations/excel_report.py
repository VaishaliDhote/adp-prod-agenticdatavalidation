"""
utils/excel_report.py
======================
Helpers for generating Excel reports from pipeline results.
Extracted from main.py so that service classes can use them independently.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill


def build_validation_error_excel(
    mismatched_cols: list[dict],
    execution_id: str,
    output_dir: Path,
) -> Path:
    """
    Build an Excel report of validation mismatches.

    Columns:
        A  Field with error
        B  Field Value       (the rule key that failed, e.g. dataType)
        C  Error Message

    Returns the path to the saved .xlsx file.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Validation Errors"

    # ── Header row ────────────────────────────────────────────────────────────
    headers     = ["Field with error", "Field Value", "Error Message"]
    header_fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill      = header_fill
        cell.font      = header_font
        cell.alignment = Alignment(horizontal="left", vertical="center")

    # ── Data rows ─────────────────────────────────────────────────────────────
    for row_idx, mismatch in enumerate(mismatched_cols, start=2):
        column_name = mismatch.get("column", "")
        rule_key    = mismatch.get("rule", "")
        actual      = mismatch.get("actual", "")

        if rule_key == "column_exists":
            field_value   = "—"
            error_message = "Provided value is not a valid value"
        else:
            field_value   = str(actual) if actual is not None else ""
            error_message = "Provided value is not a valid value"

        ws.cell(row=row_idx, column=1, value=column_name)
        ws.cell(row=row_idx, column=2, value=field_value)
        ws.cell(row=row_idx, column=3, value=error_message)

    # ── Column widths ─────────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 40

    # ── Save ─────────────────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"validation_errors_{execution_id}.xlsx"
    wb.save(file_path)
    return file_path
