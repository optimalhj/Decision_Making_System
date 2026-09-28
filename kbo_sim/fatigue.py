"""
fatigue.py
----------
체력 저하 모델 (요구사항 #4).

- 타자/야수: "14번 스윙"을 목표치로 설정(3~4이닝마다 절반 이상을 교체해야 효과적이도록
  조정한 값). 배팅 스윙 + 수비 이닝(1이닝당 스윙 환산 3~5)이 같은 스윙 카운트 풀(pool)을
  공유한다. 회복 없음(경기 내내 누적).
- 투수: "실제 등판당 평균 투구수(NP/G)"를 목표치로 설정, ±20% 개인차 랜덤.
- 목표치 자체에 랜덤을 줘서 "잘하는 선수가 항상 잘하지 않도록" 개인차/경기차를 만든다
  (스펙 요구사항: 개인차는 목표치 자체에 랜덤).
- 능력치는 시그모이드로 목표치 부근에서 급락.

시그모이드: mult(count) = 1 - MAX_DROP / (1 + exp(-k * (count - target)))
  count << target  -> mult ≈ 1.0 (거의 정상)
  count == target   -> mult ≈ 1 - MAX_DROP/2 (절반 저하)
  count >> target   -> mult ≈ 1 - MAX_DROP (최대 저하, 완전히 0이 되지는 않음)

k는 target에 비례해 정해 목표치가 큰 선발투수는 완만하게, 목표치가 작은 불펜투수는
좁은 구간에서 급하게 떨어지도록 한다. 타자/야수는 투수보다 더 가파른 계수(BATTER_STEEPNESS_NUMERATOR)를
써서 목표치 근처에서 "서서히"가 아니라 "확" 무너지게 한다 - 벤치 자원을 아끼지 않고
3~4이닝마다 절반 이상을 굴려야 효과적인 정도로 설계.
"""
from __future__ import annotations

import math
import random

# 목표치를 낮출수록(그리고 야수의 이닝당 소모 3~5회를 감안하면) 대략 3~4이닝 만에
# 도달한다 - 매 3~4이닝마다 출전 선수 절반 이상을 갈아줘야 체력 저하를 피할 수 있도록
# 의도한 값이다 (tools/calibrate.py로 득점 환경 재확인 완료).
# 2~3이닝만 지나도 체감되도록 목표치를 낮춰왔다(14.0 -> 12.0 -> 10.5).
BATTER_BASE_TARGET_SWINGS = 10.5
BATTER_TARGET_JITTER = 0.20        # 타자 목표치 개인차 (투수의 ±20% 규정을 동일 적용; 스펙에 타자 수치가
                                    # 명시되어 있지 않아 투수와 동일한 비율을 합리적 기본값으로 채택 - 문서화된 가정)
PITCHER_TARGET_JITTER = 0.20       # "투수는 실제 등판당 평균 투구수 기반 ±20%" (스펙 명시)
# 실제 등판당 평균 투구수(NP/G)를 그대로 목표치로 쓰면 혹사가 잘 드러나지 않아,
# 그보다 낮은 지점부터 지치기 시작하도록 목표치를 깎는다. NP/G 자체(실측값)는 data_pipeline에서
# 그대로 유지하고, 여기서만 "게임 내 소모 속도"로 축소한다. 투수를 특히 더 빨리 지치게 하라는
# 요청에 따라 타자보다 훨씬 크게 깎았다(0.85 -> 0.70, 실측 NP/G의 70%).
PITCHER_TARGET_SCALE = 0.70

FIELDING_SWING_EQUIV_MIN = 3.0     # "야수의 1이닝 수비는 스윙 3~5번 체력저하"
FIELDING_SWING_EQUIV_MAX = 5.0

