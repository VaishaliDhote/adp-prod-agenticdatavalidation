# ============================================================================
# Deliverable 8 - Existing API Execution & Development Accuracy Loop (Sprint 8).
# Produced-vs-known output comparison (tasks 9, 10, 11) - manual-drop workflow.
#
# The existing backend has no programmatic "download produced output" endpoint;
# after a real execution it writes the produced output file to a network share
# (BluePrism/CCS UAT MyForms). The operator navigates there, downloads the file,
# and pastes it into this project's drop folder (``data/executed_outputs``). This
# module then loads that produced file and the known/expected output file, aligns
# them by header + row, and diffs them cell-by-cell into an
# :class:`~accuracy_loop.models.OutputComparisonReport` whose cell-level match is
# folded into the loop's accuracy (task 16).
# ============================================================================
"""
accuracy_loop.output_comparison
==============================
Load and diff produced vs known output files (Deliverable 8 tasks 9-11).

Public surface:

* :class:`OutputLocations` - resolves the network share directory (where the
  backend writes outputs), the local *drop* folder (where the operator pastes the
  downloaded produced file), and the *expected* folder (the known outputs);
  best-effort creates the network directory.
* :func:`load_tabular` - read an ``.xlsx``/``.xls``/``.csv``/``.tsv`` file into a
  normalised :class:`TabularData` (header + rows), reusing pandas.
* :func:`compare_tabular` / :func:`compare_output_files` - align by header + row
  and diff cell-by-cell into an :class:`OutputComparisonReport` + a list of
  ``OUTPUT``-kind :class:`~accuracy_loop.models.Mismatch` records.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from accuracy_loop.models import (
    Mismatch,
    MismatchKind,
    OutputCellDiff,
    OutputComparisonReport,
)

logger = logging.getLogger(__name__)

# Supported produced/known output file formats (reuse of Deliverable 1 parsers'
# dependency stack: pandas + openpyxl).
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
_CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
_SUPPORTED_SUFFIXES = _EXCEL_SUFFIXES | _CSV_SUFFIXES

# The network share the backend writes produced outputs to (operator-facing).
# Overridable via EXECUTION_OUTPUT_NETWORK_DIR (a UNC path or a file:// URI).
_DEFAULT_NETWORK_URI = "file://fs01.ad.esi.adp.com/adpfiles/ESD/BluePrism/CCS/UAT/MyForms/"
_DEFAULT_NETWORK_SUBDIR = "AgenticDataValidation"

# Cap on the number of individual cell diffs materialised into a report so a
# wildly-wrong output cannot produce an unbounded payload. The counts remain
# exact; only the per-cell ``cell_diffs`` list is truncated.
_MAX_CELL_DIFFS = 500

_OUTPUT_MISMATCH_CODE = "OUTPUT_CELL_MISMATCH"
_MISSING_COLUMN_CODE = "OUTPUT_MISSING_COLUMN"
_MISSING_ROW_CODE = "OUTPUT_MISSING_ROW"


class OutputFileError(RuntimeError):
    """Raised when a produced/known output file cannot be loaded or compared."""


class OutputFileNotFound(OutputFileError):
    """Raised when a produced/known output file cannot be located on disk."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def uri_to_local_path(uri_or_path: str) -> str:
    """Convert a ``file://`` URI to a local or UNC filesystem path.

    Handles the two shapes that appear in this project:

    * ``file://host/share/dir``      -> ``\\\\host\\share\\dir`` (UNC network share)
    * ``file:///C:/dir`` / ``file:///dir`` -> ``C:/dir`` / ``/dir`` (local)

    A value that is already a plain path is returned unchanged.
    """
    s = (uri_or_path or "").strip()
    if not s.lower().startswith("file://"):
        return s
    rest = unquote(s[len("file://"):])
    if rest.startswith("/"):
        # file:///C:/path or file:///abs/path -> strip leading slashes.
        return rest.lstrip("/")
    # file://host/share/... -> UNC \\host\share\...
    return "\\\\" + rest.replace("/", "\\")


