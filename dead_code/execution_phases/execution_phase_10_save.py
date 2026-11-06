"""
execution/execution_phase_10_save.py
======================================
PHASE 10 — Save
-----------------
Writes the final output to disk.

  41. Save file  ← config["phase_9_final"]["final_save"]

Supports:
  • xlsx / xlsm  (openpyxl)
  • csv / tsv    (pandas)
  • parquet / json / html (pandas)

Also applies final sheet-level settings when saving to Excel:
  • freeze_panes   (row 1 frozen by default)
  • locked_header  (bold + background color)
  • print_area     (sets ws.print_area)

Config key : "phase_9_final"  →  "final_save"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    config: Dict[str, Any],
    output_path: str,
    workbook: Optional["Workbook"] = None,
    sheet_name: str = "Sheet1",
) -> str:
    """
    Save the final DataFrame (and optional pre-built workbook) to disk.

    Args:
        df          : Final DataFrame from Phase 8/9.
        config      : Full merged config dict.
        output_path : Destination file path (extension determines format).
        workbook    : Pre-built openpyxl Workbook from Phase 9, or None.
        sheet_name  : Worksheet name when writing Excel.

    Returns:
        Resolved absolute path of the saved file.
    """
    save_cfg   = config.get("phase_9_final", {}).get("final_save", {})
    details    = save_cfg.get("details", {})

    # Determine format: config wins, then fall back to file extension
    cfg_format = str(details.get("format", "")).lower()
    ext        = Path(output_path).suffix.lower().lstrip(".")
    fmt        = cfg_format or ext or "xlsx"

    freeze     = details.get("freeze_panes",  True)
    lock_hdr   = details.get("locked_header", False)
    print_area = details.get("print_area",    False)

    # Ensure output directory exists
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Apply final output column names from metadata.column_mapping ──────────
    # Phase 3 already applies residual renames, but the workbook passed in from
    # Phase 9 may have been built BEFORE those renames if Phase 9 ran on an
    # older df snapshot.  We always rename the df here so both the fresh-workbook
    # path AND the reused-workbook path get the correct output header names.
    df = _apply_output_column_names(df, config)

    # ── Excel formats ─────────────────────────────────────────────────────────
    if fmt in ("xlsx", "xlsm", "xls", "xlsb"):
        _save_excel(df, out_path, workbook, sheet_name, freeze, lock_hdr, print_area, fmt)

    # ── CSV / TSV ─────────────────────────────────────────────────────────────
    elif fmt == "csv":
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"[Phase 10] Saved CSV → {out_path}")

    elif fmt == "tsv":
        df.to_csv(out_path, index=False, sep="\t", encoding="utf-8-sig")
        print(f"[Phase 10] Saved TSV → {out_path}")

    # ── Parquet ───────────────────────────────────────────────────────────────
    elif fmt == "parquet":
        df.to_parquet(out_path, index=False)
        print(f"[Phase 10] Saved Parquet → {out_path}")

    # ── JSON ──────────────────────────────────────────────────────────────────
    elif fmt == "json":
        df.to_json(out_path, orient="records", indent=2, force_ascii=False)
        print(f"[Phase 10] Saved JSON → {out_path}")

    # ── HTML ──────────────────────────────────────────────────────────────────
    elif fmt == "html":
        df.to_html(out_path, index=False)
        print(f"[Phase 10] Saved HTML → {out_path}")

    # ── Fallback: xlsx ────────────────────────────────────────────────────────
    else:
        _save_excel(df, out_path, workbook, sheet_name, freeze, lock_hdr, print_area, "xlsx")

    return str(out_path.resolve())


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_output_column_names(
    df: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Rename any remaining input-namespace column headers to their output-namespace
    equivalents using metadata.column_mapping before the file is written.

    This is the FINAL safety net — it ensures the saved file always has the
    correct output column names even if:
      • Phase 3 rename_columns.applied was False  (rename not detected as an op)
      • Phase 3 residual mapping was skipped       (column already had output name)
      • The workbook passed from Phase 9 was built before Phase 3 renames ran

    Rules applied here:
      • Only renames columns that still exist under their INPUT name in df
      • Skips identity mappings  (input_name == output_name)
      • Skips if the target output name is already present in df  (avoid clash)
      • Logs every rename so the pipeline output is fully traceable
    """
    # column_mapping is {output_col: input_col} (lineage direction).
    # Phase 3 residual should have already applied the renames; this is a
    # safety net using the same correct direction: rename input_col->output_col.
    col_mapping: Dict[str, str] = config.get("metadata", {}).get("column_mapping", {})
    if not col_mapping:
        return df

    rename_map: Dict[str, str] = {}
    for out_col, in_col in col_mapping.items():  # out_col=key, in_col=value
        if out_col == in_col:
            continue                            # identity — nothing to do
        if in_col not in df.columns:
            continue                            # already renamed upstream
        if out_col in df.columns and out_col != in_col:
            print(f"  [Phase 10] Header rename skipped: "
                  f"'{in_col}' -> '{out_col}' (target already exists)")
            continue
        rename_map[in_col] = out_col

    if rename_map:
        df = df.rename(columns=rename_map)
        for old, new in rename_map.items():
            print(f"  [Phase 10] Final header rename: '{old}' -> '{new}'")
    else:
        print("  [Phase 10] All column headers already match output names.")

    return df


