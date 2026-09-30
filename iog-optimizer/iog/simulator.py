"""하루 단위 게임 시뮬레이터 (계획 채점기).

하루 순서 (게임 장부 구조를 따름):
  1) 자재 입고  2) 생산 (자재 부족하면 가능한 job 수까지만)  3) 판매·결품
  4) 운영 후 재고에 보관비 (완제품 30/개, 자재 1·1·2/개)
검증: tests/test_simulator.py 가 2026-09-27 장부 3팀을 원 단위까지 재현한다.
가정(ASSUMED)은 config.py 참고: 결품 이월 없음, 구매비는 입고일 과금, 자재 부족 시 P1 먼저.
"""
from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from . import config as C
from . import ptimes
from .calendar import daterange, has_demand, to_date
from .costs import production_cost
from .scheduling import makespan as seq_makespan, neh


@dataclass
class Order:
    material: str
    order_date: dt.date
    kind: str            # "normal" | "urgent"
    qty: int
    source: str = "MANUAL"

    @property
    def arrival(self) -> dt.date:
        return self.order_date + dt.timedelta(days=C.LEAD_TIME[(self.material, self.kind)])

    def to_dict(self) -> dict:
        return {"material": self.material, "order_date": self.order_date.isoformat(), "type": self.kind,
                "qty": int(self.qty), "arrival": self.arrival.isoformat(), "source": self.source}

    @staticmethod
    def from_dict(d: dict) -> "Order":
        return Order(d["material"], to_date(d["order_date"]), d.get("type", d.get("kind", "normal")).lower(),
                     int(d["qty"]), d.get("source", "MANUAL"))


@dataclass
class SimState:
    material: dict = field(default_factory=lambda: dict(C.INIT_MATERIAL))
    fg: dict = field(default_factory=lambda: {"P1": 0, "P2": 0})
    orders: list = field(default_factory=list)       # list[Order] (입고 전)


@dataclass
class DayPlan:
    """그날 제품별 생산 계획. seq 가 있으면 그 순서로, 없으면 n_jobs 개를 NEH로 가정."""
    n_jobs: int = 0
    seq: np.ndarray | None = None       # 0-based
    makespan: float | None = None       # 알고 있으면 직접 지정 (게임 결과 재현용)


def _plan_makespan(product: str, day: dt.date, plan: DayPlan, jobs: int) -> float:
    if jobs <= 0:
        return 0.0
    if plan.makespan is not None and jobs == plan.n_jobs:
        return float(plan.makespan)
    P = ptimes.table_for(product, day)
    if plan.seq is not None:
        seq = np.asarray(plan.seq, dtype=np.int64)[:jobs]
        return seq_makespan(P, seq)
    return neh(P[:jobs])[1]


