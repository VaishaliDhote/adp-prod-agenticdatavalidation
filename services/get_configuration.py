# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Session -> detect_all detection driver. REUSABLE.
# Rework: retarget merged output to the transformation rule inventory / IR.
# ============================================================================
"""
services/get_configuration.py
================================
GetConfigurationService
------------------------
High-level service that turns a session_id into a fully-populated
phase-config dict by:

  1. Resolving file paths from session_id
       data/input_files/<session_id>/<single_file>   →  df_in
       data/output_files/<session_id>/<single_file>  →  df_out

  2. Building a column_mapping  {input_col → output_col}
       Detected automatically from the rename phase (phase_3_columns).
       Accepts manual overrides via the `extra_column_mapping` argument.

  3. Running phases/main.py  detect_all()  with that mapping and returning
       the merged, production-safe config dict.

  4. Optionally persisting the config to
       data/generated_configs/<session_id>_transformation_config.yaml

Usage
-----
    from services.get_configuration import GetConfigurationService

    svc    = GetConfigurationService()
    result = svc.get_config("160cf6ce-bb3b-4d0d-9d45-18645e791e2f")

    print(result.config)          # full phase config dict
    print(result.column_mapping)  # {input_col: output_col}
    print(result.summary())       # human-readable summary
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import pandas as pd
import yaml

from transformation_phases.main import detect_all
from utils.serializer import make_serializable


# ─────────────────────────────────────────────────────────────────────────────
# Constants – directory layout
# ─────────────────────────────────────────────────────────────────────────────

_ROOT        = Path(__file__).resolve().parent.parent          # project root
_INPUT_DIR   = _ROOT / "data" / "input_files"
_OUTPUT_DIR  = _ROOT / "data" / "output_files"
_CONFIGS_DIR = _ROOT / "data" / "generated_configs"

# Supported tabular file extensions (priority order)
_EXCEL_EXTS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}
_CSV_EXTS   = {".csv", ".tsv"}
_ALL_EXTS   = _EXCEL_EXTS | _CSV_EXTS


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConfigurationResult:
    """Returned by GetConfigurationService.get_config()."""

    session_id:       str
    input_file:       str
    output_file:      str
    sheet_name:       str
    input_shape:      Tuple[int, int]     = (0, 0)
    output_shape:     Tuple[int, int]     = (0, 0)
    column_mapping:   Dict[str, str]      = field(default_factory=dict)
    config:           Dict[str, Any]      = field(default_factory=dict)
    config_saved_to:  Optional[str]       = None
    generated_at:     str                 = field(
        default_factory=lambda: datetime.now().isoformat()
    )
    error:            Optional[str]       = None

    # ── convenience ----------------------------------------------------------

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"  GetConfigurationService  —  session: {self.session_id}",
            "-" * 60,
            f"  Input  : {self.input_file}  {self.input_shape}",
            f"  Output : {self.output_file}  {self.output_shape}",
            f"  Sheet  : {self.sheet_name}",
            f"  Column mapping ({len(self.column_mapping)} renamed columns):",
        ]
        for src, tgt in self.column_mapping.items():
            lines.append(f"      {src!r:30s} → {tgt!r}")
        if not self.column_mapping:
            lines.append("      (none detected)")

        # Phase summary
        phase_summary = self.config.get("metadata", {}).get("phase_summary", {})
        if phase_summary:
            lines.append("")
            lines.append("  Phase summary:")
            for phase, status in phase_summary.items():
                badge = "✅" if status == "active" else "⏭️ "
                lines.append(f"    {badge}  {phase:<28} {status}")

        if self.config_saved_to:
            lines.append("")
            lines.append(f"  Config saved → {self.config_saved_to}")
        if self.error:
            lines.append("")
            lines.append(f"  ⚠️  ERROR: {self.error}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class GetConfigurationService:
    """
    Detects all transformations for a session and returns a
    production-safe config dict.

    Args:
        input_root   : Override default  data/input_files/  directory.
        output_root  : Override default  data/output_files/ directory.
        configs_root : Override default  data/generated_configs/ directory.
        run_formatting : Pass to detect_all(); set False for headless/CSV runs.
        save_config    : Persist YAML to data/generated_configs/ when True.
    """

    def __init__(
        self,
        input_root:      Optional[str] = None,
        output_root:     Optional[str] = None,
        configs_root:    Optional[str] = None,
        run_formatting:  bool = True,
        save_config:     bool = True,
    ) -> None:
        self._input_root     = Path(input_root)   if input_root   else _INPUT_DIR
        self._output_root    = Path(output_root)  if output_root  else _OUTPUT_DIR
        self._configs_root   = Path(configs_root) if configs_root else _CONFIGS_DIR
        self._run_formatting = run_formatting
        self._save_config    = save_config

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def get_config(
        self,
        session_id: str,
        sheet_name: Optional[str] = None,
        extra_column_mapping: Optional[Dict[str, str]] = None,
    ) -> ConfigurationResult:
        """
        Main entry point.

        Args:
            session_id           : Folder name under data/input_files/ and
                                   data/output_files/.
            sheet_name           : Sheet to load.  When None the first sheet
                                   is used automatically.
            extra_column_mapping : Manual renames that are merged ON TOP of
                                   the auto-detected column mapping.
                                   Use when the rename detector misses an edge
                                   case.  Format: {input_col: output_col}.

        Returns:
            ConfigurationResult  (never raises — errors surfaced in .error field).
        """
        result = ConfigurationResult(
            session_id  = session_id,
            input_file  = "",
            output_file = "",
            sheet_name  = sheet_name or "Sheet1",
        )

        try:
            # 1. Resolve file paths
            input_path, output_path = self._resolve_file_paths(session_id)
            result.input_file  = str(input_path)
            result.output_file = str(output_path)

            # 2. Load DataFrames (auto-detect sheet when sheet_name is None)
            df_in, df_out, resolved_sheet = self._load_dataframes(
                input_path, output_path, sheet_name
            )
            result.sheet_name    = resolved_sheet
            result.input_shape   = df_in.shape
            result.output_shape  = df_out.shape

            print(
                f"[GetConfigurationService] Session '{session_id}'\n"
                f"  Input  : {input_path}  {df_in.shape}\n"
                f"  Output : {output_path}  {df_out.shape}\n"
                f"  Sheet  : {resolved_sheet}"
            )

            # 3. Build column_mapping
            column_mapping = self.build_column_mapping(
                df_in, df_out, extra=extra_column_mapping
            )
            result.column_mapping = column_mapping

            if column_mapping:
                print(
                    f"[GetConfigurationService] Column mapping "
                    f"({len(column_mapping)} renames): {column_mapping}"
                )

            # 4. Rename df_in columns to match df_out using the column mapping
            #    so all phases receive DataFrames with aligned column labels.
            #    IMPORTANT: keep a copy of the original BEFORE renaming —
            #    phase_9_final resequence needs the original column names
            #    (e.g. "SL No" = [1..100]) to detect that renumbering is needed.
            df_in_original = df_in.copy()   # ← original: SL No=[1..100], unfiltered

            print(f"\n[GetConfigurationService] 🔄 Aligning df_in columns to df_out...")
            print(f"  Before: {list(df_in.columns)}")
            df_in = df_in.rename(columns=column_mapping)
            print(f"  After:  {list(df_in.columns)}")

            # 5. Run all phase detectors with aligned DataFrames.
            #    Pass the already-computed column_mapping so detect_all skips
            #    its own O(n²) content-match detection (avoids doing it twice).
            print(f"\n[GetConfigurationService] 🔍 Running detect_all() with aligned DataFrames...")
            config = detect_all(
                df_in           = df_in,
                df_out          = df_out,
                column_mapping  = column_mapping,  # already computed — skip re-detection
                run_formatting  = self._run_formatting,
                input_file_path = None,
                df_in_full      = df_in_original,  # ← original unfiltered+unrenamed df_in
            )
            result.config = config

            # 6. Save config to YAML
            if self._save_config:
                saved_path = self._save_yaml(session_id, config)
                result.config_saved_to = saved_path
                print(f"[GetConfigurationService] Config saved → {saved_path}")

            print(f"[GetConfigurationService] ✅ Done for session '{session_id}'")

        except Exception as exc:
            result.error = str(exc)
            print(f"[GetConfigurationService] ❌ Error for session '{session_id}': {exc}")
            import traceback
            traceback.print_exc()

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Column mapping
    # ─────────────────────────────────────────────────────────────────────────

    def build_column_mapping(
        self,
        df_in:  pd.DataFrame,
        df_out: pd.DataFrame,
        extra:  Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Detect column name mappings between input and output DataFrames.

        Strategy:
          - If column sets match exactly by name, map each column to itself.
          - Otherwise, match columns by data content: an input column is mapped
            to an output column when the output column's unique values are a
            subset of the input column's unique values AND the two columns share
            at least one value in their first few rows.

        The optional *extra* dict is merged last so manual overrides always
        win over auto-detection.

        Args:
            df_in  : Input DataFrame.
            df_out : Output DataFrame.
            extra  : Manual {in_col: out_col} overrides (optional).

        Returns:
            Dict[str, str]  — {input_col: output_col} for every detected mapping.
        """
        print("\n🔍 Detecting column mappings...\n")

        if set(df_out.columns) == set(df_in.columns):
            print("✅ Columns match by name")
            mapping = {col: col for col in df_in.columns if col in df_out.columns}
        else:
            print("⚠️ Columns don't match by name, attempting to match by data content...")
            mapping: Dict[str, str] = {}

            for in_col in df_in.columns:
                for out_col in df_out.columns:
                    if out_col in mapping.values():
                        continue
                    try:
                        in_values  = set(df_in[in_col].dropna().astype(str).unique())
                        out_values = set(df_out[out_col].dropna().astype(str).unique())

                        if len(out_values) > 0 and out_values.issubset(in_values):
                            sample_size = min(5, len(df_out))
                            in_sample  = set(df_in[in_col].dropna().astype(str).head(sample_size))
                            out_sample = set(df_out[out_col].dropna().astype(str).head(sample_size))

                            if len(in_sample.intersection(out_sample)) > 0:
                                mapping[in_col] = out_col
                                print(f"  ✓ Matched '{in_col}' → '{out_col}'")
                                break
                    except Exception:
                        continue

        print(f"\n📊 Column Mapping: {mapping}")

        # ── Merge manual overrides ────────────────────────────────────────
        if extra:
            mapping.update(extra)

        return mapping

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_file_paths(self, session_id: str) -> Tuple[Path, Path]:
        """
        Locate the single input file and single output file for a session.

        Each session folder is expected to contain exactly ONE tabular file.
        Raises FileNotFoundError when the folder or file is missing.
        """
        in_dir  = self._input_root  / session_id
        out_dir = self._output_root / session_id

        if not in_dir.exists():
            raise FileNotFoundError(
                f"Input folder not found: {in_dir}\n"
                f"  (looked for session_id='{session_id}' under {self._input_root})"
            )
        if not out_dir.exists():
            raise FileNotFoundError(
                f"Output folder not found: {out_dir}\n"
                f"  (looked for session_id='{session_id}' under {self._output_root})"
            )

        input_file  = self._pick_single_file(in_dir,  label="input")
        output_file = self._pick_single_file(out_dir, label="output")

        return input_file, output_file

    def _pick_single_file(self, folder: Path, label: str) -> Path:
        """
        Return the single tabular file in *folder*.

        Preference order: Excel files → CSV files.
        Raises if no file or more than one file is found.
        """
        candidates = [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in _ALL_EXTS
        ]

        if not candidates:
            raise FileNotFoundError(
                f"No tabular file (.xlsx/.xls/.csv …) found in {label} folder: {folder}"
            )

        if len(candidates) == 1:
            return candidates[0]

        # Multiple files: prefer Excel over CSV
        excel_candidates = [p for p in candidates if p.suffix.lower() in _EXCEL_EXTS]
        if len(excel_candidates) == 1:
            return excel_candidates[0]

        # Still ambiguous — pick alphabetically first for determinism
        candidates.sort(key=lambda p: p.name.lower())
        print(
            f"[GetConfigurationService] ⚠️  Multiple files in {label} folder {folder}. "
            f"Using: '{candidates[0].name}'. Pass a specific path to override."
        )
        return candidates[0]

    def _load_dataframes(
        self,
        input_path:  Path,
        output_path: Path,
        sheet_name:  Optional[str],
    ) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
        """
        Load df_in and df_out.  Auto-resolves the sheet name:
          - If sheet_name is explicitly provided, use it for both files.
          - Otherwise, use the first sheet of the input file and the first
            sheet of the output file independently.

        Returns:
            (df_in, df_out, resolved_sheet_name)
        """
        df_in,  in_sheet  = self._read_file(input_path,  sheet_name)
        df_out, _out_sheet = self._read_file(output_path, sheet_name)

        # Resolved sheet label: prefer the caller-supplied name, else the
        # name actually found in the input file.
        resolved = sheet_name if sheet_name else in_sheet

        return df_in, df_out, resolved

    def _read_file(
        self,
        path: Path,
        sheet_name: Optional[str],
    ) -> Tuple[pd.DataFrame, str]:
        """
        Read a single tabular file (Excel or CSV) into a DataFrame.

        Returns:
            (DataFrame, actual_sheet_name_used)
        """
        suffix = path.suffix.lower()

        if suffix in _EXCEL_EXTS:
            # Determine which sheet to load
            xl     = pd.ExcelFile(str(path))
            sheets = xl.sheet_names

            if sheet_name and sheet_name in sheets:
                actual_sheet = sheet_name
            elif sheet_name:
                # Caller specified a sheet that doesn't exist — try first sheet
                print(
                    f"[GetConfigurationService] ⚠️  Sheet '{sheet_name}' not found in "
                    f"'{path.name}'. Using first sheet: '{sheets[0]}'."
                )
                actual_sheet = sheets[0]

            else:
                actual_sheet = sheets[0]

            df = pd.read_excel(str(path), sheet_name=actual_sheet)
            return df, actual_sheet

        elif suffix in _CSV_EXTS:
            sep = "\t" if suffix == ".tsv" else ","
            df  = pd.read_csv(str(path), sep=sep)
            return df, "Sheet1"   # virtual sheet name for CSVs

        else:
            raise ValueError(
                f"Unsupported file type '{suffix}' for file: {path}"
            )

    @staticmethod
    def _dtypes_compatible(s_in: pd.Series, s_out: pd.Series) -> bool:
        """
        Return True when the two series have broadly compatible dtypes.
        Prevents positional / similarity matches between e.g. an int column
        and a datetime column.
        """
        def _kind(s: pd.Series) -> str:
            if pd.api.types.is_bool_dtype(s):
                return "bool"
            if pd.api.types.is_datetime64_any_dtype(s):
                return "datetime"
            if pd.api.types.is_numeric_dtype(s):
                return "numeric"
            return "string"

        return _kind(s_in) == _kind(s_out)

    def _save_yaml(self, session_id: str, config: Dict[str, Any]) -> str:
        """
        Persist the config dict to
            data/generated_configs/<session_id>_transformation_config.yaml

        Returns the absolute path of the saved file.
        """
        self._configs_root.mkdir(parents=True, exist_ok=True)

        filename  = f"{session_id}_transformation_config.yaml"
        save_path = self._configs_root / filename

        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(
                make_serializable(config),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        return str(save_path)
