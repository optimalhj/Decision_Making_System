"""날짜 규칙: 수요 발생일, 생산 가능일, 주간 범위, 요일별 처리시간표 키."""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from . import config as C


def to_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def daterange(start, end) -> list[dt.date]:
    """start~end (양끝 포함)."""
    s, e = to_date(start), to_date(end)
    return [s + dt.timedelta(days=i) for i in range((e - s).days + 1)]


def is_krx_trading_day(d) -> bool:
    d = to_date(d)
    return d.weekday() < 5 and d not in C.KRX_HOLIDAYS


def has_demand(product: str, d) -> bool:
    d = to_date(d)
    if product == "P1":
        return is_krx_trading_day(d)
    return True  # P2: 매일


def can_produce(product: str, d) -> bool:
    d = to_date(d)
    if product == "P1" and C.P1_WEEKDAY_PRODUCTION_ONLY:
        return d.weekday() < 5  # 공휴일 평일도 입력칸은 열려 있음
    return True


def weekday_key(d) -> str:
    return C.WEEKDAY_KEYS[to_date(d).weekday()]


def week_of(d) -> list[dt.date]:
    """d가 속한 일~토 주."""
    d = to_date(d)
    sunday = d - dt.timedelta(days=(d.weekday() + 1) % 7)
    return daterange(sunday, sunday + dt.timedelta(days=6))


def next_week(d) -> list[dt.date]:
    """d 다음 주(일~토). 토요일에 입력하는 주간계획 대상."""
    return week_of(to_date(d) + dt.timedelta(days=7))


def round_of(d) -> str | None:
    d = to_date(d)
    for name, (s, e) in C.ROUNDS.items():
        if s <= d <= e:
            return name
    return None


def clip_to_round(days: Iterable[dt.date], round_name: str) -> list[dt.date]:
    s, e = C.ROUNDS[round_name]
    return [d for d in days if s <= d <= e]
