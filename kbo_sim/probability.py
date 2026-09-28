"""
probability.py
---------------
한 타석(PA)의 결과 확률분포를 계산한다. 파이프라인:

1. 타자 개인 event_rate(리그평균으로 축소된 실제 기록) × 투수 개인 event_rate ÷ 리그평균
   → "매치업 예측 확률" (표준적인 sabermetric 승산비(odds-ratio/log5류) 방법의 다범주 확장)
2. 실제 투수-타자 통산 맞대결 기록(matchup.csv)이 있으면, 그 표본크기(PA)에 비례한
   가중치로 1)의 결과와 블렌딩 ("매치 데이터가 있으면 그것을 더 신뢰, 없으면(가중치 0)
   순수 개인 스탯 기반으로 대체" — 요구사항 #1을 부드러운 가중평균으로 구현).
3. 체력(fatigue) 배수를 반영: 투수가 지치면 타자에게 유리한 사건(안타/홈런/볼넷) 확률이,
   타자가 지치면 타자에게 불리한 사건(삼진/아웃) 확률이 각각 올라간다.
4. 매 타석 약간의 무작위 컨디션 잡음(jitter)을 곱해 "잘하는 선수가 항상 잘하지는 않도록" 한다.
5. 마지막에 합이 1이 되도록 재정규화.
"""
from __future__ import annotations

import math
import random
from typing import Dict

from .data_pipeline import BATTER_EVENTS, LeagueData

BATTER_FAVORABLE = {"BB", "HBP", "1B", "2B", "3B", "HR"}
PITCHER_FAVORABLE = {"SO", "OUT"}

MATCHUP_SHRINK_PA = 15.0     # 실전 맞대결 표본이 작으므로(대부분 1~30타석) 축소 강도도 작게
JITTER_SIGMA = 0.12          # 매 타석 컨디션 잡음(로그정규) 표준편차
# 체력 배수 -> 사건확률 배수 변환 강도. 1.0이면 체력차가 확률에 그대로 곱해져 지친 투수가
# 비현실적으로 얻어맞는다. 값이 클수록 "지칠수록 더 얻어맞는다"가 강해진다.
# 체력저하가 실제 성적에 더 뚜렷이 드러나도록 계속 올려왔다(0.68 -> 0.78 -> 0.86).
FATIGUE_SKILL_ALPHA = 0.86
# 체력차 + 컨디션 잡음을 합친 최종 확률배수의 상한(하한은 그 역수). 이게 없으면 완전히 지친
# 투수(체력배수 0.27)가 쌩쌩한 타자를 만났을 때 배수가 3배를 넘어 출루확률이 0.8을 넘고, 이닝이
# 좀처럼 끝나지 않아 한 이닝 20점 같은 결과가 나온다. 실측(한 투수로 9이닝 완투시키는 최악의
# 운영 기준, 1080이닝):
#     상한없음 → 9이닝 31.9점, 10점 이상 이닝 11.9%
#     1.45     → 9이닝 11.0점, 10점 이상 이닝 0.2%
# 정상 운영(이닝/3이닝마다 교체)은 상한값과 무관하게 9이닝 5.2점 수준으로 영향을 받지 않는다 —
# 쌩쌩한 투수는 애초에 상한 근처까지 가지 않기 때문. 즉 이 상한은 혹사 페널티(정상의 2배 실점)는
# 남기고 "한 이닝에 10~20점" 극단값만 잘라낸다.
FATIGUE_FACTOR_CAP = 1.45


def _renorm(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    if s <= 0:
        return {k: 1.0 / len(d) for k in d}
    return {k: v / s for k, v in d.items()}


def log5_blend(batter_rate: Dict[str, float], pitcher_rate: Dict[str, float],
                league_rate: Dict[str, float]) -> Dict[str, float]:
    out = {}
    for ev in BATTER_EVENTS:
        lg = max(league_rate.get(ev, 1e-6), 1e-6)
        out[ev] = max(batter_rate.get(ev, 0.0) * pitcher_rate.get(ev, 0.0) / lg, 1e-9)
    return _renorm(out)


def _matchup_event_rate(row: dict) -> tuple[Dict[str, float], float]:
    pa = row.get("PA", 0) or 0
    if pa <= 0:
        return {}, 0.0
    h = row.get("H", 0) or 0
    doubles = row.get("2B", 0) or 0
    triples = row.get("3B", 0) or 0
    hr = row.get("HR", 0) or 0
    bb = row.get("BB", 0) or 0
    hbp = row.get("HBP", 0) or 0
    so = row.get("SO", 0) or 0
    singles = max(h - doubles - triples - hr, 0)
    out = max(pa - bb - hbp - h - so, 0)
    counts = {"BB": bb, "HBP": hbp, "SO": so, "1B": singles, "2B": doubles, "3B": triples, "HR": hr, "OUT": out}
    return counts, pa


def blend_with_matchup(model_rate: Dict[str, float], matchup_row: dict | None) -> Dict[str, float]:
    if not matchup_row:
        return model_rate
    counts, pa = _matchup_event_rate(matchup_row)
    if pa <= 0:
        return model_rate
    weight = pa / (pa + MATCHUP_SHRINK_PA)  # 맞대결 타석이 많을수록 실제기록 비중 증가
    empirical = {ev: counts.get(ev, 0.0) / pa for ev in BATTER_EVENTS}
    blended = {ev: weight * empirical[ev] + (1 - weight) * model_rate[ev] for ev in BATTER_EVENTS}
    return _renorm(blended)


def apply_fatigue_and_jitter(rate: Dict[str, float], batter_fatigue_mult: float,
                              pitcher_fatigue_mult: float, engine_rng: random.Random) -> Dict[str, float]:
    pitcher_fatigue_mult = max(pitcher_fatigue_mult, 1e-3)
    skill_factor = (batter_fatigue_mult / pitcher_fatigue_mult) ** FATIGUE_SKILL_ALPHA
    jitter = math.exp(engine_rng.gauss(0.0, JITTER_SIGMA))
    factor_favorable = skill_factor * jitter
    factor_favorable = min(max(factor_favorable, 1.0 / FATIGUE_FACTOR_CAP), FATIGUE_FACTOR_CAP)
    factor_unfavorable = 1.0 / factor_favorable

    out = {}
    for ev, p in rate.items():
        if ev in BATTER_FAVORABLE:
            out[ev] = p * factor_favorable
        elif ev in PITCHER_FAVORABLE:
            out[ev] = p * factor_unfavorable
        else:
            out[ev] = p
    return _renorm(out)


def resolve_pa_probabilities(league: LeagueData, batter_pcode: int, pitcher_pcode: int,
                              batter_fatigue_mult: float, pitcher_fatigue_mult: float,
                              engine_rng: random.Random) -> Dict[str, float]:
    batter = league.batter(batter_pcode)
    pitcher = league.pitcher(pitcher_pcode)
    model_rate = log5_blend(batter["event_rate"], pitcher["event_rate"], league.league_avg.batter)
    matchup_row = league.get_matchup(pitcher_pcode, batter_pcode)
    blended = blend_with_matchup(model_rate, matchup_row)
    final = apply_fatigue_and_jitter(blended, batter_fatigue_mult, pitcher_fatigue_mult, engine_rng)
    return final
