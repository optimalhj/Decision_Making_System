"""게임 입력 화면에 넣을 값 정리 (마크다운 + 화면 칸 이름 기준 JSON).

화면 칸 이름 (2026-09-28 확인)
- 수요예측  /plans_demand/frm/...     : autop(0/1), ma_by, p1_YYYY-MM-DD, p2_YYYY-MM-DD   (다음 주 일~토)
- 생산      /plans_production/frm/... : autop, spt_by, type_Px_DATE(seq|qty), seq_Px_DATE, qty_Px_DATE (다음 주)
- 자재      /plans_material/frm/...   : autop, m1_d_1, m2_n_1, m2_u_1, m3_d_1                (오늘 발주분만)
- 판매      /plans_sales/frm/...      : sales_p1, sales_p2 (0.0~1.0, 다음 날 할인율)
Auto 가 ON 이면 수동 칸이 읽기 전용이 되므로, 수동 값을 넣으려면 해당 화면의 Auto 를 OFF 로 바꿔야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .calendar import to_date


def _wd(d) -> str:
    return "월화수목금토일"[to_date(d).weekday()]


def weekly_form(fc_rows, prod_rows) -> dict:
    """fc_rows: [(date, product, forecast)], prod_rows: [(date, product, n_jobs, seq_text)]."""
    demand = {"autop": 0}
    for d, p, f in fc_rows:
        demand[f"{p.lower()}_{to_date(d).isoformat()}"] = int(f)
    production = {"autop": 0}
    for d, p, n, seq_text in prod_rows:
        key = f"{p}_{to_date(d).isoformat()}"
        if n > 0:
            production[f"type_{key}"] = "seq"
            production[f"seq_{key}"] = seq_text
        else:
            production[f"type_{key}"] = "qty"
            production[f"qty_{key}"] = 0
    return {"plans_demand": demand, "plans_production": production}


def daily_form(material_vals: dict, discounts: dict | None = None) -> dict:
    out = {"plans_material": {"autop": 0, **{k: int(v) for k, v in material_vals.items()}}}
    if discounts is not None:
        out["plans_sales"] = {"sales_p1": float(discounts.get("P1", 0.0)),
                              "sales_p2": float(discounts.get("P2", 0.0))}
    return out


def save_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def md_table(headers: list[str], rows: list[list]) -> str:
    def fmt(x):
        if isinstance(x, float):
            return f"{x:,.1f}" if abs(x) < 1000 else f"{x:,.0f}"
        if isinstance(x, int):
            return f"{x:,}"
        return str(x)
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def date_label(d) -> str:
    d = to_date(d)
    return f"{d:%m/%d}({_wd(d)})"
