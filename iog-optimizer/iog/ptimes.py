"""처리시간표 로딩.

게임 규칙: 그날 n개 job을 만들면 해당 요일 파일의 JobID 1~n 행만 쓴다.
(공식 scheduling_lib.py 가 pd.read_csv(..., nrows=len(job_seq)) 로 앞 n행만 읽음)
"""
from __future__ import annotations

import io
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .calendar import weekday_key

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ZIP_PATH = DATA_DIR / "ProcessingTimeTable_P1_P2.zip"


@lru_cache(maxsize=None)
def load_table(product: str, weekday: str) -> np.ndarray:
    """(500, 20) float64 배열. 행 i = JobID i+1."""
    name = f"{C.PTIME_PREFIX[product]}{weekday}.csv"
    csv_dir = DATA_DIR / "processing_times"
    if (csv_dir / name).exists():
        df = pd.read_csv(csv_dir / name, index_col="JobID")
    else:
        with zipfile.ZipFile(ZIP_PATH) as z:
            with z.open(name) as f:
                df = pd.read_csv(io.BytesIO(f.read()), index_col="JobID")
    df = df.sort_index()
    arr = df.to_numpy(dtype=np.float64)
    if arr.shape != (C.MAX_JOBS, C.N_MACHINES):
        raise ValueError(f"{name}: unexpected shape {arr.shape}")
    return arr


def table_for(product: str, d) -> np.ndarray:
    return load_table(product, weekday_key(d))


def first_n(product: str, d, n: int) -> np.ndarray:
    """그날 n개 job에 해당하는 처리시간 (JobID 1~n)."""
    if not 0 <= n <= C.MAX_JOBS:
        raise ValueError(f"n={n} out of range")
    return table_for(product, d)[:n]
