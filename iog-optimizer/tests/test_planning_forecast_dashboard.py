"""계획(DP·MRP)·예측·대시보드 파서."""
import datetime as dt
from pathlib import Path

import pytest

from iog import config as C
from iog import dashboard as DB
from iog import forecast as F
from iog.calendar import daterange, has_demand, is_krx_trading_day
from iog.costs import CostCurves
from iog.planning import lot_size, plan_material_orders, production_outlook, targets_from_forecast, todays_form_values
from iog.simulator import Order

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def curves():
    return CostCurves()


def test_calendar_holidays():
    assert not is_krx_trading_day(dt.date(2026, 10, 5)) and not is_krx_trading_day(dt.date(2026, 10, 9))
    assert is_krx_trading_day(dt.date(2026, 10, 6))
    assert has_demand("P2", dt.date(2026, 10, 4)) and not has_demand("P1", dt.date(2026, 10, 4))


def test_lot_size_meets_demand_and_batches_p2_on_friday(curves):
    days = daterange("2026-10-04", "2026-10-12")
    need = {d: 87_000 for d in days}
    plan = lot_size("P2", days, need, curves)
    inv = 0
    for d in days:
        inv += plan[d] * 1000 - need[d]
        assert inv >= 0
    assert plan[dt.date(2026, 10, 9)] > 200            # 금요일 몰아서
    assert plan[dt.date(2026, 10, 10)] == 0 and plan[dt.date(2026, 10, 11)] == 0


def test_lot_size_p1_never_on_weekend(curves):
    days = daterange("2026-10-04", "2026-10-10")
    need = {d: (300_000 if has_demand("P1", d) else 0) for d in days}
    plan = lot_size("P1", days, need, curves)
    assert plan[dt.date(2026, 10, 4)] == 0 and plan[dt.date(2026, 10, 10)] == 0


def test_production_outlook_nets_finished_goods(curves):
    """추정 구간은 그때까지 남을 완제품 재고만큼 덜 만든다 (9/29 수요 누락으로 재고가 남은 경우)."""
    hist = F.load_history()
    start, end = dt.date(2026, 10, 4), dt.date(2026, 10, 8)
    base, _ = production_outlook({}, hist, curves, start, end)
    netted, est = production_outlook({}, hist, curves, start, end, start_fg={"P1": 0, "P2": 30_000})
    assert all(est["P2"].values())
    cut = sum(base["P2"].values()) - sum(netted["P2"].values())
    assert 25 <= cut <= 35
    assert netted["P1"] == base["P1"]


def test_mrp_flags_unfixable_m1_and_orders_the_rest():
    today = dt.date(2026, 9, 28)
    days = daterange(today, "2026-10-08")
    jobs = {"P1": {d: (280 if d.weekday() < 5 and is_krx_trading_day(d) else 0) for d in days},
            "P2": {d: 85 for d in days}}
    on_hand = {"M1": 300_000, "M2": 2_848_000, "M3": 424_000}
    open_orders = [Order("M1", dt.date(2026, 9, 27), "normal", 300_000, "AUTO"),
                   Order("M3", dt.date(2026, 9, 27), "normal", 500_000, "AUTO")]
    props, shorts = plan_material_orders(today, jobs, on_hand, open_orders, "2026-10-08")
    assert any(s["date"] == dt.date(2026, 9, 29) and "M1" in s["material"] for s in shorts)  # 캔은 못 막음
    vals = todays_form_values(props, today)
    assert vals["m1_d_1"] > 0          # 10/1 도착 캔
    assert all(o.order_date >= today for o in props)
    assert all(o.kind == "normal" or o.material == "M2" for o in props)


def test_targets_rounding():
    import pandas as pd
    fc = pd.DataFrame([(dt.date(2026, 10, 6), "P1", 286500, 1), (dt.date(2026, 10, 5), "P1", 0, 0)],
                      columns=["date", "product", "forecast", "h"])
    t = targets_from_forecast(fc, {"P1": 0.06, "P2": 0.02})
    assert t["P1"][dt.date(2026, 10, 6)] == 303690 and t["P1"][dt.date(2026, 10, 5)] == 0


def test_plan_forecast_horizons():
    hist = F.load_history()
    fc = F.plan_forecast(hist, dt.date(2026, 9, 19), "naive")      # 9/20~9/26 주
    p1 = fc[fc["product"] == "P1"].set_index("date")
    assert p1.loc[dt.date(2026, 9, 21), "h"] == 1 and p1.loc[dt.date(2026, 9, 21), "forecast"] == 260000
    assert p1.loc[dt.date(2026, 9, 24), "forecast"] == 0           # 추석 휴장 → 0
    p2 = fc[fc["product"] == "P2"]
    assert p2["h"].max() == 7


def test_dashboard_parsers():
    pages = DB.read_saved_pages(FIX)
    mat = DB.parse_material(pages["plans_material"])
    assert mat["on_hand"] == {"M1": 300000, "M2": 2848000, "M3": 424000}
    assert mat["orders_visible"] and len(mat["open_orders"]) == 5
    lg = DB.parse_ledger(pages["plans_ledger"])
    assert lg["Revenue"].sum() - lg["Expense"].sum() == 390000
    pr = DB.parse_production(pages["plans_production"])
    assert int(pr[(pr["Date"] == "2026-09-27") & (pr["Product"] == "P2")]["Makespan"].iloc[0]) == 11588
    dm = DB.parse_demand(pages["plans_demand"])
    row = dm[(dm["date"] == "2026-09-27") & (dm["product"] == "P2")].iloc[0]
    assert row["actual"] == 84856 and row["forecast"] == 75500
    home = DB.parse_home(pages["home"])
    ksh = home[home["Team ID"] == "26_DGU_KSH"].iloc[0]
    assert ksh["Balance"] == 390000
    assert DB.logged_in_team(pages["plans_material"]) == "DGU/26_DGU_KSH"
