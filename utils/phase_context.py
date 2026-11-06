# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 - Detection support: shared pre-computed phase context. REUSABLE.
# ============================================================================
"""
utils/phase_context.py
======================
Shared pre-computed state injected into every detection phase.

Building a ``PhaseContext`` once in ``detect_all()`` and threading it through
all nine detection phases eliminates redundant per-column dtype inference
(``infer_dtype`` is called once per column rather than once per rule per phase)
and provides a single, authoritative place for shared column metadata.

Usage
-----
    from utils.phase_context import PhaseContext

    ctx = PhaseContext.build(df_in, df_out, column_mapping)

    # Each phase receives ctx and can resolve column objects without re-inferring
    result = p1(df_in, df_out, column_mapping=cm, ctx=ctx)

Column object caching
---------------------
``ctx.col_obj_in(col)`` / ``ctx.col_obj_out(col)`` return the pre-built column
object for *col*.  If a column is encountered that was not present at build time
(e.g. a derived intermediate column), the object is built on demand and cached
for subsequent calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

from utils.config_utils import make_column_object


@dataclass
class PhaseContext:
    """
    Pre-computed, shared state passed to all detection phases.

    Attributes
    ----------
    column_mapping : Dict[str, str]
        ``{input_col: output_col}`` mapping computed once by
        ``detect_column_mapping()`` before any phase runs.
    in_col_objects : Dict[str, Any]
        ``{col_name: column_object}`` for every column in ``df_in``.
        Built once; reused across all nine detection phases.
    out_col_objects : Dict[str, Any]
        Same for ``df_out`` columns.
    common_cols : List[str]
        Column names present in both ``df_in`` and ``df_out`` (input namespace).
    """

    column_mapping:  Dict[str, str]
    in_col_objects:  Dict[str, Any] = field(default_factory=dict)
    out_col_objects: Dict[str, Any] = field(default_factory=dict)
    common_cols:     List[str]      = field(default_factory=list)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        df_in: pd.DataFrame,
        df_out: pd.DataFrame,
        column_mapping: Dict[str, str],
    ) -> "PhaseContext":
        """
        Build a ``PhaseContext`` by computing column objects for every column in
        ``df_in`` and ``df_out`` exactly once.

        Args:
            df_in:          Input DataFrame (before transformations).
            df_out:         Output DataFrame (after transformations).
            column_mapping: ``{input_col: output_col}`` mapping.

        Returns:
            Fully populated ``PhaseContext``.
        """
        print("[PhaseContext] Building shared column context...")
        in_objs: Dict[str, Any] = {
            col: make_column_object(col, series=df_in[col])
            for col in df_in.columns
        }
        out_objs: Dict[str, Any] = {
            col: make_column_object(col, series=df_out[col])
            for col in df_out.columns
        }
        out_set = set(df_out.columns)
        common = [c for c in df_in.columns if c in out_set]
        print(
            f"[PhaseContext] Built {len(in_objs)} input + {len(out_objs)} output "
            f"column objects. {len(common)} common columns."
        )
        return cls(
            column_mapping=column_mapping,
            in_col_objects=in_objs,
            out_col_objects=out_objs,
            common_cols=common,
        )

    # ── Column object accessors (with on-demand caching) ─────────────────────

    def col_obj_in(self, col: str) -> Any:
        """
        Return the pre-built column object for an input column.

        If the column was not seen at build time (e.g. an intermediate derived
        column), a column object is built on demand and cached for reuse.
        """
        if col not in self.in_col_objects:
            self.in_col_objects[col] = make_column_object(col)
        return self.in_col_objects[col]

    def col_obj_out(self, col: str) -> Any:
        """
        Return the pre-built column object for an output column.

        Same on-demand caching behaviour as ``col_obj_in``.
        """
        if col not in self.out_col_objects:
            self.out_col_objects[col] = make_column_object(col)
        return self.out_col_objects[col]
