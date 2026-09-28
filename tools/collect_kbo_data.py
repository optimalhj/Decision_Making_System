"""
collect_kbo_data.py
--------------------
KBO 공식 기록실(koreabaseball.com)에서 "오늘 기준" 현재 시즌 데이터를 수집해
kbo_sim/data_snapshot/ 에 날짜가 찍힌 4개 CSV(teams_YYYYMMDD.csv, batters_YYYYMMDD.csv,
pitchers_YYYYMMDD.csv, matchup_YYYYMMDD.csv)로 저장한다.

koreabaseball.com은 ASP.NET WebForms 사이트라 일반적인 "URL에 쿼리 붙여서 GET" 방식이
아니라 페이지의 __VIEWSTATE/__EVENTVALIDATION을 유지한 채로 폼 postback을 흉내내야 한다.
이 스크립트는 requests.Session 하나로 그 상태를 이어가며 다음 순서로 수집한다:

  1. teams  : 각 통계 페이지의 팀 드롭다운(ddlTeam) 옵션 + 고정 구단 정식명칭표
  2. batters/pitchers : 선수기록 페이지(HitterBasic/PitcherBasic Basic1·Basic2)를
     "팀 필터"를 걸어서 조회한다. 팀을 지정하지 않은 기본 화면은 KBO 공식 규정타석/규정이닝
     기준 상위 선수만 보여주기 때문에(예: 2026시즌 전체 48명), 팀별로 걸러야 벤치 선수까지
     포함한 전체 로스터가 나온다.
  3. bio(등번호/포지션/투타/생년월일/체격) : Player/Register.aspx(구단별 등록현황)에서
     팀별로 한 번에 가져온다. 이 페이지가 대부분의 리그 웹사이트와 달리 투타유형까지
     같이 보여주는 유일한 목록형 페이지라, 선수별 상세페이지를 586번 따로 열 필요가 없다.
  4. matchup(투수 vs 타자 상대전적) : koreabaseball.com에는 이 데이터를 대량으로 받는
     방법이 없다 - "투수 1명 + 타자 1명"을 반드시 지정해야 하는 1:1 조회 폼뿐이다. 따라서
     팀이 다른 모든 투수x타자 조합(대략 7~8만 쌍)을 하나씩 조회해야 하며, 사이트에 부담을
     주거나 차단당하지 않도록 기본적으로 쉬어가며(--delay) 적은 동시 연결(--workers)로
     수집하고, 중간에 끊겨도 --resume으로 이어받을 수 있게 체크포인트를 남긴다. 이 단계가
     압도적으로 오래 걸린다(수 시간 단위) - 필요 없으면 --no-matchup으로 건너뛸 것.

사용 예:
    python tools/collect_kbo_data.py                    # teams/batters/pitchers만 (수 분)
    python tools/collect_kbo_data.py --matchup           # 상대전적까지 전수 수집 (수 시간)
    python tools/collect_kbo_data.py --matchup --resume  # 중단된 상대전적 수집 이어하기
    python tools/collect_kbo_data.py --limit-teams SS,KT # 특정 팀만 (동작 검증용)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(ROOT, "kbo_sim", "data_snapshot")

BASE = "https://www.koreabaseball.com"
URL_HITTER = {1: f"{BASE}/Record/Player/HitterBasic/Basic1.aspx", 2: f"{BASE}/Record/Player/HitterBasic/Basic2.aspx"}
URL_PITCHER = {1: f"{BASE}/Record/Player/PitcherBasic/Basic1.aspx", 2: f"{BASE}/Record/Player/PitcherBasic/Basic2.aspx"}
URL_REGISTER = f"{BASE}/player/register.aspx"
URL_HITVSPIT = f"{BASE}/Record/Etc/HitVsPit.aspx"

PREFIX = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36 (KBO data collector for a school sim project)"}

# koreabaseball.com은 구단 명칭 변경(연고이전/모기업 변경)이 몇 년에 한 번 있을까 말까 할
# 정도로 드물고, 정식명칭(예: "삼성 라이온즈")은 드롭다운 어디에도 텍스트로 노출되지 않아
# 매번 다시 수집할 방법이 없다. 구단 코드/약칭은 매 실행마다 사이트에서 그대로 읽어오되,
# 정식명칭만 고정표로 보완한다 (구단이 실제로 바뀌면 이 표만 수정하면 됨).
TEAM_FULLNAME = {
    "SS": "삼성 라이온즈", "KT": "KT 위즈", "LG": "LG 트윈스", "HT": "KIA 타이거즈",
    "OB": "두산 베어스", "NC": "NC 다이노스", "LT": "롯데 자이언츠", "SK": "SSG 랜더스",
    "HH": "한화 이글스", "WO": "키움 히어로즈",
}

# data-id(사이트 내부 필드명) -> 우리 CSV 컬럼명
HITTER1_MAP = {"HRA_RT": "AVG", "GAME_CN": "G", "PA_CN": "PA", "AB_CN": "AB", "RUN_CN": "R",
               "HIT_CN": "H", "H2_CN": "2B", "H3_CN": "3B", "HR_CN": "HR", "TB_CN": "TB",
               "RBI_CN": "RBI", "SH_CN": "SAC", "SF_CN": "SF"}
HITTER2_MAP = {"BB_CN": "BB", "IB_CN": "IBB", "HP_CN": "HBP", "KK_CN": "SO", "GD_CN": "GDP",
               "SLG_RT": "SLG", "OBP_RT": "OBP", "OPS_RT": "OPS", "MH_HITTER_CN": "MultiHit",
               "SP_HRA_RT": "RISP_AVG", "PH_HRA_RT": "PH_AVG"}
PITCHER1_MAP = {"ERA_RT": "ERA", "GAME_CN": "G", "W_CN": "W", "L_CN": "L", "SV_CN": "SV",
                "HOLD_CN": "HLD", "WRA_RT": "WPCT", "INN2_CN": "IP", "HIT_CN": "H", "HR_CN": "HR",
                "BB_CN": "BB", "HP_CN": "HBP", "KK_CN": "SO", "R_CN": "R", "ER_CN": "ER", "WHIP_RT": "WHIP"}
PITCHER2_MAP = {"CG_CN": "CG", "SHO_CN": "SHO", "QS_CN": "QS", "BS_CN": "BSV", "PA_CN": "TBF",
                "PIT_CN": "NP", "OAVG_RT": "OAVG", "H2_CN": "2B", "H3_CN": "3B", "SH_CN": "SAC",
                "SF_CN": "SF", "IB_CN": "IBB", "WP_CN": "WP", "BK_CN": "BK"}

BATTER_COLUMNS = ["pCode", "team", "name", "displayId", "backNo", "position", "throwBat", "birthday",
                  "heightWeight", "AVG", "G", "PA", "AB", "R", "H", "2B", "3B", "HR", "TB", "RBI", "SAC",
                  "SF", "BB", "IBB", "HBP", "SO", "GDP", "SLG", "OBP", "OPS", "MultiHit", "RISP_AVG", "PH_AVG"]
PITCHER_COLUMNS = ["pCode", "team", "name", "displayId", "backNo", "position", "throwBat", "birthday",
                   "heightWeight", "ERA", "G", "W", "L", "SV", "HLD", "WPCT", "IP", "H", "HR", "BB", "HBP",
                   "SO", "R", "ER", "WHIP", "CG", "SHO", "QS", "BSV", "TBF", "NP", "OAVG", "2B", "3B",
                   "SAC", "SF", "IBB", "WP", "BK"]
MATCHUP_COLUMNS = ["pitcherPCode", "hitterPCode", "AVG", "PA", "AB", "H", "2B", "3B", "HR", "RBI",
                   "BB", "HBP", "SO", "SLG", "OBP", "OPS"]


def log(msg: str) -> None:
    print(f"[KBO수집] {msg}", flush=True)


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def request_with_retry(session: requests.Session, method: str, url: str, *, data=None,
                        max_retries: int = 5, base_backoff: float = 2.0) -> requests.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = session.request(method, url, data=data, timeout=20)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = base_backoff * (2 ** attempt) + random.uniform(0, 1)
                log(f"  {resp.status_code} 응답 - {wait:.1f}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_exc = e
            wait = base_backoff * (2 ** attempt) + random.uniform(0, 1)
            log(f"  요청 실패({e}) - {wait:.1f}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
            time.sleep(wait)
    raise RuntimeError(f"{url} 요청이 {max_retries}번 모두 실패했습니다") from last_exc


def build_form(soup: BeautifulSoup) -> dict:
    data = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        t = (inp.get("type") or "text").lower()
        if t in ("submit", "button", "image"):
            continue
        data[name] = inp.get("value", "")
    for sel in soup.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = opt.get("value", "") if opt else ""
    return data


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    resp = request_with_retry(session, "GET", url)
    return BeautifulSoup(resp.text, "lxml")


def postback(session: requests.Session, url: str, soup: BeautifulSoup, event_target: str,
             overrides: Optional[dict] = None) -> BeautifulSoup:
    data = build_form(soup)
    data["__EVENTTARGET"] = event_target
    data["__EVENTARGUMENT"] = ""
    if overrides:
        data.update(overrides)
    resp = request_with_retry(session, "POST", url, data=data)
    return BeautifulSoup(resp.text, "lxml")


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------

def collect_teams(session: requests.Session) -> List[dict]:
    soup = get_soup(session, URL_HITTER[1])
    sel = soup.find("select", {"name": PREFIX + "ddlTeam$ddlTeam"})
    teams = []
    for opt in sel.find_all("option"):
        code = opt.get("value")
        if not code:
            continue
        name = opt.text.strip()
        full = TEAM_FULLNAME.get(code)
        if not full:
            log(f"  경고: 구단 코드 '{code}'({name})의 정식명칭이 고정표에 없습니다. "
                f"TEAM_FULLNAME에 추가해 주세요.")
            full = name
        teams.append({"teamCode": code, "teamName": name, "teamFullName": full})
    return teams


# ---------------------------------------------------------------------------
# batter / pitcher stat tables
# ---------------------------------------------------------------------------

def parse_stat_rows(soup: BeautifulSoup, detail_path: str, col_map: dict) -> Dict[int, dict]:
    table = soup.find("table", class_="tData01")
    if table is None or table.tbody is None:
        return {}
    rows = {}
    for tr in table.tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        link = tds[1].find("a")
        if link is None or detail_path not in (link.get("href") or ""):
            continue
        m = re.search(r"playerId=(\d+)", link["href"])
        if not m:
            continue
        pcode = int(m.group(1))
        team = tds[2].get_text(strip=True)
        values = {}
        for td in tds:
            data_id = td.get("data-id")
            if data_id and data_id in col_map:
                values[col_map[data_id]] = td.get_text(strip=True)
        rows[pcode] = {"pCode": pcode, "team": team, "name": link.get_text(strip=True), **values}
    return rows


def collect_team_stat_page(session: requests.Session, url: str, team_code: str,
                            detail_path: str, col_map: dict, max_pages: int = 20) -> Dict[int, dict]:
    soup = get_soup(session, url)
    soup = postback(session, url, soup, PREFIX + "ddlTeam$ddlTeam", {PREFIX + "ddlTeam$ddlTeam": team_code})
    all_rows: Dict[int, dict] = {}
    page = 1
    while page <= max_pages:
        page_rows = parse_stat_rows(soup, detail_path, col_map)
        if not page_rows:
            break
        all_rows.update(page_rows)
        next_page = page + 1
        next_btn = soup.find("a", id=re.compile(rf"ucPager_btnNo{next_page}$"))
        if next_btn is None:
            break
        soup = postback(session, url, soup, PREFIX + f"ucPager$btnNo{next_page}")
        page = next_page
    return all_rows


def collect_players(session: requests.Session, team_codes: List[str], kind: str) -> Dict[int, dict]:
    urls = URL_HITTER if kind == "batter" else URL_PITCHER
    detail_path = "HitterDetail" if kind == "batter" else "PitcherDetail"
    map1 = HITTER1_MAP if kind == "batter" else PITCHER1_MAP
    map2 = HITTER2_MAP if kind == "batter" else PITCHER2_MAP
    merged: Dict[int, dict] = {}
    for team_code in team_codes:
        team_name = None
        page1 = collect_team_stat_page(session, urls[1], team_code, detail_path, map1)
        page2 = collect_team_stat_page(session, urls[2], team_code, detail_path, map2)
        pcodes = set(page1) | set(page2)
        for pcode in pcodes:
            row = {}
            row.update(page1.get(pcode, {}))
            row.update({k: v for k, v in page2.get(pcode, {}).items() if k not in row})
            merged[pcode] = row
            team_name = row.get("team", team_name)
        log(f"  [{kind}] {team_name or team_code}: {len(pcodes)}명 (누적 {len(merged)}명)")
    return merged


# ---------------------------------------------------------------------------
# bio (등번호 / 포지션 / 투타 / 생년월일 / 체격) via 구단별 등록현황
# ---------------------------------------------------------------------------

def _reformat_birthday(raw: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if not m:
        return raw.strip()
    y, mo, d = m.groups()
    return f"{y}년 {mo}월 {d}일"


def collect_bio(session: requests.Session, team_codes: List[str]) -> Dict[int, dict]:
    soup = get_soup(session, URL_REGISTER)
    bio: Dict[int, dict] = {}
    for team_code in team_codes:
        soup = postback(session, URL_REGISTER, soup, PREFIX + "btnCalendarSelect",
                         {PREFIX + "hfSearchTeam": team_code})
        team_name = TEAM_FULLNAME.get(team_code, team_code).split(" ")[0]
        count = 0
        for table in soup.find_all("table", class_="tNData"):
            thead = table.find("thead")
            if thead is None:
                continue
            headers = thead.find_all("th")
            if len(headers) < 2:
                continue
            position_label = headers[1].get_text(strip=True)
            body = table.find("tbody")
            if body is None:
                continue
            for tr in body.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 5:
                    continue
                link = tds[1].find("a")
                if link is None:
                    continue
                href = link.get("href") or ""
                if "/Record/Retire/" in href:
                    continue  # 감독/코치(은퇴선수 출신) - 현역 선수 아님
                m = re.search(r"playerId=(\d+)", href)
                if not m:
                    continue
                pcode = int(m.group(1))
                is_pitcher = "PitcherDetail" in href
                bio[pcode] = {
                    "pCode": pcode,
                    "team": team_short_name(team_code),
                    "name": link.get_text(strip=True),
                    "backNo": tds[0].get_text(strip=True),
                    "position": position_label,
                    "throwBat": tds[2].get_text(strip=True),
                    "birthday": _reformat_birthday(tds[3].get_text(strip=True)),
                    "heightWeight": tds[4].get_text(strip=True).replace(", ", "/").replace(" ", ""),
                    "is_pitcher": is_pitcher,
                }
                count += 1
        log(f"  [등록현황] {team_name}: {count}명")
    return bio


# team dropdown 표기(예: "두산", "KIA")와 register.aspx 표기가 다른 팀들이 있어(예: HT->"기아"가
# 아니라 KIA로 이미 일치, 실제로 다른 건 없었지만 안전하게 team_name_by_code로 통일한다)
def team_short_name(team_code: str) -> str:
    return TEAM_FULLNAME.get(team_code, team_code).split(" ")[0]


# ---------------------------------------------------------------------------
# merge bio + stats -> batters.csv / pitchers.csv rows
# ---------------------------------------------------------------------------

def build_player_csv_rows(session: requests.Session, stats: Dict[int, dict], bio: Dict[int, dict],
                           columns: List[str], want_pitcher: bool) -> List[dict]:
    out = []
    fallback_used = 0
    fallback_failed = 0
    wrong_category = 0
    for pcode, srow in stats.items():
        b = bio.get(pcode)
        if b is None or b["is_pitcher"] != want_pitcher:
            b = fetch_bio_fallback(session, pcode, want_pitcher)
            if b is None:
                fallback_failed += 1
                continue
            if b["is_pitcher"] != want_pitcher:
                # 투수가 대타/대주자 등으로 나와 타자기록(혹은 그 반대)에 0타석짜리 잡음
                # 행을 남긴 경우 - 이 선수의 진짜 포지션 파일에서만 다루므로 여기선 제외.
                wrong_category += 1
                continue
            fallback_used += 1
        team = srow.get("team") or team_short_name_from_bio(b)
        row = {c: srow.get(c, "") for c in columns}
        row["pCode"] = pcode
        row["team"] = team
        row["name"] = b["name"]
        row["backNo"] = b["backNo"]
        row["position"] = b["position"]
        row["throwBat"] = b["throwBat"]
        row["birthday"] = b["birthday"]
        row["heightWeight"] = b["heightWeight"]
        row["displayId"] = f"{team}{b['backNo']}{b['name']}"
        out.append(row)
    if fallback_used:
        log(f"  등록현황에 없어 개인기록 페이지로 폴백한 선수 {fallback_used}명 "
            f"(시즌 중 방출/2군행/부상자명단 등으로 오늘자 1군 등록현황에는 안 나오는 경우)")
    if fallback_failed:
        log(f"  경고: 폴백으로도 bio를 못 찾아 제외된 선수 {fallback_failed}명")
    if wrong_category:
        log(f"  {wrong_category}명은 실제 주포지션이 반대쪽(투수<->타자)이라 제외 "
            f"(대타/대주자 등 0타석 잡음 기록)")
    return out


def team_short_name_from_bio(b: dict) -> str:
    return b["team"]


DETAIL_URL = {False: f"{BASE}/Record/Player/HitterDetail/Basic.aspx", True: f"{BASE}/Record/Player/PitcherDetail/Basic.aspx"}


def fetch_bio_fallback(session: requests.Session, pcode: int, is_pitcher: bool) -> Optional[dict]:
    """register.aspx(오늘 기준 1군 등록현황)에는 시즌 중 방출/2군행/부상자명단 등으로 빠진
    선수가 안 나온다. 그런 선수는 이 개인 상세페이지에서 직접 bio를 채운다(현역이든 아니든
    한 번이라도 등록된 선수면 항상 존재)."""
    try:
        soup = get_soup(session, f"{DETAIL_URL[is_pitcher]}?playerId={pcode}")
    except RuntimeError:
        return None
    name_el = soup.find(id=re.compile(r"lblName$"))
    backno_el = soup.find(id=re.compile(r"lblBackNo$"))
    birthday_el = soup.find(id=re.compile(r"lblBirthday$"))
    position_el = soup.find(id=re.compile(r"lblPosition$"))
    hw_el = soup.find(id=re.compile(r"lblHeightWeight$"))
    if name_el is None or position_el is None:
        return None
    pos_raw = position_el.get_text(strip=True)  # 예: "외야수(우투좌타)"
    m = re.match(r"([^(]+)\(([^)]*)\)", pos_raw)
    position, throw_bat = (m.group(1), m.group(2)) if m else (pos_raw, "")
    # 이 선수의 "진짜" 주포지션은 항상 이 값이 정답이다 (예: 투수가 극단적 상황에서 대타/대주자로
    # 나와 타자 기록 페이지에 0타석짜리 잡음 행을 남기는 경우가 있는데, 그런 선수는 position이
    # "투수"로 나오므로 want_pitcher와 어긋나면 호출부에서 걸러낸다).
    return {
        "name": name_el.get_text(strip=True),
        "backNo": backno_el.get_text(strip=True) if backno_el else "",
        "position": position,
        "throwBat": throw_bat,
        "birthday": _reformat_birthday(birthday_el.get_text(strip=True)) if birthday_el else "",
        "heightWeight": hw_el.get_text(strip=True) if hw_el else "",
        "is_pitcher": position == "투수",
    }


# ---------------------------------------------------------------------------
# matchup (투수 vs 타자 상대전적) - 브루트포스, 팀이 다른 조합만
# ---------------------------------------------------------------------------

@dataclass
class MatchupJob:
    pitcher_team: str
    hitter_team: str
    pitcher_ids: List[int]
    hitter_ids: List[int]


def parse_matchup_result(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="tData")
    if table is None or table.tbody is None:
        return None
    tr = table.tbody.find("tr")
    if tr is None:
        return None
    tds = [td.get_text(strip=True) for td in tr.find_all("td")]
    if len(tds) != len(MATCHUP_COLUMNS) - 2:
        return None
    values = dict(zip(MATCHUP_COLUMNS[2:], tds))
    try:
        if int(values.get("PA", "0")) <= 0:
            return None
    except ValueError:
        return None
    return values


def collect_matchup(session_factory, pitcher_teams: Dict[str, List[int]], hitter_teams: Dict[str, List[int]],
                     delay: float, workers: int, checkpoint_path: str, resume: bool) -> None:
    done_pairs = set()
    out_fh_mode = "a"
    if resume and os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done_pairs.add((rec["pitcherPCode"], rec["hitterPCode"], rec.get("_probed", True)))
                except (json.JSONDecodeError, KeyError):
                    continue
        log(f"  체크포인트에서 {len(done_pairs)}건 이어받음: {checkpoint_path}")
    else:
        out_fh_mode = "w"

    probed_pairs = {(p, h) for (p, h, _probed) in done_pairs}

    jobs: List[MatchupJob] = []
    for p_team, p_ids in pitcher_teams.items():
        for h_team, h_ids in hitter_teams.items():
            if p_team == h_team or not p_ids or not h_ids:
                continue
            jobs.append(MatchupJob(p_team, h_team, p_ids, h_ids))

    total_pairs = sum(len(j.pitcher_ids) * len(j.hitter_ids) for j in jobs)
    remaining = total_pairs - len(probed_pairs)
    est_sec = remaining * delay / max(workers, 1)
    log(f"  전체 팀간 조합: {total_pairs}쌍, 이미 처리됨: {len(probed_pairs)}쌍, "
        f"남은 조합: {remaining}쌍 (예상 소요 {est_sec/60:.0f}분 이상, workers={workers})")

    out_fh = open(checkpoint_path, out_fh_mode, encoding="utf-8")

    def worker_run(job: MatchupJob):
        session = session_factory()
        soup = get_soup(session, URL_HITVSPIT)
        soup = postback(session, URL_HITVSPIT, soup, PREFIX + "ddlPitcherTeam",
                         {PREFIX + "ddlPitcherTeam": job.pitcher_team})
        soup = postback(session, URL_HITVSPIT, soup, PREFIX + "ddlHitterTeam",
                         {PREFIX + "ddlHitterTeam": job.hitter_team})
        base_data = build_form(soup)
        results = []
        for pid in job.pitcher_ids:
            for hid in job.hitter_ids:
                if (pid, hid) in probed_pairs:
                    continue
                d = dict(base_data)
                d[PREFIX + "ddlPitcherPlayer"] = str(pid)
                d[PREFIX + "ddlHitterPlayer"] = str(hid)
                d[PREFIX + "btnSearch"] = "검색"
                time.sleep(delay + random.uniform(0, delay))
                try:
                    resp = request_with_retry(session, "POST", URL_HITVSPIT, data=d, max_retries=4)
                except RuntimeError as e:
                    log(f"  실패(건너뜀) pitcher={pid} hitter={hid}: {e}")
                    continue
                parsed = parse_matchup_result(resp.text)
                rec = {"pitcherPCode": pid, "hitterPCode": hid, "_probed": True}
                if parsed:
                    rec.update(parsed)
                    rec["_has_data"] = True
                else:
                    rec["_has_data"] = False
                results.append(rec)
        return results

    completed_jobs = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(worker_run, job): job for job in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                results = fut.result()
            except Exception as e:
                log(f"  작업 실패 {job.pitcher_team}->{job.hitter_team}: {e}")
                continue
            for rec in results:
                out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_fh.flush()
            completed_jobs += 1
            log(f"  [상대전적] {job.pitcher_team}(투) vs {job.hitter_team}(타) 완료 "
                f"({completed_jobs}/{len(jobs)} 팀조합, 이번 조합 {len(results)}건 조회)")
    out_fh.close()


def checkpoint_to_rows(checkpoint_path: str) -> List[dict]:
    rows = []
    if not os.path.exists(checkpoint_path):
        return rows
    with open(checkpoint_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not rec.get("_has_data"):
                continue
            row = {c: rec.get(c, "") for c in MATCHUP_COLUMNS}
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# CSV 저장
# ---------------------------------------------------------------------------

def write_csv(path: str, columns: List[str], rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})
    log(f"  저장: {path} ({len(rows)}행)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--date", default=None, help="파일명에 쓸 날짜(YYYYMMDD). 기본값: 오늘")
    ap.add_argument("--matchup", dest="matchup", action="store_true", default=True,
                     help="투수-타자 상대전적까지 전수 수집 (기본값: 켜짐, 매우 오래 걸림)")
    ap.add_argument("--no-matchup", dest="matchup", action="store_false",
                     help="상대전적 수집을 건너뛰고 teams/batters/pitchers만 수집")
    ap.add_argument("--matchup-only", action="store_true",
                     help="teams/batters/pitchers는 새로 받지 않고, data_dir의 최신 파일을 읽어 "
                          "상대전적만 수집")
    ap.add_argument("--resume", action="store_true", help="중단된 상대전적 수집을 체크포인트에서 이어감")
    ap.add_argument("--delay", type=float, default=0.25, help="상대전적 조회 1건당 최소 대기(초). 기본 0.25")
    ap.add_argument("--workers", type=int, default=4, help="상대전적 조회 동시 세션 수. 기본 4")
    ap.add_argument("--limit-teams", default=None,
                     help="쉼표로 구단코드 나열시 해당 팀만 수집 (예: SS,KT). 동작 검증용")
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = args.date or date.today().strftime("%Y%m%d")

    session = new_session()
    log(f"KBO 공식 기록실에서 데이터 수집 시작 (날짜 태그: {stamp})")

    teams = collect_teams(session)
    team_codes = [t["teamCode"] for t in teams]
    if args.limit_teams:
        wanted = set(args.limit_teams.split(","))
        team_codes = [c for c in team_codes if c in wanted]
        teams = [t for t in teams if t["teamCode"] in wanted]
        log(f"  --limit-teams 지정됨: {team_codes}만 수집")

    if not args.matchup_only:
        log("teams.csv 수집")
        write_csv(os.path.join(args.out_dir, f"teams_{stamp}.csv"),
                  ["teamCode", "teamName", "teamFullName"], teams)

        log("타자 기록 수집 (팀별 Basic1+Basic2)")
        batter_stats = collect_players(session, team_codes, "batter")
        log("투수 기록 수집 (팀별 Basic1+Basic2)")
        pitcher_stats = collect_players(session, team_codes, "pitcher")

        log("선수 등록현황(등번호/포지션/투타/생년월일/체격) 수집")
        bio = collect_bio(session, team_codes)

        batter_rows = build_player_csv_rows(session, batter_stats, bio, BATTER_COLUMNS, want_pitcher=False)
        pitcher_rows = build_player_csv_rows(session, pitcher_stats, bio, PITCHER_COLUMNS, want_pitcher=True)

        write_csv(os.path.join(args.out_dir, f"batters_{stamp}.csv"), BATTER_COLUMNS, batter_rows)
        write_csv(os.path.join(args.out_dir, f"pitchers_{stamp}.csv"), PITCHER_COLUMNS, pitcher_rows)

        pitcher_ids_by_team: Dict[str, List[int]] = {}
        hitter_ids_by_team: Dict[str, List[int]] = {}
        for row in pitcher_rows:
            pitcher_ids_by_team.setdefault(row["team"], []).append(row["pCode"])
        for row in batter_rows:
            hitter_ids_by_team.setdefault(row["team"], []).append(row["pCode"])
        # team 컬럼은 약칭(예: "삼성")인데 HitVsPit.aspx의 ddlPitcherTeam/ddlHitterTeam은
        # teamCode(예: "SS")를 쓰므로 다시 코드로 매핑해 둔다.
        name_to_code = {t["teamName"]: t["teamCode"] for t in teams}
        pitcher_ids_by_code = {name_to_code[n]: ids for n, ids in pitcher_ids_by_team.items() if n in name_to_code}
        hitter_ids_by_code = {name_to_code[n]: ids for n, ids in hitter_ids_by_team.items() if n in name_to_code}
    else:
        log("--matchup-only: teams/batters/pitchers는 건너뛰고 기존 최신 파일에서 선수 목록을 읽습니다")
        pitcher_ids_by_code, hitter_ids_by_code = _load_ids_from_latest(args.out_dir, team_codes)

    if args.matchup:
        checkpoint_path = os.path.join(args.out_dir, f".matchup_checkpoint_{stamp}.jsonl")
        collect_matchup(new_session, pitcher_ids_by_code, hitter_ids_by_code,
                         args.delay, args.workers, checkpoint_path, args.resume)
        matchup_rows = checkpoint_to_rows(checkpoint_path)
        write_csv(os.path.join(args.out_dir, f"matchup_{stamp}.csv"), MATCHUP_COLUMNS, matchup_rows)
    else:
        log("--no-matchup: 상대전적 수집을 건너뜁니다 (matchup.csv는 새로 생성되지 않음)")

    log("수집 완료")
    return 0


def _load_ids_from_latest(out_dir: str, team_codes: List[str]):
    import glob
    import pandas as pd

    def latest(prefix):
        files = sorted(glob.glob(os.path.join(out_dir, f"{prefix}_*.csv")))
        return files[-1] if files else os.path.join(out_dir, f"{prefix}.csv")

    teams_df = pd.read_csv(latest("teams"))
    name_to_code = dict(zip(teams_df["teamName"], teams_df["teamCode"]))
    batters_df = pd.read_csv(latest("batters"))
    pitchers_df = pd.read_csv(latest("pitchers"))
    pitcher_ids_by_code: Dict[str, List[int]] = {}
    hitter_ids_by_code: Dict[str, List[int]] = {}
    for _, row in pitchers_df.iterrows():
        code = name_to_code.get(row["team"])
        if code:
            pitcher_ids_by_code.setdefault(code, []).append(int(row["pCode"]))
    for _, row in batters_df.iterrows():
        code = name_to_code.get(row["team"])
        if code:
            hitter_ids_by_code.setdefault(code, []).append(int(row["pCode"]))
    return pitcher_ids_by_code, hitter_ids_by_code


if __name__ == "__main__":
    raise SystemExit(main())
