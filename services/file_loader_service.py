# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 1 - File Ingestion & Metadata Extraction. REUSABLE.
# Rework: extract column positions/order + data-type & format detection; remove
# the dead LLM sheet-summary placeholder (sheet_summary="summary").
# ============================================================================
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from states.filestate import FileState, FileMeta, SheetMeta, ColumnMeta, ColumnStats
from services.llm_service import LLMService

DATA_ROOT = Path("data")
INPUT_ROOT_NAME = "input_files"
OUTPUT_ROOT_NAME = "output_files"
llm_service=LLMService()

async def _build_sheet_meta(df: pd.DataFrame,sheet_name:str, df_ref: str | None = None) -> SheetMeta:
    """Create SheetMeta from a pandas DataFrame.

    This computes row_count and basic column statistics. It is generic
    enough to work for both input and output Excel sheets.
    """
    
    
    columns: Dict[str, ColumnMeta] = {}

    for col in df.columns:
        series = df[col]
        columns[col] = ColumnMeta(
            name=str(col),
            dtype=str(series.dtype),
            stats=ColumnStats(
                non_null_count=int(series.notna().sum()),
                unique_count=int(series.nunique(dropna=True)),
            ),
        )
    
    sheet_meta= SheetMeta(
        df_ref=df_ref,
        row_count=int(len(df)),
        columns=columns,
    )
    # summary = await llm_service.sheet_summary_generator(sheet_name, sheet_meta,df)
    # print('**'*20) 
    # print('summary....',summary)
    # print('**'*20)
    sheet_meta["sheet_summary"]="summary"    
   
    # print('**'*20)
    # print(sheet_meta)
    # print('**'*20)
    return sheet_meta


async def _load_excel_file(file_path: Path) -> Dict[str, SheetMeta]:
    """Read an Excel file and return sheet metadata keyed by sheet name."""
    print('**'*20)
    print("inside load excel file")
    # read all sheets
    xls = pd.read_excel(file_path, sheet_name=None, parse_dates=True)
    sheets: Dict[str, SheetMeta] = {}

    # for sheet_name, df in xls.items():        
    #     meta = await _build_sheet_meta(df,sheet_name)
    #     # print('**'*20)
    #     # print(meta)       
    #     sheets[str(sheet_name)] = meta
        # print('**'*20)
        # print("sheet..",sheets[str(sheet_name)])

    
    # print('**'*20, sheets)

    return sheets


async def build_file_state_for_session(session_id: str) -> FileState:
    """Build FileState for a single session.

    The folder layout is assumed to be:
    data/
    input_files/<session_id>/*.xlsx
    output_files/<session_id>/*.xlsx

    The number of files is dynamic. Any Excel file found is loaded and
    its sheets/columns are summarized into the JSON-of-JSON FileState.
    """

    input_dir = DATA_ROOT / INPUT_ROOT_NAME / session_id
    output_dir = DATA_ROOT / OUTPUT_ROOT_NAME / session_id

    state: FileState = {"input_files": {}, "output_files": {}}
    print("***"*40)
    # print('input_dir ...',input_dir)
    # print('output_dir ...',output_dir) 
    # print("***"*40)
    # Handle input files
    if input_dir.is_dir():
        print("inside input directory")
        for path in sorted(input_dir.glob("*.xlsx")):
            try:
                print("_______________________"*10)
                print("path..",path)
                # sheets = await _load_excel_file(path)
                # print(sheets)
                print("_______________________"*10)
                state["input_files"][path.name] = FileMeta(
                    path=str(path),
                    io_type="input",
                    sheets="sheets",
                )
            except Exception as e:
                # If one file fails, continue with others; caller can log/handle.
                print("Exception occured in input dir parsing...",str(e))
                continue
    else:
        print(input_dir.is_dir())
    # Handle output files
    if output_dir.is_dir():
        print("inside output_dir directory")
        for path in sorted(output_dir.glob("*.xlsx")):
            try:
                # sheets = await _load_excel_file(path)
                state["output_files"][path.name] = FileMeta(
                    path=str(path),
                    io_type="output",
                    sheets="sheets",
                )
            except Exception as e:
                # If one file fails, continue with others; caller can log/handle.
                print("Exception occured in output dir parsing...",str(e))
                continue
    print("***"*40)
    # print('file state at the return ...',state)
    print("***"*40)            
    return state
