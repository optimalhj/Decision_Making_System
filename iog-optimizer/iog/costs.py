"""생산비 계산과 요일별 비용곡선.

생산비 = 셋업비(그날 생산하면) + 인건비
인건비 = H × 100,000 + max(H − 70, 0) × 100,000 × EXT_PROD_COST,  H = floor(makespan / 100)
→ 2026-09-27 장부 3팀(우리 $390,000 / AUTO $3,335,280 / 교수팀 $3,022,000)과 원 단위까지 일치
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import ptimes
from .scheduling import neh, spt

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CURVE_PATH = DATA_DIR / "cost_curves.csv"


def hours(makespan: float) -> int:
    return int(makespan // C.MAKESPAN_PER_HOUR)


def labor_cost(makespan: float) -> dict:
    h = hours(makespan)
    cap_h = C.MAKESPAN_CAPA // C.MAKESPAN_PER_HOUR
    regular = h * C.LABOR_COST
    ext = max(h - cap_h, 0) * C.LABOR_COST * C.EXT_PROD_COST
    return {"hours": h, "labor": regular, "ext": ext, "total": regular + ext}


def production_cost(product: str, makespan: float, n_jobs: int) -> dict:
    """장부의 PRODUCTION_Px 한 줄과 같은 구성 (labor, ext, setup)."""
    if n_jobs <= 0:
        return {"hours": 0, "labor": 0, "ext": 0, "setup": 0, "total": 0}
    lab = labor_cost(makespan)
    setup = C.SETUP_COST[product]
    return {**lab, "setup": setup, "total": lab["total"] + setup}


def build_curves(method: str = "neh", n_max: int = C.MAX_JOBS, products=C.PRODUCTS,
                 weekdays=C.WEEKDAY_KEYS, verbose: bool = True) -> pd.DataFrame:
    """제품·요일·job 수(n)별 makespan과 생산비. method: 'neh' | 'spt'.

    IG는 n마다 돌리기엔 느려서 곡선은 NEH(상한)로 만들고, 실제 생산일에는 IG로 순서를 다시 짠다.
    """
    rows = []
    for p in products:
        for w in weekdays:
            P = ptimes.load_table(p, w)
            for n in range(1, n_max + 1):
                seq, ms = (neh(P[:n]) if method == "neh" else spt(P[:n]))
                cost = production_cost(p, ms, n)["total"]
                rows.append((p, w, n, ms, cost))
            if verbose:
                print(f"  curve {p} {w} done")
    df = pd.DataFrame(rows, columns=["product", "weekday", "n_jobs", "makespan", "cost"])
    df["method"] = method
    return df


def load_curves(path: Path = CURVE_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음 → python scripts/build_cost_curves.py 먼저 실행")
    return pd.read_csv(path)


class CostCurves:
    """cost(product, weekday, n) 조회. n=0 이면 0."""

    def __init__(self, df: pd.DataFrame | None = None):
        df = load_curves() if df is None else df
        self._cost = {}
        self._ms = {}
        for (p, w), g in df.groupby(["product", "weekday"]):
            g = g.sort_values("n_jobs")
            cost = np.zeros(C.MAX_JOBS + 1)
            ms = np.zeros(C.MAX_JOBS + 1)
            cost[g["n_jobs"].to_numpy()] = g["cost"].to_numpy()
            ms[g["n_jobs"].to_numpy()] = g["makespan"].to_numpy()
            self._cost[(p, w)] = cost
            self._ms[(p, w)] = ms

    def cost(self, product: str, weekday: str, n: int) -> float:
        return float(self._cost[(product, weekday)][n]) if n > 0 else 0.0

    def makespan(self, product: str, weekday: str, n: int) -> float:
        return float(self._ms[(product, weekday)][n]) if n > 0 else 0.0

    def array(self, product: str, weekday: str) -> np.ndarray:
        return self._cost[(product, weekday)]
