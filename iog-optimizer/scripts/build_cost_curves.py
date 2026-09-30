"""요일별 생산비곡선 생성 → data/cost_curves.csv

사용: python scripts/build_cost_curves.py [--method neh|spt]
처리시간표나 비용 파라미터가 바뀌면 다시 돌릴 것. (numba 있으면 1~2분)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iog.costs import CURVE_PATH, build_curves  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="neh", choices=["neh", "spt"])
    ap.add_argument("--out", default=str(CURVE_PATH))
    args = ap.parse_args()
    t0 = time.time()
    df = build_curves(method=args.method)
    df.to_csv(args.out, index=False)
    print(f"saved {args.out} ({len(df)} rows, {time.time() - t0:.0f}s)")
    # 요약: 85 jobs / 280 jobs 기준 개당 생산비
    for n in (85, 280):
        sub = df[df["n_jobs"] == n].copy()
        sub["won_per_unit"] = (sub["cost"] / (n * 1000)).round(1)
        print(f"\n개당 생산비 @ {n} jobs")
        print(sub.pivot(index="weekday", columns="product", values="won_per_unit")
              .reindex(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]).to_string())


if __name__ == "__main__":
    main()
