"""수요(= 주가) 예측: 기준선들 + 토요일 계획을 흉내 낸 롤링 백테스트.

원칙 (prompts/05_forecast_experiments.md 참고)
- 판정 기준은 MAPE가 아니라 시뮬레이터 비용(결품+보관). MAPE는 보조 (게임 영역 순위용).
- 새 모델은 naive 포함 기준선 전부를 '판정용 기간'에서 채택 마진 이상 이겨야 채택.
- 게임에 입력하는 예측값은 편향 없이. 여유분(k)은 생산량에서만 준다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config as C
from .calendar import has_demand, next_week, to_date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_PATH = DATA_DIR / "demand_history.csv"


# ---------------------------------------------------------------- 데이터
def load_history(path: Path = HISTORY_PATH) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df.sort_values(["product", "date"]).reset_index(drop=True)


def series(history: pd.DataFrame, product: str) -> pd.Series:
    s = history[history["product"] == product].set_index("date")["demand"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def merge_history(history: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    out = pd.concat([history, new_rows], ignore_index=True)
    out = out.drop_duplicates(["date", "product"], keep="last")
    return out.sort_values(["product", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------- 방법 (values: 과거값 배열, h: 몇 스텝 앞)
def _naive(v, h):
    return v[-1]


def _ma(k):
    return lambda v, h: float(np.mean(v[-k:]))


def _ses(alpha):
    def f(v, h):
        level = v[0]
        for x in v:
            level = alpha * x + (1 - alpha) * level
        return level
    return f


def _drift(k):
    return lambda v, h: v[-1] + h * (v[-1] - v[-1 - k]) / k if len(v) > k else v[-1]


METHODS: dict[str, Callable] = {
    "naive": _naive, "ma3": _ma(3), "ma5": _ma(5), "ma10": _ma(10),
    "ses03": _ses(0.3), "ses06": _ses(0.6), "drift10": _drift(10),
}


def register(name: str, fn: Callable) -> None:
    """새 모델 등록. fn(values: np.ndarray, h: int) -> float. 미래 정보를 쓰면 안 됨."""
    METHODS[name] = fn


# ---------------------------------------------------------------- 계획 시점 예측
# 계획 시점(토 16:00 이후)에 확정된 마지막 값:
#   P1: 금요일 종가(마지막 개장일). P2: 16:00 갱신으로 토요일 값까지 (보수적으로 가려면 p2_same_day_known=False)
def plan_forecast(history: pd.DataFrame, plan_date, method: str = "naive", days=None,
                  p2_same_day_known: bool = True) -> pd.DataFrame:
    """plan_date(보통 토요일)에 다음 주(일~토) 수요를 예측. 수요 없는 날은 0 (휴장일 0 입력 = 정확도 계산 제외)."""
    plan_date = to_date(plan_date)
    days = days or next_week(plan_date)
    rows = []
    for p in C.PRODUCTS:
        s = series(history, p)
        cutoff = plan_date if (p == "P1" or p2_same_day_known) else plan_date - dt.timedelta(days=1)
        past = s[s.index <= cutoff]
        if past.empty:
            raise ValueError(f"{p}: {cutoff} 이전 수요 이력이 없음")
        v = past.to_numpy()
        h = 0
        last = past.index[-1]
        for d in days:
            if not has_demand(p, d) or d <= last:
                rows.append((d, p, 0, 0))
                continue
            h += 1
            rows.append((d, p, int(round(METHODS[method](v, h))), h))
    return pd.DataFrame(rows, columns=["date", "product", "forecast", "h"])


# ---------------------------------------------------------------- 백테스트
def weekly_origins(history: pd.DataFrame, start=None, end=None) -> list[dt.date]:
    dmin = min(history["date"]) + dt.timedelta(days=21)
    dmax = max(history["date"]) - dt.timedelta(days=7)
    s = to_date(start) if start else dmin
    e = to_date(end) if end else dmax
    first_sat = s + dt.timedelta(days=(5 - s.weekday()) % 7)
    out = []
    d = first_sat
    while d <= e:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def backtest(history: pd.DataFrame, method: str, origins: list[dt.date]) -> pd.DataFrame:
    """토요일마다 plan_forecast → 실제와 비교. 한 행 = (origin, date, product, h, forecast, actual)."""
    rows = []
    act = {p: series(history, p) for p in C.PRODUCTS}
    for o in origins:
        fc = plan_forecast(history, o, method)
        for r in fc.itertuples():
            if r.h == 0:
                continue
            a = act[r.product].get(r.date)
            if a is None or np.isnan(a):
                continue
            rows.append((o, r.date, r.product, r.h, r.forecast, a))
    return pd.DataFrame(rows, columns=["origin", "date", "product", "h", "forecast", "actual"])


def mape_by_h(bt: pd.DataFrame) -> pd.DataFrame:
    bt = bt.assign(ape=(bt["forecast"] - bt["actual"]).abs() / bt["actual"] * 100)
    return bt.groupby(["product", "h"])["ape"].mean().unstack(0).round(2)


def ratio_quantiles(bt: pd.DataFrame, qs=(0.1, 0.5, 0.9)) -> pd.DataFrame:
    """실제/예측 비율의 분위수 (지평별). 안전재고·자재 여유분 계산에 쓴다."""
    bt = bt.assign(ratio=bt["actual"] / bt["forecast"])
    return bt.groupby(["product", "h"])["ratio"].quantile(list(qs)).unstack().round(4)


def split_selection_holdout(origins: list[dt.date], holdout_start) -> tuple[list, list]:
    hs = to_date(holdout_start)
    return [o for o in origins if o < hs], [o for o in origins if o >= hs]
