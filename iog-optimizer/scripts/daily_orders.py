"""매일 자재 발주안 (16:00 수요 갱신 뒤 ~ 19:30 전에 실행).

사용:
  python scripts/daily_orders.py                      # 오늘 기준
  python scripts/daily_orders.py --date 2026-09-29
  python scripts/daily_orders.py --record             # 게임에 입력을 마친 뒤, 오늘 발주를 state.json 에 기록

출력: outputs/daily_YYYY-MM-DD.md (사람이 보는 표) + outputs/daily_YYYY-MM-DD_form.json (입력 칸 이름 → 값)
주의: 발주 입력은 사람이 확인한 뒤에만 한다. 이 스크립트는 게임에 아무것도 보내지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iog import config as C  # noqa: E402
from iog import forecast as F  # noqa: E402
from iog import sheets, state as S  # noqa: E402
from iog.calendar import daterange, round_of, to_date  # noqa: E402
from iog.costs import CostCurves  # noqa: E402
from iog.planning import (demand_dict, plan_material_orders, production_outlook,  # noqa: E402
                          todays_form_values)
from iog.simulator import simulate  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "outputs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--horizon", type=int, default=14)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    today = to_date(args.date)
    st = S.load()
    rnd = round_of(today) or st["round"]
    round_end = C.ROUNDS[rnd][1]
    as_of = to_date(st["as_of"])
    hist = F.load_history()
    curves = CostCurves()
    fixed = S.fixed_plans(st)
    warn = []

    # 1) 오늘 운영 전 자재 재고 (state 가 어제보다 오래됐으면 계획대로 굴려서 추정)
    sim_state = S.to_sim_state(st)
    if as_of < today - dt.timedelta(days=1):
        warn.append(f"state.json 이 {as_of} 기준입니다. 먼저 `python scripts/sync_state.py` 로 갱신하세요. "
                    f"(지금은 {as_of + dt.timedelta(days=1)}~{today - dt.timedelta(days=1)} 를 계획·예측 수요로 굴려서 추정)")
        gap = daterange(as_of + dt.timedelta(days=1), today - dt.timedelta(days=1))
        lvl = {p: float(hist[hist["product"] == p]["demand"].iloc[-1]) for p in C.PRODUCTS}
        dem = {p: {d: lvl[p] for d in gap} for p in C.PRODUCTS}
        _, _, sim_state = simulate(gap[0], gap[-1], sim_state, fixed, dem, record=False)
    on_hand = dict(sim_state.material)
    open_orders = [o for o in sim_state.orders if o.arrival >= today]

    # 2) 앞으로의 생산계획 (입력된 것 + 없는 날은 추정)
    horizon_end = min(today + dt.timedelta(days=args.horizon), round_end)
    jobs, est = production_outlook(fixed, hist, curves, today, horizon_end, start_fg=dict(sim_state.fg))

    # 3) MRP
    proposals, shortages = plan_material_orders(today, jobs, on_hand, open_orders, horizon_end, round_end)
    form_vals = todays_form_values(proposals, today)

    # 4) 투영 (발주안 반영 후 자재 재고 추이)
    lvl = {p: float(hist[hist["product"] == p]["demand"].iloc[-1]) for p in C.PRODUCTS}
    days = daterange(today, horizon_end)
    dem = {p: {d: lvl[p] for d in days} for p in C.PRODUCTS}
    todays = [o for o in proposals if o.order_date == today]
    future = [o for o in proposals if o.order_date > today]
    _, snap, _ = simulate(today, horizon_end, sim_state, jobs, dem, new_orders=todays + future, record=False)

    # 5) 출력
    lines = [f"# {today} 자재 발주안 — {st['team']} ({rnd})", ""]
    lines += [f"> ⚠️ {w}" for w in warn] + ([""] if warn else [])
    lines += ["## 오늘 입력할 값 (Material 화면, 19:30 전)", "",
              "자재 Auto material planning 이 **OFF** 인지 먼저 확인 (ON이면 칸이 잠김).", "",
              sheets.md_table(["칸", "자재·유형", "도착", "수량"], [
                  ["m1_d_1", "M1 캔 일반", str(today + dt.timedelta(days=3)), form_vals["m1_d_1"]],
                  ["m2_n_1", "M2 원두 일반", str(today + dt.timedelta(days=8)), form_vals["m2_n_1"]],
                  ["m2_u_1", "M2 원두 긴급", str(today + dt.timedelta(days=2)), form_vals["m2_u_1"]],
                  ["m3_d_1", "M3 병 일반", str(today + dt.timedelta(days=5)), form_vals["m3_d_1"]],
              ]), ""]
    cost_today = sum(o.qty * C.UNIT_COST[(o.material, o.kind)] + C.ORDER_COST[o.material] for o in todays)
    lines += [f"오늘 발주 비용(구매비+주문비): 약 {cost_today / 1e6:,.1f}백만 원", ""]
    if future:
        lines += ["## 앞으로 필요한 발주 (참고 — 매일 다시 계산)", "",
                  sheets.md_table(["발주일", "자재", "유형", "수량", "도착"],
                                  [[str(o.order_date), o.material, o.kind, o.qty, str(o.arrival)] for o in future]), ""]
    if shortages:
        lines += ["## 생산이 모자라는 날 (발주로 못 막음)", "",
                  sheets.md_table(["날짜", "제품", "막힌 자재", "생산 부족(개)", "이유"],
                                  [[str(s["date"]), s["product"], s["material"], s["short_units"], s["reason"]]
                                   for s in shortages]), ""]
    lines += ["## 생산계획 가정 (job 수, *=추정)", "",
              sheets.md_table(["날짜", "P1", "P2"], [[sheets.date_label(d),
                                                      f"{jobs['P1'][d]}{'*' if est['P1'][d] else ''}",
                                                      f"{jobs['P2'][d]}{'*' if est['P2'][d] else ''}"] for d in days]), "",
              "## 자재 재고 추이 (운영 후, 발주안 반영)", "",
              sheets.md_table(["날짜", "M1", "M2", "M3", "P1 결품", "P2 결품"],
                              [[sheets.date_label(x["date"]), x["material"]["M1"], x["material"]["M2"], x["material"]["M3"],
                                x["stockout"]["P1"], x["stockout"]["P2"]] for x in snap]), "",
              "## 입력 후", "", "1. Material plans 목록에 오늘 날짜 MANUAL 행이 생겼는지 확인",
              "2. `python scripts/daily_orders.py --record` 로 state.json 에 기록", ""]
    OUT.mkdir(exist_ok=True)
    md = OUT / f"daily_{today}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    sheets.save_json(sheets.daily_form(form_vals, {"P1": 0.0, "P2": 0.0}), OUT / f"daily_{today}_form.json")
    print("\n".join(lines))
    print(f"\nsaved {md}")

    if args.record:
        S.record_orders(st, todays)
        S.save(st)
        print(f"state.json 에 오늘 발주 {len(todays)}건 기록")


if __name__ == "__main__":
    main()
