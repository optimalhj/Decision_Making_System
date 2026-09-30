"""결정 층: 생산 목표(k) → 생산 요일 배분(DP) → 작업순서(IG) → 자재 발주(MRP).

비용은 전부 여기(결정 층)에 들어간다. 예측 모델 안에 비용을 넣지 않는다.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from . import config as C
from . import ptimes
from .calendar import can_produce, daterange, has_demand, to_date, weekday_key
from .costs import CostCurves, production_cost
from .scheduling import format_sequence, iterated_greedy, neh, spt
from .simulator import DayPlan, Order, SimState, simulate


# ---------------------------------------------------------------- 1) 생산 목표
def targets_from_forecast(fc: pd.DataFrame, k: dict | None = None) -> dict:
    """targets[product][date] = 목표 판매 가능량(개) = ceil(forecast × (1+k))."""
    k = k or C.SAFETY_FACTOR
    out = {p: {} for p in C.PRODUCTS}
    for r in fc.itertuples():
        f = float(r.forecast)
        out[r.product][r.date] = int(math.ceil(f * (1 + k[r.product]))) if f > 0 else 0
    return out


# ---------------------------------------------------------------- 2) 생산 요일 배분 (동적계획)
def lot_size(product: str, days: list[dt.date], need_units: dict, curves: CostCurves,
             init_fg_units: int = 0, max_jobs: int = C.MAX_JOBS, max_inv_jobs: int = 700) -> dict:
    """need_units[d] 를 결품 없이 채우는 최소비용 생산계획 (job 단위).
    비용 = 요일별 생산비곡선(셋업+인건비) + 완제품 보관비 30/개·일.
    days 뒤쪽에 며칠 여유(lookahead)를 붙여서 부르고, 필요한 주간만 잘라 쓰는 것을 권장.
    """
    INF = 1e30
    hold_job = C.FG_HOLD_COST * C.JOB_SIZE
    I0 = int(init_fg_units // C.JOB_SIZE)
    V = np.full(max_inv_jobs + 1, INF)
    V[min(I0, max_inv_jobs)] = 0.0
    choice = []
    n_range = np.arange(max_jobs + 1)
    for d in days:
        D = int(math.ceil(need_units.get(d, 0) / C.JOB_SIZE))
        allowed = can_produce(product, d)
        cost_n = curves.array(product, weekday_key(d))[: max_jobs + 1].copy() if allowed else None
        NV = np.full(max_inv_jobs + 1, INF)
        arg = np.full(max_inv_jobs + 1, -1, dtype=np.int64)
        for I in np.nonzero(V < INF)[0]:
            if allowed:
                J = I + n_range - D
                ok = (J >= 0) & (J <= max_inv_jobs)
                if not ok.any():
                    continue
                tot = V[I] + cost_n[ok] + hold_job * J[ok]
                Js = J[ok]
                better = tot < NV[Js]
                NV[Js[better]] = tot[better]
                arg[Js[better]] = I * 1000 + n_range[ok][better]
            else:
                J = I - D
                if 0 <= J <= max_inv_jobs:
                    tot = V[I] + hold_job * J
                    if tot < NV[J]:
                        NV[J] = tot
                        arg[J] = I * 1000
        choice.append(arg)
        V = NV
    if not np.isfinite(V).any() or V.min() >= INF:
        raise ValueError(f"{product}: 결품 없이 채울 수 없음 (하루 최대 {max_jobs} jobs, 보유재고 {I0} jobs)")
    J = int(np.argmin(V))
    plan = {}
    for t in range(len(days) - 1, -1, -1):
        a = int(choice[t][J])
        I, n = divmod(a, 1000)
        plan[days[t]] = n
        J = I
    return plan


# ---------------------------------------------------------------- 3) 작업순서
def sequence_for(product: str, d, n_jobs: int, method: str = "ig", seconds: float = 10.0, seed: int = 0):
    """그날 n개 job의 순서와 makespan. 반환 seq는 0-based (게임 입력은 format_sequence)."""
    if n_jobs <= 0:
        return np.empty(0, np.int64), 0.0, {}
    P = ptimes.first_n(product, d, n_jobs)
    if method == "spt":
        seq, ms = spt(P)
        return seq, ms, {"method": "spt"}
    seq, ms = neh(P)
    if method == "neh" or n_jobs <= 2:
        return seq, ms, {"method": "neh"}
    seq, ms, info = iterated_greedy(P, time_limit=seconds, seed=seed, init_seq=seq)
    info["method"] = "ig"
    return seq, ms, info


# ---------------------------------------------------------------- 4) 자재 발주 (MRP)
def material_requirements(prod_jobs: dict, days: list[dt.date]) -> dict:
    """req[m][d] = 그날 생산에 필요한 자재 개수."""
    req = {m: {d: 0 for d in days} for m in C.MATERIALS}
    for p, byday in prod_jobs.items():
        for d, jobs in byday.items():
            if d in req["M1"]:
                for m, r in C.BOM[p].items():
                    req[m][d] += int(jobs) * C.JOB_SIZE * r
    return req


def material_sim(days: list[dt.date], on_hand: dict, orders: list[Order], prod_jobs: dict) -> list[dict]:
    """자재만 굴리는 빠른 시뮬레이션. 자재가 모자라면 생산이 줄고(BOM·배분 순서 반영) 그만큼 다른 자재도 덜 쓴다."""
    inv = {m: int(on_hand[m]) for m in C.MATERIALS}
    rec = {}
    for o in orders:
        rec[(o.arrival, o.material)] = rec.get((o.arrival, o.material), 0) + int(o.qty)
    log = []
    for d in days:
        for m in C.MATERIALS:
            inv[m] += rec.get((d, m), 0)
        before = dict(inv)
        req = {m: 0 for m in C.MATERIALS}
        made, short = {}, {}
        for p in C.PRODUCTION_ORDER_WHEN_SHORT:
            want = int(prod_jobs.get(p, {}).get(d, 0))
            for m, r in C.BOM[p].items():
                req[m] += want * C.JOB_SIZE * r
            cap = min(inv[m] // (r * C.JOB_SIZE) for m, r in C.BOM[p].items())
            jobs = min(want, int(cap))
            if jobs < want:
                short[p] = {"jobs": want - jobs,
                            "binding": [m for m, r in C.BOM[p].items() if inv[m] // (r * C.JOB_SIZE) < want]}
            for m, r in C.BOM[p].items():
                inv[m] -= jobs * C.JOB_SIZE * r
            made[p] = jobs
        log.append({"date": d, "before": before, "after": dict(inv), "req": req, "made": made, "short": short})
    return log


def plan_material_orders(today, prod_jobs: dict, on_hand: dict, open_orders: list[Order],
                         horizon_end, round_end=None, cover_days: dict | None = None,
                         safety_units: dict | None = None, max_iter: int = 60) -> tuple[list[Order], list[dict]]:
    """오늘부터 horizon_end까지 자재가 모자라지 않게 발주안 작성 (시뮬레이션하며 고치는 방식).

    on_hand: 오늘 운영 '전' 재고 (= 어제 운영 후 재고).
    규칙: 운영 전 재고가 그날 소요 + 안전재고보다 적어지는 첫 날 d를 찾아, d에 도착하도록 가장 늦은 날 일반 발주.
          일반으로 못 맞추면 M2만 긴급. 둘 다 안 되면 '막을 수 없는 부족'으로 넘기고 계속.
          한 번에 cover_days 소요일치를 덮고, 라운드 끝 이후 소요는 사지 않는다.
          다른 자재 부족으로 생산이 줄면 그 자재 소요도 줄어드는 것까지 반영된다.
    반환: (발주안 — order_date == today 인 것만 오늘 입력 가능, 생산 부족 목록)
    """
    today = to_date(today)
    horizon_end = to_date(horizon_end)
    round_end = to_date(round_end) if round_end else horizon_end
    cover_days = cover_days or C.MATERIAL_COVER_DAYS
    safety_units = safety_units or C.MATERIAL_SAFETY_UNITS
    days = daterange(today, horizon_end)
    full_req = material_requirements(prod_jobs, days)
    proposals: list[Order] = []
    skip: set = set()
    for _ in range(max_iter):
        log = material_sim(days, on_hand, list(open_orders) + proposals, prod_jobs)
        trigger = None
        for row in log:
            d = row["date"]
            for m in C.MATERIALS:
                if (d, m) in skip or row["req"][m] <= 0:
                    continue
                if row["before"][m] < row["req"][m] + safety_units[m]:
                    trigger = (d, m, row["before"][m])
                    break
            if trigger:
                break
        if trigger is None:
            break
        d, m, inv_before = trigger
        kind, od = None, None
        od_n = d - dt.timedelta(days=C.LEAD_TIME[(m, "normal")])
        if od_n >= today:
            kind, od = "normal", od_n
        elif (m, "urgent") in C.LEAD_TIME and d - dt.timedelta(days=C.LEAD_TIME[(m, "urgent")]) >= today:
            kind, od = "urgent", d - dt.timedelta(days=C.LEAD_TIME[(m, "urgent")])
        if kind is None:
            skip.add((d, m))
            continue
        req_days = [x for x in days if d <= x <= round_end and full_req[m][x] > 0][: cover_days[m]]
        qty = sum(full_req[m][x] for x in req_days) + safety_units[m] - inv_before
        qty = max(qty, full_req[m][d] + safety_units[m] - inv_before, C.JOB_SIZE)
        proposals.append(Order(m, od, kind, int(math.ceil(qty / 1000.0) * 1000), "PLAN"))
    log = material_sim(days, on_hand, list(open_orders) + proposals, prod_jobs)
    shortages = []
    for row in log:
        for p, s in row["short"].items():
            shortages.append({"date": row["date"], "product": p, "material": ",".join(s["binding"]),
                              "short_units": s["jobs"] * C.JOB_SIZE,
                              "reason": "리드타임 안에 도착할 발주 방법이 없음" if any((row["date"], m) in skip for m in s["binding"])
                              else "자재 부족"})
    proposals.sort(key=lambda o: (o.order_date, o.material, o.kind))
    return proposals, shortages


def todays_form_values(proposals: list[Order], today) -> dict:
    """자재 입력 화면 칸 이름 → 값 (m1_d_1, m2_n_1, m2_u_1, m3_d_1)."""
    today = to_date(today)
    vals = {"m1_d_1": 0, "m2_n_1": 0, "m2_u_1": 0, "m3_d_1": 0}
    key = {("M1", "normal"): "m1_d_1", ("M2", "normal"): "m2_n_1", ("M2", "urgent"): "m2_u_1",
           ("M3", "normal"): "m3_d_1"}
    for o in proposals:
        if o.order_date == today:
            vals[key[(o.material, o.kind)]] += int(o.qty)
    return vals


# ---------------------------------------------------------------- 5) 상태 투영
def project(state: SimState, start, end, fixed_plans: dict, demand_fc: dict):
    """이미 입력된 계획으로 start~end 를 돌려서 end 운영 후 상태를 추정 (예측 수요 기준)."""
    if to_date(end) < to_date(start):
        return state, [], []
    ledger, days, st = simulate(start, end, state, fixed_plans, demand_fc)
    return st, ledger, days


def demand_dict(fc: pd.DataFrame) -> dict:
    out = {p: {} for p in C.PRODUCTS}
    for r in fc.itertuples():
        out[r.product][r.date] = int(r.forecast)
    return out


def plan_cost_summary(product: str, plan: dict, curves: CostCurves) -> float:
    return sum(curves.cost(product, weekday_key(d), n) for d, n in plan.items())


def naive_levels(history: pd.DataFrame) -> dict:
    """제품별 마지막 실수요 (naive 예측값)."""
    out = {}
    for p in C.PRODUCTS:
        s = history[history["product"] == p].sort_values("date")
        out[p] = float(s["demand"].iloc[-1])
    return out


def production_outlook(fixed: dict, history: pd.DataFrame, curves: CostCurves, start, end,
                       k: dict | None = None, lookahead: int = 2,
                       start_fg: dict | None = None) -> tuple[dict, dict]:
    """start~end 의 제품별 생산 job 수. 이미 입력된 계획(fixed)은 그대로, 없는 날은 추정.
    추정 = naive 예측 × (1+k) 를 결품 없이 채우는 DP 계획. 반환: (jobs[p][d], is_estimated[p][d]).
    start_fg = start 날 운영 전 완제품 재고(개). 주면 입력된 계획 + naive 수요로 추정 첫날까지 굴려서 DP 시작 재고로 쓴다."""
    k = k or C.SAFETY_FACTOR
    start, end = to_date(start), to_date(end)
    days = daterange(start, end)
    level = naive_levels(history)
    jobs = {p: {} for p in C.PRODUCTS}
    est = {p: {} for p in C.PRODUCTS}
    for p in C.PRODUCTS:
        missing = [d for d in days if d not in fixed.get(p, {})]
        for d in days:
            if d in fixed.get(p, {}):
                jobs[p][d] = int(fixed[p][d])
                est[p][d] = False
        if missing:
            ext = daterange(missing[0], missing[-1] + dt.timedelta(days=lookahead))
            need = {d: (int(math.ceil(level[p] * (1 + k[p]))) if has_demand(p, d) else 0) for d in ext}
            fg = 0.0
            if start_fg is not None:
                fg = float(start_fg.get(p, 0))
                for d in days:
                    if d >= missing[0]:
                        break
                    fg = max(fg + jobs[p].get(d, 0) * C.JOB_SIZE - (level[p] if has_demand(p, d) else 0.0), 0.0)
            plan = lot_size(p, ext, need, curves, init_fg_units=int(fg))
            for d in missing:
                jobs[p][d] = int(plan.get(d, 0))
                est[p][d] = True
    return jobs, est