def simulate(start, end, state: SimState, plans: dict, demand: dict, discounts: dict | None = None,
             new_orders: list | None = None, elasticity: float = 1.0, record: bool = True):
    """plans[product][date] = DayPlan | int, demand[product][date] = 수요(개).

    반환: (ledger rows, daily snapshots, final state)
    """
    st = copy.deepcopy(state)
    orders = list(st.orders) + list(new_orders or [])
    discounts = discounts or {}
    ledger, days = [], []

    def add(d, action, revenue=0.0, expense=0.0, desc=""):
        if record:
            ledger.append({"date": d.isoformat(), "action": action, "revenue": float(revenue),
                           "expense": float(expense), "description": desc})

    for d in daterange(start, end):
        # 1) 입고 (+ 구매비)
        for o in orders:
            charge_day = o.arrival if C.PURCHASE_COST_AT == "arrival" else o.order_date
            if o.arrival == d:
                st.material[o.material] += o.qty
            if charge_day == d:
                unit = C.UNIT_COST[(o.material, o.kind)]
                add(d, f"PURCHASE_{o.material}", 0, o.qty * unit + C.ORDER_COST[o.material],
                    f"{o.qty} items * ${unit} + order ${C.ORDER_COST[o.material]} ({o.kind})")
        # 2) 생산
        produced = {}
        for p in C.PRODUCTION_ORDER_WHEN_SHORT:
            plan = plans.get(p, {}).get(d, 0)
            plan = plan if isinstance(plan, DayPlan) else DayPlan(n_jobs=int(plan))
            want = plan.n_jobs
            cap_units = min(st.material[m] // r for m, r in C.BOM[p].items())
            jobs = int(min(want, cap_units // C.JOB_SIZE))
            ms = _plan_makespan(p, d, plan, jobs)
            cost = production_cost(p, ms, jobs)
            for m, r in C.BOM[p].items():
                st.material[m] -= jobs * C.JOB_SIZE * r
            produced[p] = jobs * C.JOB_SIZE
            add(d, f"PRODUCTION_{p}", 0, cost["total"],
                f"{jobs * C.JOB_SIZE} items (labor: {cost['labor']:.0f}, ext: {cost['ext']:.0f}, setup: {cost['setup']})"
                + (f" [planned {want} jobs, material-limited]" if jobs < want else ""))
        # 3) 판매·결품
        sold, short = {}, {}
        for p in C.PRODUCTS:
            base = int(demand.get(p, {}).get(d, 0)) if has_demand(p, d) else 0
            r = float(discounts.get(p, {}).get(d, 0.0))
            dem = int(np.floor(base * (1 + elasticity * r))) if r > 0 else base
            avail = st.fg[p] + produced[p]
            s = min(dem, avail)
            sold[p], short[p] = s, dem - s
            st.fg[p] = avail - s
            price = C.PRICE[p] * (1 - r)
            if s > 0:
                add(d, f"SALES_{p}", s * price, 0, f"{s} items * ${price:g}, dc {r:.2f}")
            if short[p] > 0:
                add(d, f"STOCKOUT_{p}", 0, short[p] * C.STOCKOUT_COST, f"{short[p]} items * ${C.STOCKOUT_COST}")
        # 4) 보관비 (운영 후 재고)
        for p in C.PRODUCTS:
            add(d, f"SALES_INVENTORY_{p}", 0, st.fg[p] * C.FG_HOLD_COST, f"{st.fg[p]} items * ${C.FG_HOLD_COST}")
        for m in C.MATERIALS:
            add(d, f"MATERIAL_INVENTORY_{m}", 0, st.material[m] * C.MAT_HOLD_COST[m],
                f"{st.material[m]} items * ${C.MAT_HOLD_COST[m]}")
        days.append({"date": d.isoformat(), "material": dict(st.material), "fg": dict(st.fg),
                     "produced": produced, "sold": sold, "stockout": short})
        # 입고 끝난 주문 정리
        orders = [o for o in orders if o.arrival > d]
    st.orders = [o for o in orders if o.arrival > to_date(end)]
    return ledger, days, st


def summarize(ledger: list) -> dict:
    rev = sum(r["revenue"] for r in ledger)
    exp = sum(r["expense"] for r in ledger)
    by = {}
    for r in ledger:
        key = r["action"].split("_")[0] if not r["action"].startswith(("SALES_INVENTORY", "MATERIAL_INVENTORY")) \
            else "_".join(r["action"].split("_")[:2])
        by[key] = by.get(key, 0.0) + r["revenue"] - r["expense"]
    return {"revenue": rev, "expense": exp, "net": rev - exp, "by_type": by}


def conservation_check(start_state: SimState, days: list, orders: list) -> None:
    """자재: 초기 + 입고 − 출고 = 기말. 완제품: 초기 + 생산 − 판매 = 기말. 틀리면 AssertionError."""
    if not days:
        return
    first, last = to_date(days[0]["date"]), to_date(days[-1]["date"])
    arrived = {m: 0 for m in C.MATERIALS}
    for o in orders:
        if first <= o.arrival <= last:
            arrived[o.material] += o.qty
    used = {m: 0 for m in C.MATERIALS}
    for day in days:
        for p, units in day["produced"].items():
            for m, r in C.BOM[p].items():
                used[m] += units * r
    for m in C.MATERIALS:
        assert start_state.material[m] + arrived[m] - used[m] == days[-1]["material"][m], m
    for p in C.PRODUCTS:
        made = sum(day["produced"][p] for day in days)
        sold = sum(day["sold"][p] for day in days)
        assert start_state.fg[p] + made - sold == days[-1]["fg"][p], p