def _repo_root() -> Path:
    """Repository root (two levels up from this file: accuracy_loop/ -> root)."""
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Locations (network share + local drop/expected folders)
# ---------------------------------------------------------------------------
@dataclass
class OutputLocations:
    """Filesystem locations for the manual produced-output workflow (tasks 9-11).

    Attributes:
        drop_dir: Local folder the operator pastes the *downloaded produced* file
            into (default ``data/executed_outputs``).
        expected_dir: Local folder holding the *known/expected* outputs (default
            ``data/expected_outputs``).
        network_dir: The network share directory the backend writes produced
            outputs to; the operator downloads from here.
    """

    drop_dir: Path
    expected_dir: Path
    network_dir: str

    @classmethod
    def from_env(cls, repo_root: Optional[Path] = None) -> "OutputLocations":
        """Build locations from ``EXECUTION_*`` env vars with sane defaults."""
        root = repo_root or _repo_root()
        drop = Path(
            os.getenv("EXECUTION_PRODUCED_OUTPUT_DIR")
            or (root / "data" / "executed_outputs")
        )
        expected = Path(
            os.getenv("EXECUTION_EXPECTED_OUTPUT_DIR")
            or (root / "data" / "expected_outputs")
        )
        net_env = os.getenv("EXECUTION_OUTPUT_NETWORK_DIR")
        if net_env:
            network = uri_to_local_path(net_env)
        else:
            base = uri_to_local_path(_DEFAULT_NETWORK_URI)
            network = str(Path(base) / _DEFAULT_NETWORK_SUBDIR)
        return cls(drop_dir=drop, expected_dir=expected, network_dir=network)

    def ensure_local_dirs(self) -> None:
        """Create the local drop + expected folders if they do not exist."""
        self.drop_dir.mkdir(parents=True, exist_ok=True)
        self.expected_dir.mkdir(parents=True, exist_ok=True)

    def ensure_network_dir(self) -> tuple[bool, str]:
        """Best-effort create the network output directory.

        Returns ``(ready, message)``. Never raises: the share is frequently
        unreachable from dev/CI machines, and that must not break the loop.
        """
        target = self.network_dir
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
            return True, f"Network output directory ready: {target}"
        except OSError as exc:  # noqa: BLE001 - unreachable share is expected off-network
            logger.warning("[accuracy-loop] network output dir unavailable (%s): %s", target, exc)
            return False, f"Network output directory unavailable ({target}): {exc}"

    # -- file resolution ----------------------------------------------------
    def resolve_produced(self, name_or_path: Optional[str]) -> Optional[Path]:
        """Resolve a produced-output file by name (in drop_dir) or explicit path."""
        return self._resolve(name_or_path, self.drop_dir)

    def resolve_known(self, name_or_path: Optional[str]) -> Optional[Path]:
        """Resolve a known/expected-output file by name (in expected_dir) or path."""
        return self._resolve(name_or_path, self.expected_dir)

    @staticmethod
    def _resolve(name_or_path: Optional[str], base: Path) -> Optional[Path]:
        if not name_or_path:
            return None
        p = Path(name_or_path)
        if p.is_absolute():
            return p if p.exists() else None
        candidate = base / name_or_path
        if candidate.exists():
            return candidate
        return p if p.exists() else None

    def latest_produced(self) -> Optional[Path]:
        """Return the most recently modified supported file in the drop folder."""
        if not self.drop_dir.exists():
            return None
        files = [
            f for f in self.drop_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _SUPPORTED_SUFFIXES
        ]
        if not files:
            return None
        return max(files, key=lambda f: f.stat().st_mtime)

    def list_files(self) -> dict[str, list[str]]:
        """List the supported files currently in the drop + expected folders."""
        def _names(d: Path) -> list[str]:
            if not d.exists():
                return []
            return sorted(
                f.name for f in d.iterdir()
                if f.is_file() and f.suffix.lower() in _SUPPORTED_SUFFIXES
            )
        return {"produced": _names(self.drop_dir), "expected": _names(self.expected_dir)}


# ---------------------------------------------------------------------------
# Tabular loading
# ---------------------------------------------------------------------------
@dataclass
class TabularData:
    """A normalised tabular file: a header plus a list of row value-lists."""

    columns: list[str]
    rows: list[list[Any]]
    source: str
    sheet: Optional[str] = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def col_count(self) -> int:
        return len(self.columns)


