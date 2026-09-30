"""비용식·시뮬레이터가 게임 장부와 같은지 고정 (보존 검사 포함)."""
import datetime as dt

import pytest

from iog import config as C
from iog.costs import labor_cost, production_cost
from iog.simulator import DayPlan, Order, SimState, conservation_check, simulate, summarize

D0927 = dt.date(2026, 9, 27)
P2_DEMAND_0927 = 84856


def test_labor_cost_ledger_0927():
    # 장부: 76000 items (labor: 11500000, ext: 4500000, setup: 4000000)
    c = production_cost("P2", 11588, 76)
    assert (c["labor"], c["ext"], c["setup"], c["total"]) == (11_500_000, 4_500_000, 4_000_000, 20_000_000)


def test_labor_cost_manual_example():
    # 매뉴얼 예: 기준 7000, 실적 9363 → 인건비 11,600,000, 총 16,600,000 (셋업 5,000,000)
    lab = labor_cost(9363)
    assert lab["total"] == 11_600_000
    assert production_cost("P1", 9363, 10)["total"] == 16_600_000


def test_no_production_no_setup():
    assert production_cost("P1", 0, 0)["total"] == 0


@pytest.mark.parametrize("jobs,ms,expected", [
    (76, 11588, 390_000),      # 26_DGU_KSH
    (85, 12766, 3_335_280),    # AUTO 팀들 (공동 1위)
    (84, 12638, 3_022_000),    # 26_DGU_PROF
])
def test_simulator_reproduces_0927_balances(jobs, ms, expected):
    auto_orders = [Order("M1", D0927, "normal", 300_000, "AUTO"), Order("M3", D0927, "normal", 500_000, "AUTO")]
    ledger, days, _ = simulate(D0927, D0927, SimState(), {"P2": {D0927: DayPlan(jobs, makespan=ms)}},
                               {"P2": {D0927: P2_DEMAND_0927}}, new_orders=auto_orders)
    assert summarize(ledger)["net"] == expected


def test_ledger_lines_match_game_format():
    ledger, _, _ = simulate(D0927, D0927, SimState(), {"P2": {D0927: DayPlan(76, makespan=11588)}},
                            {"P2": {D0927: P2_DEMAND_0927}})
    by = {r["action"]: r for r in ledger}
    assert by["STOCKOUT_P2"]["expense"] == 8856 * 250
    assert by["MATERIAL_INVENTORY_M2"]["expense"] == 2_848_000
    assert by["MATERIAL_INVENTORY_M3"]["expense"] == 424_000 * 2
    assert by["SALES_P2"]["revenue"] == 76_000 * 350


def test_material_limited_production_and_conservation():
    start = SimState(material={"M1": 41_000, "M2": 2_000_000, "M3": 300_000}, fg={"P1": 0, "P2": 0})
    d1, d2 = dt.date(2026, 9, 29), dt.date(2026, 9, 30)
    orders = [Order("M1", dt.date(2026, 9, 27), "normal", 300_000, "AUTO")]  # 9/30 도착
    plans = {"P1": {d1: 265, d2: 280}, "P2": {d1: 74, d2: 73}}
    dem = {"P1": {d1: 280_000, d2: 280_000}, "P2": {d1: 84_000, d2: 84_000}}
    start.orders = orders
    ledger, days, _ = simulate(d1, d2, start, plans, dem)
    assert days[0]["produced"]["P1"] == 41_000          # 캔이 41k 뿐
    assert days[1]["produced"]["P1"] == 280_000         # 9/30 입고 후 정상
    conservation_check(start, days, orders)


def test_purchase_cost_charged_on_arrival_by_default():
    assert C.PURCHASE_COST_AT == "arrival"
    o = Order("M2", dt.date(2026, 9, 28), "urgent", 750_000)
    ledger, _, _ = simulate(dt.date(2026, 9, 28), dt.date(2026, 9, 30), SimState(), {}, {}, new_orders=[o])
    buys = [r for r in ledger if r["action"] == "PURCHASE_M2"]
    assert len(buys) == 1 and buys[0]["date"] == "2026-09-30"
    assert buys[0]["expense"] == 750_000 * 50 + 3_000_000
