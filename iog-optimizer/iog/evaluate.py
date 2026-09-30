"""예측 방법·안전재고(k)를 '돈'으로 채점한다 (수요 불일치 비용).

토요일마다 다음 주 계획을 세우고 주중에는 못 고친다는 게임 규칙을 그대로 흉내 낸다.
생산량 = 목표재고(예측 × (1+k)) − 예상 이월재고. 실제 수요로 재고를 굴리고
비용 = 결품 × (250 + 마진 손실) + 완제품 보관 30/개·일.
생산비 자체는 여기서 빼고(예측과 무관), 불일치 비용만 비교한다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config as C
from .calendar import daterange, has_demand
from .forecast import METHODS, plan_forecast, series

# 결품 1개당 손실 = 결품비 + (판매가 − 변동비). 변동비 = 자재 + 한계 생산비(잔업 구간 기준)
LOST_MARGIN = {"P1": 250 - 80 - 106, "P2": 350 - 100 - 150}
UNDERAGE = {p: C.STOCKOUT_COST + LOST_MARGIN[p] for p in C.PRODUCTS}


def mismatch_cost(history: pd.DataFrame, method: str, origins: list, k: float, product: str,
                  lam: float = 1.0) -> dict:
    """lam<1 이면 예측오차를 lam배로 줄인 '가상의 더 좋은 모델' (개선 가치 추정용)."""
    act = series(history, product)
    carry = 0.0
    so = hold = 0.0
    n_dem = 0
    for o in origins:
        fc = plan_forecast(history, o, method)
        fc = fc[fc["product"] == product]
        days = list(fc["date"])
        f_by = dict(zip(fc["date"], fc["forecast"]))
        plan, first, prevF = {}, True, 0.0
        for d in days:
            if f_by[d] > 0 and d in act.index:
                F = act[d] + lam * (f_by[d] - act[d])
                S = F * (1 + k)
                ecarry = carry if first else k * prevF
                plan[d] = max(0.0, math.ceil((S - ecarry) / 1000) * 1000)
                first, prevF = False, F
            else:
                plan[d] = 0.0
        for d in days:
            D = float(act.get(d, 0.0)) if has_demand(product, d) else 0.0
            avail = carry + plan[d]
            sold = min(D, avail)
            so += D - sold
            carry = avail - sold
            hold += carry * C.FG_HOLD_COST
            n_dem += D > 0
    cost = so * UNDERAGE[product] + hold
    return {"cost_per_day": cost / max(n_dem, 1), "stockout_per_day": so / max(n_dem, 1),
            "holding_per_day": hold / max(n_dem, 1), "days": n_dem}


def grid_k(history, method, origins, product, ks=None, lam=1.0) -> pd.DataFrame:
    ks = ks if ks is not None else [-0.10, -0.05, 0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15]
    rows = []
    for k in ks:
        r = mismatch_cost(history, method, origins, k, product, lam)
        rows.append({"k": k, **r})
    return pd.DataFrame(rows)
