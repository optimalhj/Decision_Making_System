"""
data_pipeline.py
-----------------
4개 원천 CSV(teams, pitchers, batters, matchup)를 kbo_sim/data_snapshot/ 에서 읽어 정제하고,
시뮬레이션 엔진이 바로 쓸 수 있는 형태(사건 확률 딕셔너리, 리그 평균 등)로 가공한다.

data_snapshot/ 안의 파일은 두 가지 형태를 지원한다:
  1. 날짜가 붙은 파일 - 예: teams_20260907.csv, batters_20260907.csv (tools/collect_kbo_data.py가
     생성). 같은 접두어로 여러 날짜가 있으면 파일명 끝의 YYYYMMDD를 비교해 **가장 최신 날짜**의
     파일을 읽는다.
  2. 날짜 없는 고정 파일명 - 예: teams.csv (과거 방식, 수동으로 갈아끼우던 스냅샷). 날짜 붙은
     파일이 하나도 없을 때만 폴백으로 쓰인다.
파일명에 특정 연도를 박아두지 않은 이유(고정 파일명 방식)나 여러 날짜를 허용한 이유(수집
스크립트 방식) 모두 같다 - 시즌이 바뀌거나 데이터를 새로 받을 때마다 코드를 고칠 필요 없이
data_snapshot/ 안의 CSV 내용만 갈아끼우면 되도록 하기 위해서다.

이 모듈이 계산해서 "추가"하는 값들은 전부 원본 CSV의 실제 카운팅 스탯(안타, 볼넷, 삼진 등)을
표본크기(PA/TBF)로 나눈 **비율**이거나, 표본이 작을 때 리그 평균 쪽으로 당기는 **베이지안
축소(shrinkage)** 값이다. 즉 "새로운 사실"을 지어내는 게 아니라 있는 기록을 확률모델에 맞게
정규화하는 것뿐이다. 반면 KBO 기록에 아예 없는 항목(도루 성향, 실책 기초율 등)은
`traits.py`에서 명확히 "합성값"이라고 표시하여 별도로 다룬다 (이 모듈에는 없음).
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

DATA_DIR_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_snapshot")

SNAPSHOT_BASENAMES = ["teams", "batters", "pitchers", "matchup"]
_DATED_RE = re.compile(r"^(.+)_(\d{8})\.csv$")


def resolve_snapshot_files(data_dir: str = DATA_DIR_DEFAULT) -> Dict[str, str]:
    """data_dir 안에서 4개 원천 CSV 각각에 대해 실제로 읽을 파일 경로를 정한다.

    `{name}_YYYYMMDD.csv` 형태가 하나 이상 있으면 그 중 날짜가 가장 최신인 파일을,
    없으면 `{name}.csv`(고정 파일명) 폴백을 쓴다. 이름별로 독립적으로 판단하므로
    예를 들어 batters만 새로 받고 matchup은 예전 고정 파일을 그대로 쓰는 것도 가능하다.
    """
    resolved: Dict[str, str] = {}
    for base in SNAPSHOT_BASENAMES:
        dated = glob.glob(os.path.join(data_dir, f"{base}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].csv"))
        if dated:
            def _date_key(path):
                m = _DATED_RE.match(os.path.basename(path))
                return m.group(2) if m else ""
            resolved[base] = max(dated, key=_date_key)
        else:
            resolved[base] = os.path.join(data_dir, f"{base}.csv")
    return resolved

# PA 사건 카테고리 (합이 1이 되도록 정규화됨). OUT은 "인플레이 아웃"이며 이후 defense.py에서
# 땅볼/뜬공/라인드라이브로 다시 세분화된다.
BATTER_EVENTS = ["BB", "HBP", "SO", "1B", "2B", "3B", "HR", "OUT"]

# 축소(shrinkage) 강도: 이 값만큼의 "가상 타석/상대타자"를 리그평균으로 미리 채워둔 뒤
# 실제 표본과 합산 평균한다. 표본(PA/TBF)이 이 값보다 훨씬 크면 실제 기록이 지배적이고,
# 작으면 리그 평균 쪽으로 많이 당겨진다. 표준적인 경험적 베이즈(empirical Bayes) 방식.
BATTER_SHRINK_PA = 60
PITCHER_SHRINK_TBF = 80


def _parse_ip(raw) -> float:
    """'7 1/3' 같은 KBO식 이닝 표기를 실수(7.333...)로 변환."""
    s = str(raw).strip()
    if s in ("", "-", "nan"):
        return 0.0
    if " " in s:
        whole, frac = s.split(" ", 1)
        num, den = frac.split("/")
        return int(whole) + int(num) / int(den)
    if "/" in s:
        num, den = s.split("/")
        return int(num) / int(den)
    return float(s)


def _safe_div(a, b, default=0.0):
    return a / b if b else default


@dataclass
class LeagueAverages:
    batter: Dict[str, float]
    pitcher: Dict[str, float]


@dataclass
class LeagueData:
    teams: pd.DataFrame
    batters: pd.DataFrame
    pitchers: pd.DataFrame
    matchup: pd.DataFrame

    batter_by_pcode: Dict[int, dict] = field(default_factory=dict)
    pitcher_by_pcode: Dict[int, dict] = field(default_factory=dict)
    matchup_index: Dict[tuple, dict] = field(default_factory=dict)
    league_avg: Optional[LeagueAverages] = None

    team_code_by_name: Dict[str, str] = field(default_factory=dict)
    roster_by_team: Dict[str, dict] = field(default_factory=dict)  # team -> {"투수":[pCode..], "포수":[...], ...}
    source_files: Dict[str, str] = field(default_factory=dict)  # teams/batters/pitchers/matchup -> 실제로 읽은 경로

    def batter(self, pcode) -> dict:
        return self.batter_by_pcode[int(pcode)]

    def pitcher(self, pcode) -> dict:
        return self.pitcher_by_pcode[int(pcode)]

    def player(self, pcode) -> dict:
        pcode = int(pcode)
        if pcode in self.batter_by_pcode:
            return self.batter_by_pcode[pcode]
        return self.pitcher_by_pcode[pcode]

    def get_matchup(self, pitcher_pcode, hitter_pcode) -> Optional[dict]:
        return self.matchup_index.get((int(pitcher_pcode), int(hitter_pcode)))

    def teams_list(self):
        return list(self.teams["teamName"])


def _compute_batter_events(row) -> Dict[str, float]:
    ab = row["AB"]
    h = row["H"]
    doubles = row["2B"]
    triples = row["3B"]
    hr = row["HR"]
    singles = max(h - doubles - triples - hr, 0)
    bb = row["BB"]  # KBO 표기상 IBB는 BB에 포함되는 세부항목 (합계 아님, 데이터 검증 완료)
    hbp = row["HBP"]
    so = row["SO"]
    pa = row["PA"] if row["PA"] > 0 else (ab + bb + hbp + row["SF"] + row["SAC"])
    out = max(pa - bb - hbp - h - so, 0)  # 인플레이 아웃 = 타석 - 볼넷 - 사구 - 안타 - 삼진
    counts = {"BB": bb, "HBP": hbp, "SO": so, "1B": singles, "2B": doubles, "3B": triples, "HR": hr, "OUT": out}
    return counts, pa


def _compute_pitcher_events(row) -> Dict[str, float]:
    tbf = row["TBF"]
    h = row["H"]
    hr = row["HR"]
    bb = row["BB"]
    hbp = row["HBP"]
    so = row["SO"]
    # 투수 CSV에는 피안타의 2루타/3루타 세부 항목이 있어(2B,3B) 단타를 역산할 수 있음
    doubles = row.get("2B", 0)
    triples = row.get("3B", 0)
    singles = max(h - doubles - triples - hr, 0)
    out = max(tbf - bb - hbp - h - so, 0)
    counts = {"BB": bb, "HBP": hbp, "SO": so, "1B": singles, "2B": doubles, "3B": triples, "HR": hr, "OUT": out}
    return counts, tbf


def _rate_dict(counts: dict, denom: float, shrink_n: float, league_rate: dict) -> Dict[str, float]:
    """counts/denom 비율을 리그평균 쪽으로 shrink_n 만큼 축소(smoothing)."""
    out = {}
    eff_denom = denom + shrink_n
    for ev in BATTER_EVENTS:
        prior = league_rate.get(ev, 0.0) * shrink_n
        out[ev] = (counts.get(ev, 0.0) + prior) / eff_denom if eff_denom > 0 else league_rate.get(ev, 0.0)
    # 수치 오차로 합이 1에서 살짝 벗어날 수 있으니 재정규화
    s = sum(out.values())
    if s > 0:
        for ev in out:
            out[ev] /= s
    return out


def load_league_data(data_dir: str = DATA_DIR_DEFAULT) -> LeagueData:
    files = resolve_snapshot_files(data_dir)
    teams = pd.read_csv(files["teams"])
    batters = pd.read_csv(files["batters"])
    pitchers = pd.read_csv(files["pitchers"])
    matchup = pd.read_csv(files["matchup"])

    # 숫자 컬럼 강제 변환 ("-" 등 문자값은 NaN -> 0)
    for col in ["AVG", "G", "PA", "AB", "R", "H", "2B", "3B", "HR", "TB", "RBI", "SAC", "SF",
                "BB", "IBB", "HBP", "SO", "GDP", "SLG", "OBP", "OPS", "MultiHit", "RISP_AVG", "PH_AVG"]:
        if col in batters.columns:
            batters[col] = pd.to_numeric(batters[col], errors="coerce").fillna(0.0)

    pitchers["IP_f"] = pitchers["IP"].apply(_parse_ip)
    for col in ["ERA", "G", "W", "L", "SV", "HLD", "WPCT", "H", "HR", "BB", "HBP", "SO", "R", "ER",
                "WHIP", "CG", "SHO", "QS", "BSV", "TBF", "NP", "OAVG", "2B", "3B", "SAC", "SF", "IBB",
                "WP", "BK"]:
        if col in pitchers.columns:
            pitchers[col] = pd.to_numeric(pitchers[col], errors="coerce").fillna(0.0)

    for col in ["AVG", "PA", "AB", "H", "2B", "3B", "HR", "RBI", "BB", "HBP", "SO", "SLG", "OBP", "OPS"]:
        if col in matchup.columns:
            matchup[col] = pd.to_numeric(matchup[col], errors="coerce").fillna(0.0)

    # 타석이 없던 선수(AB=0, "-" 표기)는 시뮬레이션 대상에서 제외 (실질 기록 없음, 문서화된 처리)
    batters = batters[batters["AB"] > 0].reset_index(drop=True)
    pitchers = pitchers[pitchers["TBF"] > 0].reset_index(drop=True)

    ld = LeagueData(teams=teams, batters=batters, pitchers=pitchers, matchup=matchup, source_files=files)
    ld.team_code_by_name = dict(zip(teams["teamName"], teams["teamCode"]))

    # ---- 리그 평균 계산 (표본가중 평균 = 전체 카운트 합 / 전체 PA(또는 TBF) 합) ----
    b_counts_total = {ev: 0.0 for ev in BATTER_EVENTS}
    b_pa_total = 0.0
    b_event_cache = {}
    for _, row in batters.iterrows():
        counts, pa = _compute_batter_events(row)
        b_event_cache[int(row["pCode"])] = (counts, pa)
        b_pa_total += pa
        for ev in BATTER_EVENTS:
            b_counts_total[ev] += counts[ev]
    league_batter_rate = {ev: _safe_div(b_counts_total[ev], b_pa_total) for ev in BATTER_EVENTS}

    p_counts_total = {ev: 0.0 for ev in BATTER_EVENTS}
    p_tbf_total = 0.0
    p_event_cache = {}
    for _, row in pitchers.iterrows():
        counts, tbf = _compute_pitcher_events(row)
        p_event_cache[int(row["pCode"])] = (counts, tbf)
        p_tbf_total += tbf
        for ev in BATTER_EVENTS:
            p_counts_total[ev] += counts[ev]
    league_pitcher_rate = {ev: _safe_div(p_counts_total[ev], p_tbf_total) for ev in BATTER_EVENTS}

    ld.league_avg = LeagueAverages(batter=league_batter_rate, pitcher=league_pitcher_rate)

    # ---- 선수별 레코드 구성 ----
    for _, row in batters.iterrows():
        pcode = int(row["pCode"])
        counts, pa = b_event_cache[pcode]
        rates = _rate_dict(counts, pa, BATTER_SHRINK_PA, league_batter_rate)
        rec = row.to_dict()
        rec["pCode"] = pcode
        rec["event_counts"] = counts
        rec["PA_eff"] = pa
        rec["event_rate"] = rates  # 표본축소 적용된 사건확률 (리그평균 vs 실제기록 블렌드)
        ld.batter_by_pcode[pcode] = rec
        ld.roster_by_team.setdefault(row["team"], {}).setdefault(row["position"], []).append(pcode)

    for _, row in pitchers.iterrows():
        pcode = int(row["pCode"])
        counts, tbf = p_event_cache[pcode]
        rates = _rate_dict(counts, tbf, PITCHER_SHRINK_TBF, league_pitcher_rate)
        rec = row.to_dict()
        rec["pCode"] = pcode
        rec["event_counts"] = counts
        rec["TBF_eff"] = tbf
        rec["event_rate"] = rates
        g = row["G"] if row["G"] > 0 else 1
        np_per_g = row["NP"] / g
        rec["NP_per_G"] = np_per_g
        # 등판당 평균 투구수 목표치(기본값, 게임 시작 시 ±20% 랜덤이 적용됨 - fatigue.py 참고)
        rec["target_pitch_base"] = min(max(np_per_g, 15.0), 140.0)
        ld.pitcher_by_pcode[pcode] = rec
        ld.roster_by_team.setdefault(row["team"], {}).setdefault("투수", []).append(pcode)

    # ---- 화면 표시용 짧은 이름(shortName) 계산 ----
    # 중계 화면에는 "한화30폰세"(displayId) 대신 그냥 "폰세"처럼 이름만 보여준다.
    # 다만 한 팀 안에 동명이인이 있으면(2025시즌 기준 삼성 김태훈/이승현, 키움 이주형)
    # 구분이 안 되므로 그 선수들만 등번호를 붙인다 -> "이승현(8)".
    name_count: Dict[tuple, int] = {}
    for rec in list(ld.batter_by_pcode.values()) + list(ld.pitcher_by_pcode.values()):
        key = (rec["team"], rec["name"])
        name_count[key] = name_count.get(key, 0) + 1
    for rec in list(ld.batter_by_pcode.values()) + list(ld.pitcher_by_pcode.values()):
        key = (rec["team"], rec["name"])
        if name_count[key] > 1:
            back = rec.get("backNo")
            try:
                back_txt = str(int(float(back)))
            except (TypeError, ValueError):
                back_txt = None
            rec["shortName"] = f"{rec['name']}({back_txt})" if back_txt else rec["displayId"]
        else:
            rec["shortName"] = rec["name"]

    # ---- 매치업 인덱스 ----
    for _, row in matchup.iterrows():
        key = (int(row["pitcherPCode"]), int(row["hitterPCode"]))
        ld.matchup_index[key] = row.to_dict()

    return ld


if __name__ == "__main__":
    ld = load_league_data()
    print("teams:", ld.teams_list())
    print("batters:", len(ld.batter_by_pcode), "pitchers:", len(ld.pitcher_by_pcode))
    print("league batter rate:", ld.league_avg.batter)
    print("league pitcher rate:", ld.league_avg.pitcher)
