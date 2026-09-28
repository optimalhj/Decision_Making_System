"""
traits.py
---------
⚠️ 중요: 이 모듈이 만들어내는 값은 **실제 KBO 기록이 아니다.**

제공된 4개 CSV(teams/batters/pitchers/matchup)에는 도루(SB/CS), 수비 실책, 포수 도루저지율
같은 데이터가 전혀 없다. 하지만 스펙 요구사항(#4)은 "타자의 속성(도루 선호, 번트, 안타,
홈런 등)"과 "수비의 속성(실수 등)"을 시뮬레이션에 반영하라고 명시한다.

이 모듈은 그런 요구를 만족시키기 위해:
1. 실제 기록이 있는 항목(번트=SAC 컬럼, 안타/홈런 성향=event_rate)은 실제 기록을 그대로 쓴다.
2. 실제 기록이 없는 항목(도루 선호, 실책 기초율)은 선수 pCode + 경기 시드로부터
   **결정론적으로 파생된 합성값**을 만든다. 즉:
   - 같은 경기(같은 시드) 안에서는 매번 같은 값 (재현 가능)
   - 경기마다(시드가 다르면) 값이 달라짐
   - "이 선수는 실제로 도루를 몇 번 했다" 같은 사실을 절대 주장하지 않음 — 오직 게임
     엔진 내부의 행동 파라미터일 뿐이다.

학생/강사에게 결과를 보여줄 때 이 합성 성향을 "실제 2025 시즌 기록"인 것처럼 표기하면 안 된다
(사용자 요청: 할루시네이션 금지). 문자중계 UI에도 이 값은 "엔진 성향치"로만 노출한다.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


def _player_rng(base_seed: int, pcode: int, salt: str) -> random.Random:
    """base_seed(경기 시드) + pCode + salt(성향 종류)로부터 결정론적 RNG 생성.
    Python의 내장 hash()는 프로세스마다 랜덤화되어(PYTHONHASHSEED) 재현 불가능하므로
    md5를 사용해 안정적인 정수 시드를 만든다."""
    h = hashlib.md5(f"{base_seed}:{int(pcode)}:{salt}".encode("utf-8")).hexdigest()
    return random.Random(int(h[:8], 16))


# 포지션별 기본 실책 기초율 (문서화된 가정치. 실제 KBO 수비 기록이 아니라 게임 밸런스를 위해
# 임의로 설정한 일반적인 야구 상식 수준의 기본값이다. 강사가 자유롭게 조정 가능하도록 상수로 분리.)
# "실책이 너무 안 나온다"는 지적에 따라 이전 값(0.010/0.022/0.014/0.018)의 1.5배로 올렸다.
# 실측(200경기, 정상 운영 기준): 팀당 9이닝 실책 0.37개 → 0.52개, 실책이 하나도 없는 경기
# 70% → 61%. KBO 실제(팀당 경기 0.7개 안팎)보다는 여전히 보수적인 값이다.
BASE_ERROR_RATE = {
    "포수": 0.015,
    "내야수": 0.033,
    "외야수": 0.021,
    "투수": 0.027,  # 투수 자신에게 타구가 갈 경우(투수 앞 땅볼 등)
}
POSITION_MISMATCH_ERROR_MULT = 1.5  # "포지션이 안 맞으면 실수확률 50% 증가"


@dataclass
class BatterTraits:
    steal_pref: float       # 0~1, 도루 시도 적극성 (합성값)
    bunt_pref: float        # 0~1, 번트 적극성 (실제 SAC 기록 기반 + 약간의 합성 변동)
    error_mult: float       # 개인 실책 배수 (0.5~1.8 범위, 합성값) - 수비할 때 사용


def derive_batter_traits(base_seed: int, pcode: int, sac_rate_real: float) -> BatterTraits:
    r_steal = _player_rng(base_seed, pcode, "steal")
    r_bunt = _player_rng(base_seed, pcode, "bunt")
    r_err = _player_rng(base_seed, pcode, "errmult")

    # 도루 선호: 대다수 선수는 낮고 일부만 높은 오른쪽 꼬리분포 (베타분포)
    steal_pref = r_steal.betavariate(1.4, 4.0)

    # 번트 선호: 실제 SAC/PA 비율(진짜 기록)을 0~1로 스케일링한 값에 약간의 합성 변동을 더함
    real_component = min(sac_rate_real * 12.0, 1.0)  # 대략 SAC/PA 8% 이상이면 1.0으로 포화
    synth_component = r_bunt.uniform(-0.15, 0.15)
    bunt_pref = max(0.0, min(1.0, real_component * 0.8 + synth_component))

    error_mult = r_err.uniform(0.5, 1.8)

    return BatterTraits(steal_pref=steal_pref, bunt_pref=bunt_pref, error_mult=error_mult)


def fielder_error_probability(base_seed: int, pcode: int, assigned_group: str, real_position: str,
                               error_mult: float, fatigue_mult: float) -> float:
    """assigned_group: 이번 수비 슬롯에서 요구되는 그룹('포수'/'내야수'/'외야수'/'투수')
    real_position: 그 선수의 실제 고정 포지션 (data_pipeline 기준)
    fatigue_mult: fatigue.performance_multiplier() 결과 (체력이 낮을수록 <1, 실책확률은 반대로 증가시켜야 함)
    """
    base = BASE_ERROR_RATE.get(assigned_group, 0.02)
    mismatch = assigned_group != real_position
    p = base * error_mult
    if mismatch:
        p *= POSITION_MISMATCH_ERROR_MULT
    # 체력이 떨어질수록(fatigue_mult가 1보다 작을수록) 실책 확률 증가
    fatigue_penalty = (1.0 - fatigue_mult)  # 0(쌩쌩) ~ MAX_DROP(탈진)
    p *= (1.0 + fatigue_penalty)
    return max(0.001, min(p, 0.35))
