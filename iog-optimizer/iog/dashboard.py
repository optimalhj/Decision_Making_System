"""게임 대시보드 HTML 파싱 (+ 선택적으로 공개 페이지 가져오기).

- 로그인 없이 보이는 페이지: 재고 요약, 생산계획 표, 판매 재고, 장부, 수요 차트, 팀 순위
- 로그인해야 보이는 것: Material plans(발주 목록), Sales plans
  → 로그인한 브라우저에서 페이지를 저장(Ctrl+S)해서 --html-dir 로 넘기면 발주 목록도 읽힌다.
비밀번호·인증키는 이 코드 어디에도 넣지 않는다.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd

BASE = "http://play.ioground.kr:5005"
PAGES = ("plans_material", "plans_production", "plans_sales", "plans_ledger", "plans_demand")


def board_url(page: str, univ: str, team: str, round_name: str) -> str:
    return f"{BASE}/{page}/board/{univ}/{team}/{round_name}"


def fetch(page: str, univ: str, team: str, round_name: str, timeout: int = 20) -> str:
    """공개 대시보드 1페이지 GET. 하루 몇 번만 쓸 것."""
    import requests

    r = requests.get(board_url(page, univ, team, round_name), timeout=timeout,
                     headers={"User-Agent": "iog-optimizer (team dashboard sync)"})
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(io.StringIO(html))
    except ValueError:
        return []


def _num(x) -> float:
    """'1,234' / '$-25,514,000' / 'Loading...  3335280' / None → 숫자 (없으면 0)."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return 0.0 if pd.isna(x) else float(x)
    s = str(x).replace(",", "").replace("$", "")
    found = re.findall(r"-?\d+(?:\.\d+)?", s)
    return float(found[-1]) if found else 0.0


def logged_in_team(html: str) -> str | None:
    text = re.sub(r"<[^>]+>", "", html)
    m = re.search(r"Logged in as\s+([A-Z]+)\s*/\s*([\w\-]+)", text)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def sys_today(html: str) -> str | None:
    m = re.search(r"Sys Today\([A-Z]*\):\s*(\d{4}-\d{2}-\d{2})", html)
    return m.group(1) if m else None


def parse_material(html: str) -> dict:
    out = {"on_hand": {}, "open_orders": [], "orders_visible": False, "movements": []}
    for t in _tables(html):
        cols = [str(c) for c in t.columns]
        if "Current level" in cols:
            for r in t.itertuples(index=False):
                out["on_hand"][str(r[0])] = int(_num(r[cols.index("Current level")]))
        elif "Scheduled arrival date" in cols:
            out["orders_visible"] = True
            for _, r in t.iterrows():
                delivered = str(r.get("Delivered date", "None"))
                out["open_orders" if delivered in ("None", "nan", "") else "movements"].append({
                    "material": str(r["Material type"]), "order_date": str(r["Order date"])[:10],
                    "arrival": str(r["Scheduled arrival date"])[:10], "type": str(r["Order type"]).lower(),
                    "qty": int(_num(r["Qty"])), "source": str(r.get("In type", "")),
                    "delivered": delivered,
                })
    return out


def parse_sales(html: str) -> dict:
    out = {"fg_on_hand": {}, "promotions": []}
    for t in _tables(html):
        cols = [str(c) for c in t.columns]
        if "Current level" in cols and "Product" in cols:
            for r in t.itertuples(index=False):
                out["fg_on_hand"][str(r[0])] = int(_num(r[cols.index("Current level")]))
        elif "Promotion ratio" in cols:
            out["promotions"] = t.to_dict("records")
    return out


def parse_production(html: str) -> pd.DataFrame:
    for t in _tables(html):
        if "Planned #Jobs" in [str(c) for c in t.columns]:
            t = t.copy()
            t["Date"] = t["Date"].astype(str).str[:10]
            t["Planned #Jobs"] = t["Planned #Jobs"].map(_num).astype(int)
            t["Produced Qty"] = t["Produced Qty"].map(_num).astype(int)
            t["Makespan"] = t["Makespan"].map(_num)
            return t
    return pd.DataFrame()


def parse_ledger(html: str) -> pd.DataFrame:
    for t in _tables(html):
        if "Action" in [str(c) for c in t.columns]:
            t = t.copy()
            t["Revenue"] = t["Revenue"].map(_num)
            t["Expense"] = t["Expense"].map(_num)
            t["Date"] = t["Date"].astype(str).str[:10]
            return t
    return pd.DataFrame()


def _plotly_json(html: str, var_name: str = "graphs") -> dict | None:
    m = re.search(rf"var\s+{var_name}\s*=\s*", html)
    if not m:
        return None
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(html[m.end():])
    return obj


def parse_demand(html: str) -> pd.DataFrame:
    """수요 차트(chart_p1)에서 실수요·우리 예측 추출 → columns: date, product, actual, forecast."""
    g = _plotly_json(html, "graphs")
    if not g:
        return pd.DataFrame(columns=["date", "product", "actual", "forecast"])
    rows = {}
    for tr in g.get("data", []):
        name = tr.get("name", "")
        m = re.match(r"(Actual|Forecasted)\((P\d)\)", name)
        if not m:
            continue
        kind, p = m.group(1), m.group(2)
        for x, y in zip(tr.get("x", []), tr.get("y", [])):
            key = (str(x)[:10], p)
            rows.setdefault(key, {"date": key[0], "product": p, "actual": None, "forecast": None})
            rows[key]["actual" if kind == "Actual" else "forecast"] = y
    df = pd.DataFrame(rows.values())
    return df.sort_values(["product", "date"]).reset_index(drop=True) if len(df) else df


def parse_home(html: str) -> pd.DataFrame:
    for t in _tables(html):
        cols = [str(c) for c in t.columns]
        if "Team ID" in cols and "Balance" in cols:
            t = t.copy()
            t["Balance"] = t["Balance"].map(_num)
            return t
    return pd.DataFrame()


def read_saved_pages(html_dir: Path) -> dict:
    """저장한 HTML 파일들을 이름으로 구분해서 읽기 (파일명에 plans_material 등 포함)."""
    out = {}
    for f in Path(html_dir).glob("*.htm*"):
        for p in PAGES + ("home",):
            if p in f.name:
                out[p] = f.read_text(encoding="utf-8", errors="replace")
    return out
