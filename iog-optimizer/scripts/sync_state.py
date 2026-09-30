"""대시보드에서 팀 상태를 읽어 data/state.json · data/demand_history.csv 갱신.

사용 (둘 중 하나):
  python scripts/sync_state.py --fetch                 # 공개 페이지 GET (로그인 불필요, 발주 목록은 안 보임)
  python scripts/sync_state.py --html-dir <폴더>       # 로그인한 브라우저에서 저장(Ctrl+S)한 페이지들 → 발주 목록까지
     (파일명에 plans_material / plans_production / plans_sales / plans_ledger / plans_demand 가 들어가 있으면 됨)

매일 20:00 운영이 끝나고(약 21:00 이후) 또는 다음 날 16:00 이후에 실행.
비밀번호·인증키는 쓰지 않는다. 공개 페이지는 하루 몇 번만 요청할 것.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from iog import config as C  # noqa: E402
from iog import dashboard as DB  # noqa: E402
from iog import forecast as F  # noqa: E402
from iog import state as S  # noqa: E402
from iog.calendar import to_date  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--html-dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    st = S.load()
    if args.fetch:
        pages = {p: DB.fetch(p, st["university"], st["team"], st["round"]) for p in DB.PAGES}
    else:
        pages = DB.read_saved_pages(Path(args.html_dir))
        missing = [p for p in DB.PAGES if p not in pages]
        if missing:
            print(f"경고: 없는 페이지 {missing}")

    changes = []
    # 장부 → as_of, balance
    if "plans_ledger" in pages:
        lg = DB.parse_ledger(pages["plans_ledger"])
        if len(lg):
            as_of = max(lg["Date"])
            bal = float(lg["Revenue"].sum() - lg["Expense"].sum())
            if as_of != st["as_of"]:
                changes.append(f"as_of {st['as_of']} → {as_of}")
            st["as_of"] = as_of
            st.setdefault("balance_history", {})[as_of] = bal
            changes.append(f"balance({as_of}) = {bal:,.0f}")
    # 자재
    if "plans_material" in pages:
        mat = DB.parse_material(pages["plans_material"])
        for m, v in mat["on_hand"].items():
            if st["material_on_hand"].get(m) != v:
                changes.append(f"{m} 재고 {st['material_on_hand'].get(m):,} → {v:,}")
            st["material_on_hand"][m] = v
        if mat["orders_visible"]:
            st["open_orders"] = [
                {"material": o["material"], "order_date": o["order_date"], "type": o["type"], "qty": o["qty"],
                 "arrival": o["arrival"], "source": o["source"]} for o in mat["open_orders"]]
            changes.append(f"발주 목록 {len(st['open_orders'])}건으로 교체 (로그인 페이지)")
        else:
            before = len(st.get("open_orders", []))
            st["open_orders"] = [o for o in st.get("open_orders", []) if to_date(o["arrival"]) > to_date(st["as_of"])]
            changes.append(f"발주 목록은 안 보임(비로그인) → 입고 끝난 {before - len(st['open_orders'])}건만 정리")
    # 완제품
    if "plans_sales" in pages:
        s = DB.parse_sales(pages["plans_sales"])
        for p in C.PRODUCTS:
            v = int(s["fg_on_hand"].get(p, 0))
            if st["fg_on_hand"].get(p, 0) != v:
                changes.append(f"{p} 완제품 {st['fg_on_hand'].get(p, 0):,} → {v:,}")
            st["fg_on_hand"][p] = v
    # 생산계획 (입력된 것 전부)
    if "plans_production" in pages:
        pr = DB.parse_production(pages["plans_production"])
        for r in pr.itertuples(index=False):
            p, d, n = r[3], r[0], int(r[5])
            st.setdefault("production_plan", {}).setdefault(p, {})[d] = n
        changes.append(f"생산계획 {len(pr)}행 반영")
    # 수요 이력
    if "plans_demand" in pages:
        dm = DB.parse_demand(pages["plans_demand"])
        dm = dm[dm["actual"].notna()]
        hist = F.load_history()
        new = pd.DataFrame({"date": pd.to_datetime(dm["date"]).dt.date, "product": dm["product"],
                            "demand": dm["actual"].astype(float).round().astype(int)})
        merged = F.merge_history(hist, new)
        added = len(merged) - len(hist)
        changes.append(f"수요 이력 +{added}행 (마지막: " + ", ".join(
            f"{p} {merged[merged['product'] == p]['date'].max()}" for p in C.PRODUCTS) + ")")
        if not args.dry_run:
            merged.to_csv(F.HISTORY_PATH, index=False)
    print("\n".join("- " + c for c in changes))
    if not args.dry_run:
        S.save(st)
        print("state.json 저장")


if __name__ == "__main__":
    main()
