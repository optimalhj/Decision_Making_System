"""
models.py
---------
경기 진행 중 사용하는 경량 데이터 구조.
(정적 선수 기록 자체는 data_pipeline.LeagueData에 이미 있음 — 여기서는 "이번 경기 동안의 상태"만 다룸)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .data_pipeline import LeagueData
from .traits import BatterTraits, derive_batter_traits, _player_rng as _deterministic_rng

# 선수ID 표기 규칙 (구현고려 #3, #4)
# - pCode: KBO 내부 선수 고유 ID. 게임 엔진의 "진짜 키" (딕셔너리 키, 상태 추적 등 전부 이걸로).
# - displayId ("팀명+등번호+이름", 예: "삼성123김동현"): 사람이 보는 화면/문자중계 표시용일 뿐.
#   데이터에 이미 컬럼으로 존재 (data_pipeline이 그대로 실어 나름).


@dataclass
class Team:
    name: str          # 팀명 (예: '삼성') - CSV의 'team' 컬럼과 동일 키
    code: str          # 팀코드 (예: 'SS')
    full_name: str
    batter_pcodes: List[int]
    pitcher_pcodes: List[int]
    roster_by_position: Dict[str, List[int]]  # '포수'/'내야수'/'외야수' -> [pCode,...] (투수 제외)

    def display_id(self, league: LeagueData, pcode: int) -> str:
        return league.player(pcode)["displayId"]


def build_team(league: LeagueData, team_name: str) -> Team:
    code = league.team_code_by_name.get(team_name, team_name)
    full_name = league.teams.loc[league.teams["teamName"] == team_name, "teamFullName"]
    full_name = full_name.iloc[0] if len(full_name) else team_name
    roster = league.roster_by_team.get(team_name, {})
    batter_codes = []
    roster_by_pos = {}
    for pos in ("포수", "내야수", "외야수"):
        codes = list(roster.get(pos, []))
        roster_by_pos[pos] = codes
        batter_codes.extend(codes)
    pitcher_codes = list(roster.get("투수", []))
    return Team(name=team_name, code=code, full_name=full_name, batter_pcodes=batter_codes,
                pitcher_pcodes=pitcher_codes, roster_by_position=roster_by_pos)


@dataclass
class PlayerRuntime:
    """한 경기 동안 누적되는 선수 상태. 회복 없음 - 경기 끝날 때까지 계속 누적."""
    pcode: int
    is_pitcher: bool
    real_position: str                 # 고정 포지션 ('투수'/'포수'/'내야수'/'외야수')
    pitch_count: float = 0.0
    pitch_target: Optional[float] = None
    swing_count: float = 0.0
    swing_target: Optional[float] = None
    traits: Optional[BatterTraits] = None
    innings_pitched_outs: int = 0
    appearances: int = 0                # 이닝 출전 횟수(디버그/통계용)

    def fatigue_mult(self):
        from .fatigue import (performance_multiplier, BATTER_STEEPNESS_NUMERATOR,
                              PITCHER_STEEPNESS_NUMERATOR, PITCHER_MAX_DROP)
        if self.is_pitcher:
            return performance_multiplier(self.pitch_count, self.pitch_target or 100.0,
                                          PITCHER_STEEPNESS_NUMERATOR, PITCHER_MAX_DROP)
        return performance_multiplier(self.swing_count, self.swing_target or 14.0, BATTER_STEEPNESS_NUMERATOR)

    def health_pct(self):
        from .fatigue import (health_pct, BATTER_STEEPNESS_NUMERATOR,
                              PITCHER_STEEPNESS_NUMERATOR, PITCHER_MAX_DROP)
        if self.is_pitcher:
            return health_pct(self.pitch_count, self.pitch_target or 100.0,
                              PITCHER_STEEPNESS_NUMERATOR, PITCHER_MAX_DROP)
        return health_pct(self.swing_count, self.swing_target or 14.0, BATTER_STEEPNESS_NUMERATOR)


class GameRosterState:
    """경기 전체(9이닝)에 걸쳐 유지되는 두 팀 전체 선수의 PlayerRuntime 저장소.
    이닝이 바뀌어도, 같은 선수가 다시 나와도 체력은 계속 이어진다 ("체력저하는 유지")."""

    def __init__(self, league: LeagueData, game_seed: int):
        self.league = league
        self.game_seed = game_seed
        self._states: Dict[int, PlayerRuntime] = {}

    def get(self, pcode: int) -> PlayerRuntime:
        pcode = int(pcode)
        if pcode not in self._states:
            is_pitcher = pcode in self.league.pitcher_by_pcode
            rec = self.league.player(pcode)
            rt = PlayerRuntime(pcode=pcode, is_pitcher=is_pitcher, real_position=rec["position"])
            if is_pitcher:
                from .fatigue import roll_pitcher_target
                r = _deterministic_rng(self.game_seed, pcode, "ptarget")
                rt.pitch_target = roll_pitcher_target(r, rec["target_pitch_base"])
            else:
                from .fatigue import roll_batter_target
                r = _deterministic_rng(self.game_seed, pcode, "starget")
                rt.swing_target = roll_batter_target(r)
                sac_pa = rec["SAC"] / rec["PA"] if rec.get("PA", 0) else 0.0
                rt.traits = derive_batter_traits(self.game_seed, pcode, sac_pa)
            self._states[pcode] = rt
        return self._states[pcode]

    def health_snapshot(self, pcodes: List[int]) -> Dict[int, float]:
        return {p: self.get(p).health_pct() for p in pcodes}