def _save_excel(
    df: pd.DataFrame,
    out_path: Path,
    workbook: Optional["Workbook"],
    sheet_name: str,
    freeze: bool,
    lock_hdr: bool,
    print_area: bool,
    fmt: str,
) -> None:
    """Write to Excel using openpyxl (preferred) or pandas ExcelWriter fallback."""

    if _OPENPYXL_AVAILABLE:
        _save_excel_openpyxl(df, out_path, workbook, sheet_name, freeze, lock_hdr, print_area, fmt)
    else:
        # Fallback: plain pandas to_excel (no formatting)
        df.to_excel(str(out_path), index=False, sheet_name=sheet_name)
        print(f"[Phase 10] Saved Excel (no formatting) → {out_path}")


def _save_excel_openpyxl(
    df: pd.DataFrame,
    out_path: Path,
    workbook: Optional["Workbook"],
    sheet_name: str,
    freeze: bool,
    lock_hdr: bool,
    print_area: bool,
    fmt: str,
) -> None:
    """Save using openpyxl, applying freeze panes, header lock, and print area."""

    # If Phase 9 already built a workbook, we must re-write its header row
    # because Phase 10 may have just applied final renames to df.columns.
    # We always create a fresh workbook from the (now correctly named) df
    # to guarantee header/data consistency.
    if workbook is not None and sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
        # Overwrite header row cells with the final (renamed) column names
        for col_idx, col_name in enumerate(df.columns, start=1):
            ws.cell(row=1, column=col_idx, value=str(col_name))
        print(f"  [Phase 10] Updated header row in existing workbook with output names.")
    else:
        workbook = Workbook()
        ws = workbook.active
        ws.title = sheet_name
        _write_df_to_ws(df, ws)

    n_cols = len(df.columns)
    n_rows = len(df) + 1   # +1 for header row

    # ── Freeze panes (freeze row 1 = header) ──────────────────────────────────
    if freeze:
        ws.freeze_panes = "A2"
        print(f"  [Phase 10] Freeze panes set at A2.")

    # ── Locked header (bold + light blue background) ──────────────────────────
    if lock_hdr:
        header_fill = PatternFill(start_color="BDD7EE", end_color="BDD7EE",
                                  fill_type="solid")
        header_font = Font(bold=True, color="000000")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for col_idx in range(1, n_cols + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill      = header_fill
            cell.font      = header_font
            cell.alignment = header_align
        print(f"  [Phase 10] Header row styled (bold + blue fill).")

    # ── Auto column widths ────────────────────────────────────────────────────
    # Use positional iloc to avoid issues with duplicate column names returning
    # a DataFrame instead of a Series when indexed by label.
    for col_idx, col_name in enumerate(df.columns, start=1):
        col_series = df.iloc[:, col_idx - 1]
        if isinstance(col_series, pd.DataFrame):
            # Shouldn't happen with iloc, but guard just in case
            col_series = col_series.iloc[:, 0]
        max_len = max(
            len(str(col_name)),
            col_series.astype(str).str.len().max() if len(df) > 0 else 0,
        )
        adjusted = min(max_len + 4, 50)   # cap at 50 chars
        ws.column_dimensions[get_column_letter(col_idx)].width = adjusted

    # ── Print area ────────────────────────────────────────────────────────────
    if print_area:
        last_col = get_column_letter(n_cols)
        ws.print_area = f"A1:{last_col}{n_rows}"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        print(f"  [Phase 10] Print area set: A1:{last_col}{n_rows}")

    # ── Sheet tab color (subtle visual marker) ────────────────────────────────
    ws.sheet_properties.tabColor = "4472C4"

    # Save
    save_path = str(out_path)
    if not save_path.endswith(f".{fmt}"):
        save_path = str(out_path.with_suffix(f".{fmt}"))

    workbook.save(save_path)
    print(f"[Phase 10] Saved Excel ({fmt.upper()}) → {save_path}")


def _write_df_to_ws(df: pd.DataFrame, ws: Any) -> None:
    """Write DataFrame headers and data rows into an openpyxl worksheet."""
    import numpy as np
    from datetime import datetime
    
    for col_idx, col_name in enumerate(df.columns, start=1):
        ws.cell(row=1, column=col_idx, value=str(col_name))

    for row_idx, row in enumerate(df.itertuples(index=False), start=2):
        for col_idx, value in enumerate(row, start=1):
            try:
                if pd.isna(value):
                    value = None
                # Convert pandas Timestamp / numpy datetime64 to native Python datetime
                # to prevent openpyxl from converting to numeric timestamp
                elif isinstance(value, pd.Timestamp):
                    value = value.to_pydatetime()
                elif isinstance(value, np.datetime64):
                    value = pd.Timestamp(value).to_pydatetime()
            except (TypeError, ValueError):
                pass
            ws.cell(row=row_idx, column=col_idx, value=value)
