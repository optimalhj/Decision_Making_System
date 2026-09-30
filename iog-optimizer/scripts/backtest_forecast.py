"""예측 방법 비교 (롤링 백테스트, 토요일 계획 흉내).

사용:
  python scripts/backtest_forecast.py                 # 선택용 기간만 (모델 고르는 단계)
  python scripts/backtest_forecast.py --holdout       # 판정용 기간까지 (최종 판정 때 한 번만!)
  python scripts/backtest_forecast.py --methods naive,ses06,mymodel

주 지표: 불일치 비용(원/수요일) — 선택용 기간에서 k를 고르고, 판정용 기간에선 그 k로만 평가.
보조 지표: MAPE(지평별).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from iog import forecast as F  # noqa: E402
from iog.evaluate import grid_k, mismatch_cost  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "outputs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default=",".join(F.METHODS))
    ap.add_argument("--holdout-start", default="2026-09-01")
    ap.add_argument("--holdout", action="store_true", help="판정용 기간 평가 (마지막에 한 번만)")
    ap.add_argument("--margin", type=float, default=0.05, help="채택 마진: naive 대비 비용 이만큼 낮아야 채택")
    args = ap.parse_args()

    hist = F.load_history()
    origins = F.weekly_origins(hist)
    sel, hold = F.split_selection_holdout(origins, args.holdout_start)
    print(f"origins: selection {len(sel)}주 ({sel[0]}~{sel[-1]}), holdout {len(hold)}주")

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    rows = []
    for m in methods:
        bt = F.backtest(hist, m, sel)
        mape = F.mape_by_h(bt)
        row = {"method": m}
        for p in ("P1", "P2"):
            g = grid_k(hist, m, sel, p)
            best = g.loc[g["cost_per_day"].idxmin()]
            row[f"{p}_k"] = best["k"]
            row[f"{p}_sel_cost"] = best["cost_per_day"]
            row[f"{p}_mape_h1"] = mape[p].iloc[0]
            row[f"{p}_mape_hmax"] = mape[p].dropna().iloc[-1]
            if args.holdout and hold:
                row[f"{p}_hold_cost"] = mismatch_cost(hist, m, hold, best["k"], p)["cost_per_day"]
        rows.append(row)
    df = pd.DataFrame(rows).set_index("method")
    base = df.loc["naive"] if "naive" in df.index else None
    if base is not None:
        for p in ("P1", "P2"):
            df[f"{p}_vs_naive_sel"] = (df[f"{p}_sel_cost"] / base[f"{p}_sel_cost"] - 1).round(3)
            if args.holdout and f"{p}_hold_cost" in df:
                df[f"{p}_vs_naive_hold"] = (df[f"{p}_hold_cost"] / base[f"{p}_hold_cost"] - 1).round(3)
                df[f"{p}_overfit_gap"] = (df[f"{p}_vs_naive_hold"] - df[f"{p}_vs_naive_sel"]).round(3)
    pd.set_option("display.width", 220)
    print(df.round(2).to_string())
    if base is not None:
        print(f"\n채택 기준: 두 제품 모두 naive 대비 비용 {args.margin:.0%} 이상 낮을 것 (판정용 기간에서)")
    OUT.mkdir(exist_ok=True)
    out = OUT / ("backtest_holdout.csv" if args.holdout else "backtest_selection.csv")
    df.to_csv(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
