# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 5 / Deliverable 7 - make_serializable helper. REUSABLE.
# Rework: ensure deterministic ordering + exact JSON typing for the compiler.
# ============================================================================
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from pandas import Timestamp, NaT


def make_serializable(obj):
    if obj is NaT:
        return None
    elif isinstance(obj, Timestamp):
        return obj.isoformat()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    elif isinstance(obj, pd.Series):
        return obj.tolist()
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, Path):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_serializable(i) for i in obj]
    return obj
