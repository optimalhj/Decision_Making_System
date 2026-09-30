"""게임 장부가 우리 비용식과 맞는지 매일 점검 (규칙이 바뀌었는지 감시).

사용:
  python scripts/verify_ledger.py --fetch
  python scripts/verify_ledger.py --html-dir <저장한 페이지 폴더>

점검: PRODUCTION 줄의 labor/ext/setup = costs.production_cost(생산판 makespan)
      SALES/STOCKOUT/INVENTORY 줄의 금액 = 수량 × 단가
      PURCHASE 등 처음 보는 Action 이 나오면 알려줌 (구매비 과금 시점 확인용)
하나라도 틀리면 종료코드 1.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iog import config as C  # noqa: E402
from iog import dashboard as DB  # noqa: E402
from iog import state as S  # noqa: E402
from iog.costs import production_cost  # noqa: E402

KNOWN = {f"PRODUCTION_{p}" for p in C.PRODUCTS} | {f"SALES_{p}" for p in C.PRODUCTS} | \
        {f"STOCKOUT_{p}" for p in C.PRODUCTS} | {f"SALES_INVENTORY_{p}" for p in C.PRODUCTS} | \
        {f"MATERIAL_INVENTORY_{m}" for m in C.MATERIALS}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--html-dir")
    args = ap.parse_args()
    st = S.load()
    if args.fetch:
        pages = {p: DB.fetch(p, st["university"], st["team"], st["round"]) for p in ("plans_ledger", "plans_production")}
    else:
        pages = DB.read_saved_pages(Path(args.html_dir))
    lg = DB.parse_ledger(pages["plans_ledger"])
    pr = DB.parse_production(pages["plans_production"])
    ms = {(r[0], r[3]): float(r[8]) for r in pr.itertuples(index=False)}
    bad, unknown = [], set()
    for r in lg.itertuples(index=False):
        date, rev, exp, action, desc = r[0], float(r[1]), float(r[2]), str(r[3]), str(r[4])
        if action not in KNOWN:
            unknown.add(action)
            continue
        if action.startswith("PRODUCTION_"):
            p = action.split("_")[1]
            m = re.match(r"(\d+) items \(labor: (\d+), ext: (\d+), setup: (\d+)\)", desc)
            if not m:
                bad.append((date, action, "설명 형식이 다름: " + desc))
                continue
            items, labor, ext, setup = map(int, m.groups())
            if items == 0:
                continue
            calc = production_cost(p, ms.get((date, p), 0.0), items // C.JOB_SIZE)
            if (calc["labor"], calc["ext"], calc["setup"]) != (labor, ext, setup):
                bad.append((date, action, f"장부 {labor}/{ext}/{setup} vs 계산 {calc['labor']}/{calc['ext']}/{calc['setup']} "
                                          f"(makespan {ms.get((date, p))})"))
        else:
            m = re.match(r"(\d+) items \* \$([\d.]+)", desc)
            if m:
                qty, unit = int(m.group(1)), float(m.group(2))
                amount = rev if action.startswith("SALES_") and not action.startswith("SALES_INVENTORY") else exp
                if abs(qty * unit - amount) > 0.5:
                    bad.append((date, action, f"{qty}×{unit} ≠ {amount}"))
    print(f"점검 {len(lg)}줄, 불일치 {len(bad)}건")
    for b in bad:
        print("  ✗", *b)
    if unknown:
        print("처음 보는 Action:", sorted(unknown), "→ config.py 가정(구매비 과금 시점 등)을 확인하고 simulator 에 반영할 것")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