def load_tabular(path: str | Path, *, sheet: Optional[str] = None) -> TabularData:
    """Load an Excel/CSV file into a :class:`TabularData` (header + rows).

    NaN/empty cells are normalised to ``None``. Excel reads the requested sheet
    (or the first sheet); CSV/TSV are read as strings to avoid pandas coercing
    ids/codes to floats.
    """
    import pandas as pd  # local import: keeps module import light for non-output paths

    p = Path(path)
    if not p.exists():
        raise OutputFileNotFound(f"Output file not found: {p}")
    suffix = p.suffix.lower()

    sheet_name: Optional[str] = None
    try:
        if suffix in _EXCEL_SUFFIXES:
            excel = pd.ExcelFile(p)
            if not excel.sheet_names:
                raise OutputFileError(f"Workbook has no sheets: {p.name}")
            sheet_name = sheet or excel.sheet_names[0]
            if sheet_name not in excel.sheet_names:
                raise OutputFileError(
                    f"Sheet '{sheet_name}' not found in '{p.name}' "
                    f"(available: {', '.join(excel.sheet_names)})"
                )
            df = excel.parse(sheet_name)
        elif suffix in _CSV_SUFFIXES:
            sep = "\t" if suffix == ".tsv" else ","
            df = pd.read_csv(p, sep=sep, dtype=str, keep_default_na=True)
        else:
            raise OutputFileError(
                f"Unsupported output format '{suffix}' for '{p.name}' "
                f"(supported: {', '.join(sorted(_SUPPORTED_SUFFIXES))})"
            )
    except OutputFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalise pandas/engine errors
        raise OutputFileError(f"Failed to read output file '{p.name}': {exc}") from exc

    columns = [str(c) for c in df.columns]
    rows: list[list[Any]] = []
    for record in df.itertuples(index=False, name=None):
        rows.append([None if _is_nan(v) else v for v in record])
    return TabularData(columns=columns, rows=rows, source=str(p), sheet=sheet_name)


# ---------------------------------------------------------------------------
# Cell normalisation
# ---------------------------------------------------------------------------
@dataclass
class CompareOptions:
    """Cell-equality tuning for the diff."""

    trim_whitespace: bool = True
    case_insensitive: bool = False
    float_tolerance: float = 1e-9
    max_cell_diffs: int = _MAX_CELL_DIFFS


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _norm_str(value: Any, opts: CompareOptions) -> Optional[str]:
    if value is None:
        return None
    s = str(value)
    if opts.trim_whitespace:
        s = s.strip()
    if opts.case_insensitive:
        s = s.casefold()
    return s


def _cells_equal(produced: Any, known: Any, opts: CompareOptions) -> bool:
    """Compare two cells: numeric-tolerant first, then normalised string."""
    p_none = produced is None or _is_nan(produced)
    k_none = known is None or _is_nan(known)
    if p_none and k_none:
        return True
    if p_none != k_none:
        return False
    fp, fk = _as_float(produced), _as_float(known)
    if fp is not None and fk is not None:
        return abs(fp - fk) <= opts.float_tolerance
    return _norm_str(produced, opts) == _norm_str(known, opts)


def _col_key(name: str, opts: CompareOptions) -> str:
    key = name.strip() if opts.trim_whitespace else name
    return key.casefold() if opts.case_insensitive else key


