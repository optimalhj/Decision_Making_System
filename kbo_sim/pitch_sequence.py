"""
pitch_sequence.py
------------------
"타석 결과를 먼저 확률적으로 확정하고, 그 결과에 맞는 그럴듯한 투구 시퀀스를 역생성"
(요구사항 #4).

이미 확정된 PA 결과(BB/HBP/SO/1B/2B/3B/HR/OUT류)를 받아서, 그 결과와 모순되지 않는
볼-스트라이크 카운트 진행과 파울볼을 그럴듯하게 만들어낸다. 이 시퀀스 자체가 경기 승패에
영향을 주지는 않는다(이미 결과는 확정됨) — 오직 문자중계 연출과, 스윙수/투구수 집계(체력 소모)를
위한 것이다.

타자 체력배수(batter_fatigue_mult)를 받아 "지친 타자일수록 루킹보다 헛스윙이 많아지도록"
스트라이크의 종류를 고른다(FATIGUE_WHIFF_TILT). 결과(삼진/볼넷 등)는 이미 확정된 뒤이므로
승패에는 영향이 없지만, 헛스윙은 swung=True라 스윙수 집계에는 반영된다.

반환하는 각 pitch: {"seq": n, "balls": int, "strikes": int, "kind": str, "swung": bool, "text": str}
kind ∈ {"ball","called_strike","swinging_strike","foul","hbp","inplay"}
"""
from __future__ import annotations

import random
from typing import Dict, List

TERMINAL_BIP_EVENTS = {"1B", "2B", "3B", "HR", "OUT"}

PITCH_LOCATION_FLAVOR = [
    "바깥쪽 낮은 코스", "몸쪽 높은 코스", "한가운데", "바깥쪽 꽉 찬 코스", "몸쪽 붙는 코스",
    "낮게 떨어지는 변화구", "높은 코스", "스트라이크존 경계선",
]


# 타자가 지치면 같은 스트라이크라도 루킹보다 헛스윙이 되기 쉽다. 완전 탈진(체력배수 0.37)일 때
# 헛스윙 확률에 0.63 * 0.20 ≈ +0.13이 더해진다 (예: 삼진 마무리 0.62 -> 0.75).
FATIGUE_WHIFF_TILT = 0.20


def _flavor(rng: random.Random) -> str:
    return rng.choice(PITCH_LOCATION_FLAVOR)


def _whiff_p(base: float, batter_fatigue_mult: float) -> float:
    """헛스윙 확률. 체력배수가 1이면 기존 값 그대로, 낮을수록 헛스윙이 늘어난다."""
    fatigue_penalty = max(0.0, 1.0 - batter_fatigue_mult)  # 0(쌩쌩) ~ MAX_DROP(탈진)
    return min(base + fatigue_penalty * FATIGUE_WHIFF_TILT, 0.95)


def generate_pitch_sequence(event: str, engine_rng: random.Random,
                             batter_fatigue_mult: float = 1.0) -> List[Dict]:
    pitches: List[Dict] = []
    balls, strikes = 0, 0

    def add(kind: str, swung: bool, text: str):
        pitches.append({"seq": len(pitches) + 1, "balls": balls, "strikes": strikes,
                         "kind": kind, "swung": swung, "text": text})

    if event == "HBP":
        n_prior = 0 if engine_rng.random() < 0.55 else 1
        for _ in range(n_prior):
            if engine_rng.random() < 0.5:
                balls += 1
                add("ball", False, f"{_flavor(engine_rng)} 볼")
            else:
                strikes += 1
                add("called_strike", False, f"{_flavor(engine_rng)} 스트라이크")
        add("hbp", False, "몸에 맞는 볼")
        return pitches

    if event == "BB":
        while balls < 4:
            # 스트라이크가 아직 3개 미만이면 확률적으로 볼/스트라이크 선택
            if strikes < 2:
                is_ball = engine_rng.random() < 0.60
            else:
                is_ball = True if engine_rng.random() < 0.55 else "foul"
            if is_ball is True:
                balls += 1
                add("ball", False, f"{_flavor(engine_rng)} 볼")
            elif is_ball == "foul":
                add("foul", True, "커트해내는 파울")
            else:
                if strikes < 2:
                    swung = engine_rng.random() < _whiff_p(0.45, batter_fatigue_mult)
                    strikes += 1
                    add("swinging_strike" if swung else "called_strike", swung,
                        "헛스윙 스트라이크" if swung else f"{_flavor(engine_rng)} 스트라이크")
                else:
                    add("foul", True, "파울로 버텨내는 타자")
        return pitches

    if event == "SO":
        # 볼넷 이외의 결과에서는 4번째 볼을 만들지 않는다. 카운트를 되돌리면 안 된다.
        swinging_final = engine_rng.random() < _whiff_p(0.62, batter_fatigue_mult)
        while strikes < 3:
            if strikes < 2:
                roll = engine_rng.random()
                if roll < 0.40 and balls < 3:
                    balls += 1
                    add("ball", False, f"{_flavor(engine_rng)} 볼")
                else:
                    swung = engine_rng.random() < _whiff_p(0.5, batter_fatigue_mult)
                    strikes += 1
                    add("swinging_strike" if swung else "called_strike", swung,
                        "헛스윙 스트라이크" if swung else f"{_flavor(engine_rng)} 스트라이크")
            else:
                # 2스트라이크: 삼진을 만들지, 파울로 연장할지
                if strikes == 2 and engine_rng.random() < 0.35:
                    add("foul", True, "아슬아슬하게 걷어내는 파울")
                else:
                    strikes = 3
                    add("swinging_strike" if swinging_final else "called_strike", swinging_final,
                        "헛스윙 삼진!" if swinging_final else "루킹 삼진!")
        return pitches

    if event in TERMINAL_BIP_EVENTS:
        n_prior = engine_rng.choices([0, 1, 2, 3, 4], weights=[28, 27, 22, 14, 9])[0]
        for _ in range(n_prior):
            roll = engine_rng.random()
            if strikes >= 2:
                # 2스트라이크에서는 파울/볼만 (인플레이는 마지막 구에만 허용)
                if roll < 0.45 and balls < 3:
                    balls += 1
                    add("ball", False, f"{_flavor(engine_rng)} 볼")
                else:
                    add("foul", True, "파울")
            else:
                if roll < 0.45 and balls < 3:
                    balls += 1
                    add("ball", False, f"{_flavor(engine_rng)} 볼")
                elif roll < 0.80:
                    strikes += 1
                    add("called_strike", False, f"{_flavor(engine_rng)} 스트라이크")
                else:
                    strikes += 1
                    add("foul", True, "파울")
        add("inplay", True, "타격")
        return pitches

    # 방어적 fallback (알 수 없는 이벤트)
    add("inplay", True, "타격")
    return pitches


def count_swings(pitches: List[Dict]) -> int:
    return sum(1 for p in pitches if p["swung"])