# 완전 탈진 시 최대 능력치 저하폭. tools/calibrate.py로 실측해서 정한 값이다:
#   - 투수를 이닝마다 교체하며 정상 운영하면 9이닝 5점 안팎 (KBO 실제 팀당 경기 득점 수준)
#   - 한 투수로 9이닝을 끝까지 끌면 크게 무너짐 (혹사에 대한 분명한 페널티)
# 이 값과 probability.FATIGUE_SKILL_ALPHA가 함께 체력 -> 실제 성적 저하 폭을 결정한다.
# 지친 티가 더 뚜렷하게 나도록 계속 올려왔다(0.52->0.58->0.63, 0.60->0.67->0.73).
MAX_DROP = 0.63                     # 타자/야수 최대 저하폭
PITCHER_MAX_DROP = 0.73            # 투수는 더 깊이 무너진다 - 투구수 누적에 대한 페널티를 크게
# 투수는 목표 투구수보다 앞선 지점부터 투구수에 비례해 완만히 체력이 깎이도록 계수를 낮춘다.
# 값이 작을수록 곡선이 넓게 퍼져 "투구수에 비례한" 저하에 가까워진다.
PITCHER_STEEPNESS_NUMERATOR = 6.5   # k = PITCHER_STEEPNESS_NUMERATOR / target (투수: 완만한 곡선)
# 타자/야수는 목표치보다 한참 낮을 땐 거의 정상, 목표치 부근에서 짧은 구간(대략 ±1이닝
# 분량의 스윙수) 안에 절반 이상 떨어지도록 투수보다 훨씬 가파르게 잡는다.
BATTER_STEEPNESS_NUMERATOR = 16.0


def roll_batter_target(engine_rng: random.Random) -> float:
    lo = BATTER_BASE_TARGET_SWINGS * (1 - BATTER_TARGET_JITTER)
    hi = BATTER_BASE_TARGET_SWINGS * (1 + BATTER_TARGET_JITTER)
    return engine_rng.uniform(lo, hi)


def roll_pitcher_target(engine_rng: random.Random, base_target_pitches: float) -> float:
    scaled_base = base_target_pitches * PITCHER_TARGET_SCALE
    lo = scaled_base * (1 - PITCHER_TARGET_JITTER)
    hi = scaled_base * (1 + PITCHER_TARGET_JITTER)
    return max(engine_rng.uniform(lo, hi), 10.0)


def roll_fielding_fatigue(engine_rng: random.Random) -> float:
    """야수가 수비로 1이닝을 나갔을 때 스윙 카운트 풀에 더해지는 '환산 스윙수'."""
    return engine_rng.uniform(FIELDING_SWING_EQUIV_MIN, FIELDING_SWING_EQUIV_MAX)


def performance_multiplier(count: float, target: float,
                            steepness_numerator: float = PITCHER_STEEPNESS_NUMERATOR,
                            max_drop: float = MAX_DROP) -> float:
    if target <= 0:
        target = 1.0
    k = steepness_numerator / target
    x = k * (count - target)  # count > target 이면 x>0 -> sig->1(탈진), count < target 이면 x<0 -> sig->0(쌩쌩)
    # overflow 방지
    if x > 40:
        sig = 1.0
    elif x < -40:
        sig = 0.0
    else:
        sig = 1.0 / (1.0 + math.exp(-x))
    return 1.0 - max_drop * sig


def health_pct(count: float, target: float,
               steepness_numerator: float = PITCHER_STEEPNESS_NUMERATOR,
               max_drop: float = MAX_DROP) -> float:
    """UI에 보여줄 체력 %. performance_multiplier를 0~100% 스케일로 환산."""
    mult = performance_multiplier(count, target, steepness_numerator, max_drop)
    floor = 1.0 - max_drop
    # mult는 [floor, 1.0] 범위 -> 이를 [0,100]으로 선형 재매핑해 "완전탈진=0%, 쌩쌩=100%"로 표시
    pct = (mult - floor) / (1.0 - floor) * 100.0
    return max(0.0, min(100.0, pct))
