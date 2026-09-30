"""다음 주(일~토) 수요예측 + 생산계획 + 작업순서 (토요일 19:30 전 입력).

사용:
  python scripts/weekly_plan.py                          # 오늘 기준 다음 주
  python scripts/weekly_plan.py --plan-date 2026-10-03 --method naive --ig-seconds 20
  python scripts/weekly_plan.py --record                 # 게임에 입력을 마친 뒤 state.json 에 생산계획 기록

흐름: 예측(편향 없이) → 목표 = 예측×(1+k) → 이번 주 남은 날을 굴려 다음 주 시작 재고 추정
      → 요일별 비용곡선으로 생산 요일 배분(DP, 다음 주+2일 여유) → 날짜별 IG 작업순서 → 자재 점검(MRP)
출력: outputs/weekly_<plan-date>.md, outputs/weekly_<plan-date>_form.json (입력 칸 이름 → 값)
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
from iog.calendar import can_produce, daterange, has_demand, next_week, round_of, to_date, weekday_key  # noqa: E402
from iog.costs import CostCurves, production_cost  # noqa: E402
from iog.planning import (lot_size, plan_material_orders, production_outlook, sequence_for,  # noqa: E402
                          targets_from_forecast)
from iog.scheduling import format_sequence, spt  # noqa: E402
from iog import ptimes  # noqa: E402
from iog.simulator import simulate  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "outputs"


def upcoming_saturday(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=(5 - d.weekday()) % 7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-date", default=None, help="계획 입력일(보통 토요일). 기본: 오늘 이후 첫 토요일")
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--method", default="naive", choices=list(F.METHODS))
    ap.add_argument("--k-p1", type=float, default=C.SAFETY_FACTOR["P1"])
    ap.add_argument("--k-p2", type=float, default=C.SAFETY_FACTOR["P2"])
    ap.add_argument("--ig-seconds", type=float, default=15.0, help="날짜·제품마다 IG 시간(초)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    today = to_date(args.today)
    plan_date = to_date(args.plan_date) if args.plan_date else upcoming_saturday(today)
    st = S.load()
    hist = F.load_history()
    curves = CostCurves()
    fixed = S.fixed_plans(st)
    k = {"P1": args.k_p1, "P2": args.k_p2}
    week = next_week(plan_date)
    rnd = round_of(week[0]) or round_of(plan_date) or st["round"]
    r_start, r_end = C.ROUNDS[rnd]
    week_in = [d for d in week if r_start <= d <= r_end]
    notes = []
    if not week_in:
        print(f"{week[0]}~{week[-1]} 은 라운드 기간 밖입니다 ({rnd}: {r_start}~{r_end}).")
        return
    if len(week_in) < 7:
        notes.append(f"라운드 경계: {week_in[0]}~{week_in[-1]} 만 운영에 반영됨")

    # 1) 예측 (편향 없이) + 여유 2일
    look = [week[-1] + dt.timedelta(days=i) for i in (1, 2)]
    fc = F.plan_forecast(hist, min(plan_date, max(hist["date"])), args.method, days=week + look)
    targets = targets_from_forecast(fc, k)

    # 2) 다음 주 시작 재고 추정: state.as_of 다음 날 ~ plan_date 를 입력된 계획 + 예측 수요로 굴림
    as_of = to_date(st["as_of"])
    sim_state = S.to_sim_state(st)
    if as_of < plan_date:
        gap = daterange(as_of + dt.timedelta(days=1), plan_date)
        lvl = {p: float(hist[hist["product"] == p]["demand"].iloc[-1]) for p in C.PRODUCTS}
        dem = {p: {d: (lvl[p] if has_demand(p, d) else 0) for d in gap} for p in C.PRODUCTS}
        # 실수요가 이미 있는 날은 실수요 사용
        for p in C.PRODUCTS:
            s = F.series(hist, p)
            for d in gap:
                if d in s.index:
                    dem[p][d] = float(s[d])
        _, _, sim_state = simulate(gap[0], gap[-1], sim_state, fixed, dem, record=False)
    start_fg = dict(sim_state.fg)

    # 3) 생산 요일 배분 (DP)
    plan = {}
    for p in C.PRODUCTS:
        days = [d for d in week + look if d >= week_in[0]]
        need = {d: targets[p].get(d, 0) for d in days}
        lp = lot_size(p, days, need, curves, init_fg_units=start_fg[p])
        plan[p] = {d: (lp.get(d, 0) if d in week_in else 0) for d in week}
        # 라운드 마지막 주면 라운드 끝 이후 수요를 미리 만들지 않도록 여유일 없이 다시 계산
        if week_in[-1] == r_end:
            days2 = week_in
            lp2 = lot_size(p, days2, {d: targets[p].get(d, 0) for d in days2}, curves, init_fg_units=start_fg[p])
            plan[p] = {d: lp2.get(d, 0) for d in week_in}

    # 4) 작업순서 (IG) + 비용 비교
    rows_md, prod_rows, total = [], [], {"spt": 0.0, "ig": 0.0}
    seq_out = {}
    for d in week:
        for p in C.PRODUCTS:
            n = int(plan[p].get(d, 0))
            if n <= 0 or not can_produce(p, d):
                prod_rows.append((d, p, 0, ""))
                continue
            seq, ms, info = sequence_for(p, d, n, "ig", args.ig_seconds, args.seed)
            s_seq, s_ms = spt(ptimes.first_n(p, d, n))
            c_ig = production_cost(p, ms, n)["total"]
            c_spt = production_cost(p, s_ms, n)["total"]
            total["ig"] += c_ig
            total["spt"] += c_spt
            text = format_sequence(seq)
            seq_out[f"{p}_{d}"] = {"jobs": n, "makespan": ms, "cost": c_ig, "spt_makespan": s_ms, "sequence": text}
            prod_rows.append((d, p, n, text))
            rows_md.append([sheets.date_label(d), p, n, f"{ms:,.0f}", f"{s_ms:,.0f}", c_ig / 1e6, (c_spt - c_ig) / 1e6])
            print(f"  {d} {p}: {n} jobs  IG {ms:,.0f} (SPT {s_ms:,.0f})  iters={info.get('iters')}")

    # 5) 자재 점검 (오늘부터 다음 주 끝까지)
    horizon_end = min(week_in[-1] + dt.timedelta(days=3), r_end)
    fixed2 = {p: dict(fixed.get(p, {})) for p in C.PRODUCTS}
    for p in C.PRODUCTS:
        fixed2[p].update({d: n for d, n in plan[p].items()})
    jobs, est = production_outlook(fixed2, hist, curves, today, horizon_end, start_fg=dict(S.to_sim_state(st).fg))
    mat_state = S.to_sim_state(st)
    if as_of < today - dt.timedelta(days=1):
        notes.append(f"state.json 이 {as_of} 기준 — sync_state.py 로 갱신 권장 (자재 점검이 부정확할 수 있음)")
    proposals, shortages = plan_material_orders(today, jobs, mat_state.material,
                                                [o for o in mat_state.orders if o.arrival >= today],
                                                horizon_end, r_end)

    # 6) 출력
    fc_week = fc[fc["date"].isin(week_in)]
    fc_rows = [(r.date, r.product, r.forecast) for r in fc_week.itertuples()
               if (r.product == "P2" or r.date.weekday() < 5)]
    lines = [f"# 주간계획 {week[0]}~{week[-1]} — {st['team']} ({rnd})", "",
             f"- 입력 마감: {plan_date} (토) {C.INPUT_DEADLINE} / 예측 방법: {args.method} / k: P1 {k['P1']:+.0%}, P2 {k['P2']:+.0%}",
             f"- 다음 주 시작 완제품 재고(추정): P1 {start_fg['P1']:,} · P2 {start_fg['P2']:,}"]
    lines += [f"- ⚠️ {n}" for n in notes]
    lines += ["", "## 1. Demand forecasting 화면 (Auto forecasting → OFF 후 입력)", "",
              "휴장일은 0 (정확도 계산에서 빠짐). 예측값은 편향 없이 — 여유분은 생산량에만 들어감.", "",
              sheets.md_table(["날짜", "P1 예측", "P2 예측"],
                              [[sheets.date_label(d),
                                (int(fc_week[(fc_week.date == d) & (fc_week["product"] == "P1")]["forecast"].sum())
                                 if d.weekday() < 5 else "—"),
                                int(fc_week[(fc_week.date == d) & (fc_week["product"] == "P2")]["forecast"].sum())]
                               for d in week_in]), "",
              "## 2. Production 화면 (Auto production scheduling → OFF, 'Job sequence' 선택 후 붙여넣기)", "",
              sheets.md_table(["날짜", "제품", "jobs", "makespan(IG)", "makespan(SPT)", "생산비(백만)", "SPT 대비 절감(백만)"],
                              rows_md), "",
              f"주간 생산비: IG {total['ig'] / 1e6:,.1f}백만 / SPT 였다면 {total['spt'] / 1e6:,.1f}백만 "
              f"(절감 {(total['spt'] - total['ig']) / 1e6:,.1f}백만)", "",
              "순서 문자열은 outputs/weekly_*_form.json 의 seq_* 값을 그대로 복사.", "",
              "## 3. 자재 점검 (오늘부터)", ""]
    if proposals:
        lines += [sheets.md_table(["발주일", "자재", "유형", "수량", "도착"],
                                  [[str(o.order_date), o.material, o.kind, o.qty, str(o.arrival)] for o in proposals]), ""]
    else:
        lines += ["추가 발주 필요 없음.", ""]
    if shortages:
        lines += ["생산이 모자라는 날:", "",
                  sheets.md_table(["날짜", "제품", "막힌 자재", "부족(개)", "이유"],
                                  [[str(s["date"]), s["product"], s["material"], s["short_units"], s["reason"]]
                                   for s in shortages]), ""]
    lines += ["## 입력 후", "", "1. Production 목록에 다음 주 MANUAL 행과 job 수가 맞게 들어갔는지 확인",
              "2. `python scripts/weekly_plan.py --plan-date ... --record` (같은 인자) 로 state.json 에 기록", ""]
    OUT.mkdir(exist_ok=True)
    md = OUT / f"weekly_{plan_date}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    form = sheets.weekly_form(fc_rows, [r for r in prod_rows if r[0] in week_in])
    sheets.save_json({"week": [str(week_in[0]), str(week_in[-1])], "forms": form, "details": seq_out},
                     OUT / f"weekly_{plan_date}_form.json")
    print("\n".join(lines))
    print(f"saved {md}")

    if args.record:
        for p in C.PRODUCTS:
            S.record_production_plan(st, p, {d: n for d, n in plan[p].items() if d in week_in})
        S.save(st)
        print("state.json 에 다음 주 생산계획 기록")


if __name__ == "__main__":
    main()
