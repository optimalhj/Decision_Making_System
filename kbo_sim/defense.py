"""
defense.py
----------
'OUT' 판정이 난 타구를 실제로 처리한다: 타구 유형(땅볼/뜬공/라인드라이브) 결정,
담당 수비수 배정, 포지션 불일치 시 실책확률 +50%, 병살/희생플라이 판정.

체력은 두 군데에 반영된다: 지친 **타자**는 뜬공 비율이 올라가고(FATIGUE_FLYBALL_TILT),
지친 **야수**는 실책 확률이 올라간다(traits.fielder_error_probability).

수비 라인업(defense_lineup)은 student_api를 통해 수비팀 학생 알고리즘이 이번 이닝에
결정한 10명짜리 리스트다. 순서 고정: [내야수x4, 외야수x3, 포수, DH, 투수]
(요구사항 #5: "앞 4개는 내야수, 마지막이 투수"). DH는 수비를 보지 않는다(공격 전용 슬롯이지만
로스터 연속성을 위해 리스트에 포함됨).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

from .data_pipeline import LeagueData
from .models import GameRosterState
from .traits import fielder_error_probability

DEFENSE_SLOT_GROUPS = ["내야수", "내야수", "내야수", "내야수", "외야수", "외야수", "외야수", "포수", "DH", "투수"]
IF_SLOTS = [0, 1, 2, 3]
OF_SLOTS = [4, 5, 6]
C_SLOT = 7
P_SLOT = 9

# 타자가 완전히 지쳤을 때(체력배수 1-MAX_DROP=0.37) 뜬공 가중치에 더해지는 값.
# 0.63(최대 체력저하) * 0.11 ≈ +0.07 → 뜬공 비율 대략 33% -> 40%.
FATIGUE_FLYBALL_TILT = 0.11


@dataclass
class BallInPlayResult:
    subtype: str            # 'GB','FB','LD'
    fielder_pcode: int
    fielder_group: str
    fielder_real_position: str
    mismatch: bool
    error: bool
    double_play: bool
    sac_fly: bool
    outs_recorded: int
    description: str


def _choose_batted_ball_type(batter_rec: dict, league: LeagueData, engine_rng: random.Random,
                              batter_fatigue_mult: float = 1.0) -> str:
    gb_w, fb_w, ld_w = 0.45, 0.34, 0.21
    ab = batter_rec.get("AB", 0) or 1
    hr_rate = (batter_rec.get("HR", 0) or 0) / ab
    league_hr_rate = 0.020
    tilt = max(min((hr_rate - league_hr_rate) * 3.0, 0.15), -0.15)
    # 지친 타자는 배트가 늦어 뜬공이 늘어난다는 가정 — 땅볼 가중치에서 같은 양을 뺀다.
    fatigue_penalty = max(0.0, 1.0 - batter_fatigue_mult)  # 0(쌩쌩) ~ MAX_DROP(탈진)
    tilt += fatigue_penalty * FATIGUE_FLYBALL_TILT
    fb_w += tilt
    gb_w -= tilt
    gb_w = max(gb_w, 0.15)
    total = gb_w + fb_w + ld_w
    r = engine_rng.random() * total
    if r < gb_w:
        return "GB"
    if r < gb_w + fb_w:
        return "FB"
    return "LD"


def _choose_fielder(subtype: str, defense_lineup: List[int], engine_rng: random.Random):
    if subtype == "GB":
        slots = IF_SLOTS
        weights = [0.9 / len(IF_SLOTS)] * len(IF_SLOTS) + [0.10]
        idx = engine_rng.choices(slots + [P_SLOT], weights=weights)[0]
    else:  # FB, LD
        if subtype == "FB" and engine_rng.random() < 0.12:
            idx = engine_rng.choice(IF_SLOTS)  # 얕은 뜬공을 내야수가 처리
        else:
            idx = engine_rng.choice(OF_SLOTS)
    return idx, defense_lineup[idx], DEFENSE_SLOT_GROUPS[idx]


def resolve_ball_in_play(league: LeagueData, batter_pcode: int, defense_lineup: List[int],
                          roster_state: GameRosterState, engine_rng: random.Random,
                          outs: int, runner_on_1: bool, runner_on_3: bool) -> BallInPlayResult:
    batter_rec = league.batter(batter_pcode)
    subtype = _choose_batted_ball_type(batter_rec, league, engine_rng,
                                        roster_state.get(batter_pcode).fatigue_mult())
    idx, fielder_pcode, fielder_group = _choose_fielder(subtype, defense_lineup, engine_rng)

    fielder_rt = roster_state.get(fielder_pcode)
    real_pos = fielder_rt.real_position
    mismatch = (fielder_group != real_pos) and fielder_group in ("내야수", "외야수", "포수")
    error_mult = fielder_rt.traits.error_mult if fielder_rt.traits else 1.0
    fatigue_mult = fielder_rt.fatigue_mult()
    error_p = fielder_error_probability(roster_state.game_seed, fielder_pcode, fielder_group, real_pos,
                                         error_mult, fatigue_mult)
    error = engine_rng.random() < error_p

    double_play = False
    sac_fly = False
    outs_recorded = 1
    desc = ""

    if error:
        outs_recorded = 0
        desc = f"{'포지션이 맞지 않는 ' if mismatch else ''}수비 실책으로 출루"
    else:
        if subtype == "GB" and runner_on_1 and outs < 2:
            if engine_rng.random() < 0.42:
                double_play = True
                outs_recorded = 2
                desc = "병살타"
        if not double_play and subtype in ("FB",) and runner_on_3 and outs < 2:
            if engine_rng.random() < 0.55:
                sac_fly = True
                desc = "희생플라이"
        if not desc:
            desc = {"GB": "땅볼 아웃", "FB": "뜬공 아웃", "LD": "라인드라이브 아웃"}[subtype]

    return BallInPlayResult(subtype=subtype, fielder_pcode=fielder_pcode, fielder_group=fielder_group,
                             fielder_real_position=real_pos, mismatch=mismatch, error=error,
                             double_play=double_play, sac_fly=sac_fly, outs_recorded=outs_recorded,
                             description=desc)
