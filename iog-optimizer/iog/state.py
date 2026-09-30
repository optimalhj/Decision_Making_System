"""팀 상태 파일(data/state.json) 읽기·쓰기.

as_of = 그날 20:00 운영까지 반영된 마지막 날짜. 재고는 'as_of 운영 후' 값.
production_plan = 게임에 이미 입력된(=바꿀 수 없는) 생산 job 수. 계획을 새로 넣으면 여기도 갱신.
open_orders = 아직 입고 안 된 발주 (게임 Material plans 에서 Delivered date 가 None 인 것).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import config as C
from .calendar import to_date
from .simulator import Order, SimState

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state.json"


def load(path: Path = STATE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(state: dict, path: Path = STATE_PATH) -> None:
    state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def to_sim_state(state: dict) -> SimState:
    return SimState(
        material={m: int(state["material_on_hand"][m]) for m in C.MATERIALS},
        fg={p: int(state["fg_on_hand"].get(p, 0)) for p in C.PRODUCTS},
        orders=[Order.from_dict(o) for o in state.get("open_orders", [])
                if to_date(o["arrival"]) > to_date(state["as_of"])],
    )


def fixed_plans(state: dict) -> dict:
    """{product: {date: jobs}} — 이미 입력된 생산계획."""
    out = {}
    for p, byday in state.get("production_plan", {}).items():
        out[p] = {to_date(d): int(n) for d, n in byday.items()}
    return out


def record_orders(state: dict, orders: list[Order]) -> dict:
    existing = {(o["material"], o["order_date"], o["type"]) for o in state.get("open_orders", [])}
    for o in orders:
        d = o.to_dict()
        key = (d["material"], d["order_date"], d["type"])
        if key in existing:  # 같은 날 같은 칸은 게임에서도 덮어쓰기됨
            state["open_orders"] = [x for x in state["open_orders"]
                                    if (x["material"], x["order_date"], x["type"]) != key]
        state.setdefault("open_orders", []).append(d)
    return state


def record_production_plan(state: dict, product: str, plan: dict) -> dict:
    pp = state.setdefault("production_plan", {}).setdefault(product, {})
    for d, n in plan.items():
        pp[to_date(d).isoformat()] = int(n)
    return state