def _cell_text(value: Any) -> Optional[str]:
    if value is None or _is_nan(value):
        return None
    return str(value)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def compare_tabular(
    produced: TabularData,
    known: TabularData,
    *,
    options: Optional[CompareOptions] = None,
) -> tuple[OutputComparisonReport, list[Mismatch]]:
    """Diff produced vs known tabular data cell-by-cell (tasks 10, 11).

    Columns are aligned by header (respecting the case/trim options); rows are
    aligned positionally. ``output_accuracy`` is ``matched_cells`` over the full
    known grid, so missing rows/columns lower the score. Returns the report plus
    one ``OUTPUT``-kind :class:`Mismatch` per differing cell (capped), plus one
    per missing column and one representative per missing-row band.
    """
    opts = options or CompareOptions()

    known_keys = [_col_key(c, opts) for c in known.columns]
    prod_keys = [_col_key(c, opts) for c in produced.columns]
    prod_index: dict[str, int] = {}
    for i, key in enumerate(prod_keys):
        prod_index.setdefault(key, i)
    prod_key_set = set(prod_keys)

    missing_columns = [known.columns[i] for i, k in enumerate(known_keys) if k not in prod_index]
    known_key_set = set(known_keys)
    extra_columns = [produced.columns[i] for i, k in enumerate(prod_keys) if k not in known_key_set]

    overlap_row_count = min(known.row_count, produced.row_count)
    matched_cells = 0
    compared_cells = 0
    cell_diffs: list[OutputCellDiff] = []
    mismatches: list[Mismatch] = []
    truncated = False

    for r in range(overlap_row_count):
        prod_row = produced.rows[r]
        known_row = known.rows[r]
        for c_pos, c_name in enumerate(known.columns):
            key = known_keys[c_pos]
            if key not in prod_index:
                continue  # missing column reported separately
            compared_cells += 1
            k_val = known_row[c_pos] if c_pos < len(known_row) else None
            p_pos = prod_index[key]
            p_val = prod_row[p_pos] if p_pos < len(prod_row) else None
            if _cells_equal(p_val, k_val, opts):
                matched_cells += 1
                continue
            if len(cell_diffs) < opts.max_cell_diffs:
                cell_diffs.append(
                    OutputCellDiff(
                        row_index=r,
                        column=c_name,
                        expected=_cell_text(k_val),
                        actual=_cell_text(p_val),
                    )
                )
                mismatches.append(
                    Mismatch(
                        block=None,
                        kind=MismatchKind.OUTPUT,
                        code=_OUTPUT_MISMATCH_CODE,
                        row_index=r,
                        column_name=c_name,
                        field=c_name,
                        expected=_cell_text(k_val),
                        actual=_cell_text(p_val),
                        message=(
                            f"Produced output cell (row {r}, column '{c_name}') "
                            f"does not match the known output."
                        ),
                    )
                )
            else:
                truncated = True

    # Structural issues -> mismatches (informational; not auto-applied).
    for col in missing_columns:
        mismatches.append(
            Mismatch(
                block=None,
                kind=MismatchKind.OUTPUT,
                code=_MISSING_COLUMN_CODE,
                row_index=0,
                column_name=col,
                field=col,
                expected=col,
                actual=None,
                message=f"Produced output is missing expected column '{col}'.",
            )
        )

    missing_rows = max(known.row_count - produced.row_count, 0)
    extra_rows = max(produced.row_count - known.row_count, 0)
    if missing_rows:
        mismatches.append(
            Mismatch(
                block=None,
                kind=MismatchKind.OUTPUT,
                code=_MISSING_ROW_CODE,
                row_index=produced.row_count,
                message=(
                    f"Produced output has {missing_rows} fewer row(s) than the "
                    f"known output ({produced.row_count} vs {known.row_count})."
                ),
            )
        )

    expected_cells = known.row_count * known.col_count
    output_accuracy = 1.0 if expected_cells == 0 else round(matched_cells / expected_cells, 6)
    exact = (
        not missing_columns
        and not extra_columns
        and missing_rows == 0
        and extra_rows == 0
        and not cell_diffs
        and not truncated
    )

    report = OutputComparisonReport(
        produced_file=produced.source,
        known_file=known.source,
        produced_sheet=produced.sheet,
        known_sheet=known.sheet,
        produced_rows=produced.row_count,
        known_rows=known.row_count,
        produced_columns=produced.col_count,
        known_columns=known.col_count,
        compared_cells=compared_cells,
        matched_cells=matched_cells,
        expected_cells=expected_cells,
        output_accuracy=output_accuracy,
        match=exact,
        missing_columns=missing_columns,
        extra_columns=extra_columns,
        missing_rows=missing_rows,
        extra_rows=extra_rows,
        cell_diffs=cell_diffs,
        truncated_diffs=truncated,
    )
    return report, mismatches


def compare_output_files(
    produced_path: str | Path,
    known_path: str | Path,
    *,
    sheet: Optional[str] = None,
    options: Optional[CompareOptions] = None,
) -> tuple[OutputComparisonReport, list[Mismatch]]:
    """Load both files and diff them (tasks 9, 10, 11).

    ``sheet`` selects a worksheet in both workbooks when they are multi-sheet.
    """
    produced = load_tabular(produced_path, sheet=sheet)
    known = load_tabular(known_path, sheet=sheet)
    return compare_tabular(produced, known, options=options)


__all__ = [
    "CompareOptions",
    "OutputFileError",
    "OutputFileNotFound",
    "OutputLocations",
    "TabularData",
    "compare_output_files",
    "compare_tabular",
    "load_tabular",
    "uri_to_local_path",
]
