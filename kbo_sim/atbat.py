"""
atbat.py
--------
한 타석(plate appearance)의 전체 처리 흐름:
  1) (주자 있으면) 도루 시도 판정 - 투구 전에 벌어지는 별도 이벤트
  2) (상황에 맞으면) 희생번트 시도 판정 - 타자 성향(bunt_pref, 실제 SAC 기록 기반) 반영
  3) 위 두 가지가 아니면 일반 타석: probability.py로 사건확률 계산 -> 사건 샘플링
  4) 인플레이 아웃이면 defense.py로 세부 처리(땅볼/뜬공, 수비수, 실책, 병살, 희비플라이)
  5) 주자 진루 처리 (단순화된 야구 베이스러닝 규칙 - 문서화된 근사)
  6) pitch_sequence.py로 그럴듯한 투구 시퀀스 역생성 -> 스윙수/투구수 반영

이닝 중 전술적 의사결정(번트/도루 지시 등)은 학생이 하지 않는다(스펙 명시). 전부 선수
성향 + 엔진 RNG로 자동 결정된다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .data_pipeline import LeagueData
from .defense import resolve_ball_in_play
from .models import GameRosterState
from .pitch_sequence import count_swings, generate_pitch_sequence
from .probability import resolve_pa_probabilities

Bases = Dict[int, Optional[int]]  # {1: pcode|None, 2: ..., 3: ...}


def empty_bases() -> Bases:
    return {1: None, 2: None, 3: None}


@dataclass
class PAResult:
    event: str
    description: str
    runs: int
    rbi: int
    outs_added: int
    pitches: List[dict]
    steal_events: List[dict] = field(default_factory=list)
    bunt: bool = False
    error: bool = False
    bip_detail: Optional[str] = None
    # --- 그라운드 시각화(문자중계 화면)용 정보 ---
    # 경기 결과에는 영향을 주지 않지만, 타구가 어디로 갔는지 그려주기 위해 엔진 RNG로 함께 뽑는다.
    # (로그에 남으므로 리플레이해도 항상 같은 그림이 나온다)
    fielder_pcode: Optional[int] = None   # 타구를 처리한 수비수 (인플레이 아웃/실책일 때)
    bip_type: Optional[str] = None        # 'GB'(땅볼) / 'FB'(뜬공) / 'LD'(라인드라이브) / 'BUNT'
    hit_dir: Optional[float] = None       # 0.0=좌측 파울라인 ~ 1.0=우측 파울라인 방향
    hit_depth: Optional[float] = None     # 0.0=내야 ~ 1.0=펜스


STEAL_ATTEMPT_BASE_RATE = 0.16   # steal_pref=1.0인 주자가 1루에 있을 때 시도할 기본 확률 상한
STEAL_SUCCESS_BASE = 0.70
BUNT_ATTEMPT_SCALE = 0.5         # bunt_pref -> 실제 시도확률로 낮춰 매핑


def maybe_attempt_steal(bases: Bases, roster_state: GameRosterState, engine_rng: random.Random,
                         outs: int) -> Optional[dict]:
    runner1 = bases.get(1)
    if not runner1 or bases.get(2):
        return None
    traits = roster_state.get(runner1).traits
    if not traits:
        return None
    attempt_p = traits.steal_pref * STEAL_ATTEMPT_BASE_RATE
    if engine_rng.random() >= attempt_p:
        return None
    fatigue_mult = roster_state.get(runner1).fatigue_mult()
    success_p = STEAL_SUCCESS_BASE * (0.7 + 0.3 * fatigue_mult)
    success = engine_rng.random() < success_p
    if success:
        bases[2] = runner1
        bases[1] = None
        return {"kind": "SB", "runner": runner1, "success": True, "text": "2루 도루 성공!"}
    else:
        bases[1] = None
        return {"kind": "CS", "runner": runner1, "success": False, "text": "2루 도루 실패, 아웃",
                "outs_added": 1}


def maybe_attempt_bunt(bases: Bases, batter_pcode: int, roster_state: GameRosterState,
                        engine_rng: random.Random, outs: int) -> bool:
    if outs >= 2:
        return False
    if not (bases.get(1) or bases.get(2)):
        return False
    traits = roster_state.get(batter_pcode).traits
    if not traits:
        return False
    attempt_p = traits.bunt_pref * BUNT_ATTEMPT_SCALE
    return engine_rng.random() < attempt_p


def _resolve_bunt(bases: Bases, batter_pcode: int, engine_rng: random.Random) -> PAResult:
    success = engine_rng.random() < 0.85
    pitches = generate_pitch_sequence("OUT", engine_rng)[:2] or [
        {"seq": 1, "balls": 0, "strikes": 0, "kind": "inplay", "swung": True, "text": "번트"}]
    pitches[-1] = {**pitches[-1], "kind": "inplay", "swung": True, "text": "번트 타구"}
    runs = 0
    rbi = 0
    outs_added = 1
    if success:
        # 앞 주자부터 순서대로 처리하고, **앞 베이스가 비어 있을 때만** 진루시킨다.
        # (예전에는 bases[3] = bases[2]로 덮어써서 3루 주자가 사라지는 버그가 있었음)
        r1, r2, r3 = bases.get(1), bases.get(2), bases.get(3)
        squeeze = False
        if r3 is not None and engine_rng.random() < 0.35:
            runs += 1           # 스퀴즈 번트로 3루 주자 득점
            rbi += 1
            r3 = None
            squeeze = True
        new3 = r3
        if r2 is not None and new3 is None:
            new3, r2 = r2, None
        new2 = r2
        if r1 is not None and new2 is None:
            new2, r1 = r1, None
        bases[1], bases[2], bases[3] = r1, new2, new3
        desc = "스퀴즈 번트 성공, 3루 주자 득점" if squeeze else "희생번트 성공, 주자 진루"
    else:
        desc = "번트 실패, 타자 아웃 (주자 진루 없음)"
    return PAResult(event="BUNT", description=desc, runs=runs, rbi=rbi, outs_added=outs_added,
                     pitches=pitches, bunt=True, bip_type="BUNT",
                     hit_dir=engine_rng.uniform(0.25, 0.75), hit_depth=engine_rng.uniform(0.05, 0.15))


def _advance_on_hit_unlimited(bases: Bases, bases_gain: int, batter_pcode: int, engine_rng: random.Random,
                     speed_proxy_fn) -> tuple[int, int]:
    """bases_gain: 1=단타,2=2루타,3=3루타. 반환: (득점수, RBI).
    단순화된 베이스러닝 규칙: 3루주자는 항상 득점, 나머지는 타구 종류/주자 발빠르기(성향 대용치)에
    따라 확률적으로 추가진루 여부를 결정한다 (실제 타구 방향/깊이까지는 모델링하지 않음 - 문서화된 근사)."""
    r1, r2, r3 = bases.get(1), bases.get(2), bases.get(3)
    runs = 0
    rbi = 0

    if bases_gain == 3:
        for r in (r1, r2, r3):
            if r:
                runs += 1
                rbi += 1
        bases[1], bases[2], bases[3] = None, None, batter_pcode
        return runs, rbi

    if bases_gain == 2:
        if r3:
            runs += 1
            rbi += 1
        if r2:
            runs += 1
            rbi += 1
        new3 = None
        if r1:
            speed = speed_proxy_fn(r1)
            if engine_rng.random() < (0.40 + 0.30 * speed):
                runs += 1
                rbi += 1
            else:
                new3 = r1
        bases[1] = None
        bases[2] = batter_pcode
        bases[3] = new3
        return runs, rbi

    # 단타 (bases_gain == 1)
    if r3:
        runs += 1
        rbi += 1
    new3 = None
    if r2:
        speed = speed_proxy_fn(r2)
        if engine_rng.random() < (0.55 + 0.30 * speed):
            runs += 1
            rbi += 1
        else:
            new3 = r2
    new2 = r1
    bases[1] = batter_pcode
    bases[2] = new2
    bases[3] = new3
    return runs, rbi


def _advance_on_hit(bases: Bases, bases_gain: int, batter_pcode: int, engine_rng: random.Random,
                     speed_proxy_fn, max_runs: Optional[int] = None) -> tuple[int, int]:
    before = dict(bases)
    runs, rbi = _advance_on_hit_unlimited(bases, bases_gain, batter_pcode, engine_rng,
                                          speed_proxy_fn)
    if max_runs is None or runs < max_runs:
        return runs, rbi

    # Stop scoring at the winning run; retain trailing runners on base.
    scored = [b for b in (3, 2, 1)
              if before[b] is not None and before[b] not in bases.values()]
    winning_base = scored[max_runs - 1]
    gain = min(bases_gain, 4 - winning_base)
    bases.update(empty_bases())
    for b in (3, 2, 1):
        if before[b] is not None and b not in scored[:max_runs]:
            bases[min(3, b + gain)] = before[b]
    bases[gain] = batter_pcode
    return max_runs, max_runs


def _force_advance_walk(bases: Bases, batter_pcode: int) -> int:
    """볼넷/사구: 강제 진루만 발생 (표준 야구 규칙). 반환: 득점수."""
    runs = 0
    if bases.get(1) is not None:
        if bases.get(2) is not None:
            if bases.get(3) is not None:
                runs += 1  # 만루에서 밀어내기 득점
            bases[3] = bases[2]
        bases[2] = bases[1]
    bases[1] = batter_pcode
    return runs


def resolve_plate_appearance(league: LeagueData, offense_batter_pcode: int, defense_pitcher_pcode: int,
                              defense_lineup: List[int], roster_state: GameRosterState,
                              engine_rng: random.Random, bases: Bases, outs: int,
                              runs_to_win: Optional[int] = None) -> PAResult:
    steal_events = []
    steal = maybe_attempt_steal(bases, roster_state, engine_rng, outs)
    extra_outs_from_steal = 0
    if steal:
        steal_events.append(steal)
        extra_outs_from_steal += steal.get("outs_added", 0)
        if outs + extra_outs_from_steal >= 3:
            return PAResult(event="CS_INNING_END", description=steal["text"], runs=0, rbi=0,
                             outs_added=extra_outs_from_steal, pitches=[], steal_events=steal_events)

    if maybe_attempt_bunt(bases, offense_batter_pcode, roster_state, engine_rng, outs + extra_outs_from_steal):
        res = _resolve_bunt(bases, offense_batter_pcode, engine_rng)
        res.outs_added += extra_outs_from_steal
        res.steal_events = steal_events
        batter_rt = roster_state.get(offense_batter_pcode)
        batter_rt.swing_count += count_swings(res.pitches)
        pitcher_rt = roster_state.get(defense_pitcher_pcode)
        pitcher_rt.pitch_count += len(res.pitches)
        return res

    batter_rt = roster_state.get(offense_batter_pcode)
    pitcher_rt = roster_state.get(defense_pitcher_pcode)
    probs = resolve_pa_probabilities(league, offense_batter_pcode, defense_pitcher_pcode,
                                      batter_rt.fatigue_mult(), pitcher_rt.fatigue_mult(), engine_rng)
    events = list(probs.keys())
    weights = list(probs.values())
    event = engine_rng.choices(events, weights=weights)[0]

    pitches = generate_pitch_sequence(event, engine_rng, batter_rt.fatigue_mult())
    batter_rt.swing_count += count_swings(pitches)
    pitcher_rt.pitch_count += len(pitches)

    def speed_proxy(pcode):
        t = roster_state.get(pcode).traits
        return t.steal_pref if t else 0.3

    runs, rbi, outs_added, desc, error, bip_desc = 0, 0, 0, "", False, None
    fielder_pcode, bip_type, hit_dir, hit_depth = None, None, None, None

    if event == "BB" or event == "HBP":
        runs += _force_advance_walk(bases, offense_batter_pcode)
        rbi += runs
        desc = "볼넷 출루" if event == "BB" else "몸에 맞는 볼로 출루"
    elif event in ("1B", "2B", "3B"):
        gain = {"1B": 1, "2B": 2, "3B": 3}[event]
        runs, rbi = _advance_on_hit(bases, gain, offense_batter_pcode, engine_rng, speed_proxy,
                                    max_runs=runs_to_win)
        gain = next(b for b in (1, 2, 3) if bases[b] == offense_batter_pcode)
        event = {1: "1B", 2: "2B", 3: "3B"}[gain]
        desc = {"1B": "안타", "2B": "2루타", "3B": "3루타"}[event]
        hit_dir = engine_rng.random()
        # 단타는 얕게, 2·3루타는 깊게 (2루타는 좌우 갭, 3루타는 코너 쪽이 많도록 살짝 밀어줌)
        hit_depth = {"1B": engine_rng.uniform(0.30, 0.62),
                      "2B": engine_rng.uniform(0.62, 0.88),
                      "3B": engine_rng.uniform(0.72, 0.95)}[event]
        if event == "3B":
            hit_dir = engine_rng.choice([engine_rng.uniform(0.02, 0.22), engine_rng.uniform(0.78, 0.98)])
        bip_type = "LD" if event == "1B" else "FB"
    elif event == "HR":
        for b in (1, 2, 3):
            if bases.get(b):
                runs += 1
                rbi += 1
        bases[1], bases[2], bases[3] = None, None, None
        runs += 1
        rbi += 1
        desc = "홈런!"
        hit_dir = engine_rng.random()
        hit_depth = 1.0
        bip_type = "FB"
    elif event == "SO":
        outs_added = 1
        desc = "삼진 아웃"
    elif event == "OUT":
        bip = resolve_ball_in_play(league, offense_batter_pcode, defense_lineup, roster_state, engine_rng,
                                    outs + extra_outs_from_steal, bool(bases.get(1)), bool(bases.get(3)))
        error = bip.error
        bip_desc = bip.description
        fielder_pcode = bip.fielder_pcode
        bip_type = bip.subtype
        if bip.error:
            outs_added = 0
            # 타자가 1루에 살아나가므로 밀리는 주자만 강제 진루 (만루면 밀어내기 득점).
            # 예전에는 3루 주자를 덮어써서 주자가 사라지고 득점도 누락되는 버그가 있었음.
            forced = _force_advance_walk(bases, offense_batter_pcode)
            runs += forced
            rbi += 0   # 실책으로 들어온 점수는 타점으로 치지 않는다
            desc = f"[실책] {bip.description}"
        elif bip.double_play:
            outs_added = 2
            bases[1] = None
            # 3아웃째가 병살로 완성되면 그 사이 홈인은 인정되지 않는다 (야구규칙 5.09(c)).
            # 득점이 인정되지 않으면 주자는 3루에 남는다 (잔루 — 없애버리면 주자가 증발한다)
            if bases.get(3) and outs + extra_outs_from_steal + outs_added < 3:
                runs += 1
                rbi += 1
                bases[3] = None
            desc = "병살타"
        elif bip.sac_fly:
            outs_added = 1
            runs += 1
            rbi += 1
            bases[3] = None
            desc = "희생플라이"
        else:
            outs_added = 1
            desc = bip.description

    result = PAResult(event=event, description=desc, runs=runs, rbi=rbi,
                       outs_added=outs_added + extra_outs_from_steal, pitches=pitches,
                       steal_events=steal_events, error=error, bip_detail=bip_desc,
                       fielder_pcode=fielder_pcode, bip_type=bip_type,
                       hit_dir=hit_dir, hit_depth=hit_depth)
    return result
