"""
example_tabu_lineup.py
-----------------------
Tabu Search 제출물 — **체력(스태미너) 배분**을 1순위로 놓고 라인업을 짠다.

왜 체력이 핵심인가
==================
엔진의 체력 곡선(kbo_sim/fatigue.py)을 그대로 계산해 보면 이렇다.

    야수(목표 스윙 10.5회, 수비 1이닝당 3~5스윙 + 타석당 1.55스윙)
      수비 1이닝 후 : 누적  5.0스윙 → 체력 100%
      수비 2이닝 후 : 누적 10.0스윙 → 체력  68%
      수비 3이닝 후 : 누적 15.0스윙 → 체력   0%   ← 절벽

시그모이드는 목표치 앞에서는 평평하고 목표치에서 수직으로 떨어진다. 그래서
**현재 health_pct를 보고 반응하는 방식은 항상 한 박자 늦는다** — 2이닝째까지
100%로 보이다가 3이닝째에 이미 탈진해 있다. 이 예제의 이전 버전이 체력을
`0.4 + 0.6 * health` 같은 선형 항으로만 쓰던 것이 정확히 그 함정이었다.

그래서 이 버전은 "지금 체력"이 아니라 **"이 배치가 앞으로 깎아먹을 체력"**을 본다.

    (1) 이번 이닝에 그 선수가 소모할 스윙/투구수를 먼저 더한다(선반영).
    (2) 이닝 중간 시점의 능력배수로 이번 이닝 기여를 평가한다.
    (3) 그 소모로 **남은 이닝에서 잃게 될 가치**를 기회비용으로 빼준다.

DH 자리는 수비 소모가 없어서(엔진은 defense[0..7]에만 수비 체력을 물린다)
9이닝을 내리 뛰어도 체력 97%가 남는다. 위 (3) 덕분에 탐색이 알아서
"제일 좋은 타자는 DH에 모셔두고, 수비는 체력을 써도 아까울 게 없는 선수로 돌린다"를
찾아낸다 — DH를 특별취급하는 하드코딩은 한 줄도 없다.

팀 전체로 보면 이건 자원배분 문제다.
    공급 = 야수 27명 × 10.5스윙  ≈ 283스윙
    수요 = 9이닝 × 8수비 × 4.0   ≈ 288스윙  (+ 타석 소모 약 85)
공급이 수요보다 적다. 즉 **아무도 안 지치게 하는 건 불가능**하고, 누구의 체력을
어디에 쓸지 고르는 게 전부다. 그래서 이닝이 늦을수록 기회비용을 0에 수렴시켜
9회에는 체력을 아끼지 않고 전부 태운다.

그 밖의 업그레이드
==================
- 평가 단위를 OPS 가중합 → **기대 득점(선형 가중치)**으로 교체.
- 체력이 확률에 붙는 방식을 엔진과 동일하게 재현:
  유리사건 × (타자배수/투수배수)^0.86, 불리사건은 그 역수 (probability.py).
- 타순 가치는 고정 가중치 배열 대신 **그 타순이 실제로 타석에 설 확률**로 계산.
  (아웃 3개 전에 t번째 타자까지 도달할 확률 — 이닝 시작 인덱스까지 반영)
  이 확률이 곧 타석 스윙 소모량이라, 타순과 체력이 자연스럽게 연동된다.
- 상대 투수를 1명으로 찍지 않고 **등판확률 분포**로 다뤄 맞대결·체력을 기댓값으로 반영
  (아래 별도 절).
- 체력 -> 확률배수 변환에 엔진의 상한 1.45(probability.FATIGUE_FACTOR_CAP)를 적용.
  이게 빠져 있으면 탈진한 투수(배수 0.27)를 상대할 때 배수를 3.08로 계산해 2배 넘게
  과대평가하고, "지친 투수를 때린다"는 가치가 부풀어 체력 기회비용과의 저울이 망가진다.
- 투수는 무작위 이웃탐색 대신 **전수 비교로 정확히 최적**을 고른다(후보 수십 명뿐).
  누적 투구수 대비 목표치를 보고 "이번 이닝을 끝까지 버틸 수 있는가"를 적분한다.
- Tabu 기법 자체도 보강: 역이동 금지, 열망 기준, 정체 시 다변화(재시작).

측정 결과와 남은 과제
=====================
이전 버전과 홈/원정을 서로 바꿔가며 붙인 80경기(튜닝에 쓰지 않은 시드)에서
40승 11무 29패(승점비율 0.569), 득점 270 대 217이었다.

수비 8자리 선수의 평균 체력을 이닝별로 재보면 성격이 분명히 드러난다.
**측정 시점을 반드시 밝혀야 한다** — 같은 경기라도 어디서 재느냐에 따라 20포인트가 갈린다.

  (A) 의사결정 시점 — 이 알고리즘이 실제로 보는 값(이번 이닝 수비소모 반영 전)

    이닝       1     2     3     4     5     6     7     8     9 | 전체
    현재    100.0 100.0 100.0 100.0 100.0  99.6  91.9  72.0  57.6 | 91.6
    이전    100.0  99.9  98.8  99.7  99.5  99.0  96.0  85.0  63.3 | 93.7

  (B) 수비 수행 후 — 엔진이 그 이닝의 3~5스윙을 부과한 뒤(half_start 기준)

    이닝       1     2     3     4     5     6     7     8     9 | 전체
    현재     99.9  97.8  98.9  97.0  92.0  74.5  41.1  18.9   5.6 | 70.7
    이전     99.8  77.6  66.8  83.2  79.0  72.6  59.9  41.0  19.7 | 69.0

(30경기 미러링, '이전'은 체력 개정 전 버전 f141cf7 기준)

(B)를 보면 이건 "팀 전체를 덜 지치게" 만든 게 아니다(전체 70.7 대 69.0으로 큰 차이가 아니다).
위에 적었듯 공급(283스윙)이 수요(372스윙)보다 적어서 총량은 줄일 수 없기 때문이다.
바뀐 건 **체력을 언제·누구에게 쓰느냐**다. 초반 5이닝을 거의 100%로 굴리고,
좋은 타자는 DH로 피신시킨다(DH 평균 OPS 0.851 대 수비 8자리 0.635).

이번 개정(맞대결 기댓값)은 승패를 바꾸지 못했다. 홈/원정을 바꿔가며 붙인 320경기에서
승점비율 0.488 ± 0.028(표준오차), 득점 1088 대 1066이다. 맞대결만 껐다 켠 200경기 비교도
0.485 ± 0.035로 같은 결론이다. 위에 적은 대로 이 신호는 주전 9명을 뒤집는 크기가 아니라
한계 자리를 가르는 타이브레이커이고, 승패는 여전히 체력 배분이 지배한다. 확실히 좋아진 건
실행시간이다: 호출당 평균 0.132초 -> 0.053초. 맞대결 표가 2배로 커졌는데도 오히려 빨라졌는데,
표를 전처리에서 한 번만 접고 offense_runs/stamina_cost를 캐싱했기 때문이다.

체력배수 상한(1.45)도 400경기로 따로 재봤다: 0.478 ± 0.025로 역시 중립이다. 모델을 엔진에
더 정확히 맞췄는데 성적은 그대로인 전형적인 사례다. 짐작되는 이유는, 상한을 빼먹은 쪽이
"지친 투수는 한 이닝에 타자를 더 많이 상대하게 된다"는 빠진 항(이 평가함수는 이닝당 타석 수를
EXP_PA_PER_INNING으로 고정한다)을 우연히 대신 메워주고 있었다는 것이다. 그래도 상한을 남긴
이유는 이 파일의 원칙이 '엔진을 그대로 재현한다'이기 때문이다. 의심스러우면 FATIGUE_FACTOR_CAP을
아주 크게 잡아 끈 채로 A/B 해 보면 된다.

종반 혹사와 OVERUSE_W
=====================
맞대결 개정 직후에는 종반 체력이 개정 전보다 크게 나빠졌다(A 기준 8회 61.5 대 85.0).
기용한 서로 다른 야수 수는 27.8명으로 양쪽이 완전히 같았고, 다른 건 사용 횟수의 꼬리였다 —
소수를 5~7번씩 혹사했다(5회 이상 비율 5.0% 대 OLD 0.9%).

원인은 FATIGUE_FACTOR_CAP이다. 엔진이 체력배수를 [1/1.45, 1.45]로 자르므로 탈진 타자의
배수는 0.427이 아니라 **0.690**까지만 깎인다. 즉 체력 약 44% 아래로는 더 지쳐도 공격 손실이
늘지 않는다. 캡이 없는 페널티는 실책뿐인데 슬롯당 0.0072런으로 공격 차이(0.05~0.08런)의
1/10이라 회전을 강제하지 못한다. 게다가 stamina_cost는 'Δ배수'에 비례하는 한계비용이라
이미 바닥에 붙은 선수에겐 0이 된다. 결국 한 번 바닥 친 선수는 계속 쓰는 게 모델상 이득이다.
개정 전 버전이 건강해 보였던 건 캡이 빠져 있어 탈진 타자를 과대 처벌하던 부작용이었다.

그래서 OVERUSE_W(레벨 페널티)를 넣었다. 위 표의 '현재'가 그 결과이고, 8회 61.5 -> 72.0,
5회 이상 혹사 5.0% -> 3.1%로 격차의 절반쯤을 되돌렸다. 다만 **승률은 중립이다**(신규 시드
180경기 0.5417 ± 0.0373, 신뢰구간이 0.5를 포함). 엔진이 깊은 피로를 별로 벌하지 않으니
당연한 결과다 — 이 항은 이기게 해주는 장치가 아니라 같은 승률에서 체력 배분을 덜 극단적으로
만드는 장치다.

남은 과제: 그래도 7~9회는 여전히 약하다(B 기준 9회 5.6%). 종반용으로 몇 명을 아예 아껴두는
'예약(reserve)'이나, 이닝별 레버리지(점수차)를 기회비용에 곱하는 방향이 다음 후보다.
단 FUTURE_COST_W를 그냥 키우는 방식은 격자탐색에서 효과가 확인되지 않았다.

상대 투수는 '한 명'이 아니라 '확률분포'다
=========================================
decide_lineup은 양 팀이 이닝 시작에 동시에 호출되므로 **이번 이닝에 누가 올라올지 알 수 없다.**
context["opp_pitcher_pcode"]는 상대의 *직전* 이닝 투수이고, 1회에는 아예 None이다.
그런데 matchups 표에는 1회부터 **상대 투수 전원 x 우리 타자 전원**이 들어 있다.
이전 버전은 그 표를 직전 이닝 투수 1명으로 필터링해서 썼고, 그 결과 1회에는 맞대결을
한 줄도 쓰지 못했다(표의 절반 이상을 버린 셈이다).

이 버전은 필터링 대신 **기댓값**을 쓴다.

    (1) 상대 투수 j가 이번 이닝에 등판할 확률 P_j 를 추정한다.
          · 직전 이닝 투수 -> '잔류확률' p_stay
              = (상대가 지금까지 얼마나 자주 투수를 이어 썼는가, 관측으로 온라인 추정)
              x (한 이닝 더 던져도 쓸 만한 체력이 남았는가)
          · 나머지 투수 -> "좋은 투수일수록 올라온다"는 softmax (온도 tau)
          · 마지막에 균등분포를 eps만큼 섞는다 — 추정이 틀렸을 때의 손해 상한
    (2) 타자 i의 사건확률을 P_j로 가중평균한다. 엔진의 블렌딩이 선형이라 정확히 접힌다.
          r_i = sum_j P_j * [ w_ji*e_ji + (1-w_ji)*r0_i ] = (1-W_i)*r0_i + W_i*e_i
          W_i = sum_j P_j * PA_ji/(PA_ji+15)        <- 엔진 축소상수(15)를 그대로 쓴다
          e_i = sum_j P_j * w_ji * e_ji / W_i       <- P가중 경험분포
    (3) 상대 투수의 체력배수도 같은 P_j로 평균낸다. 단 득점식이 (타자배수/투수배수)^0.86
        에만 의존하므로 산술평균이 아니라 **멱평균**(지수 -0.86)이 맞다.

핵심은 (2)의 마지막 등식이다. 표 전체를 전처리에서 **타자당 (W_i, e_i) 한 쌍**으로 접어두면
탐색 루프(hot path)는 예전과 똑같이 "타자당 사건확률 벡터 1개"만 본다 — 즉 표가 2배로
커져도 탐색 비용은 0원이다. 상대 투수 20여 명을 평가함수 안에서 매번 합산하는 구현은
호출 수가 수만 번이라 10초 제한을 위협한다. **비용은 반드시 전처리에 가둬야 한다.**

효과의 크기는 정직하게 적어 둔다. 10개 팀 전타자를 1회 기준으로 재보면 맞대결이 타자의
타석당 가치를 움직이는 폭은 표준편차 0.0065런(범위 -0.025 ~ +0.035)이다. 9번째와 10번째
타자의 가치 간격이 평균 0.0029런이니 딱 그 두 배 남짓 — **주전 9명을 뒤집는 신호가 아니라
한계 자리를 가르는 타이브레이커**다. 대신 1회처럼 정보가 아예 없던 구간이 메워지는 게
실질적인 이득이다. MATCHUP_TRUST = 0 으로 두면 맞대결 반영만 통째로 꺼서
(등판확률 모형과 체력 평균은 그대로 둔 채) "켠 쪽/끈 쪽"을 같은 시드로 비교할 수 있다.

이닝 선발 규칙: decide_lineup은 이닝마다 팀당 한 번 호출되며 {"defense": [10명],
"offense": [9명]}을 반환한다. defense는 [내야x4, 외야x3, 포수, DH, 투수] 순서,
offense는 defense의 앞 9명(투수 제외)을 타순대로 재배열한 것이다. 공수교대 때 교체는 없다.
"""
import math
import time

import pandas as pd

# ---------------------------------------------------------------------
# 엔진에서 그대로 옮겨온 상수 (kbo_sim/fatigue.py, probability.py, traits.py)
# 엔진을 import할 수 없으므로 값을 복제한다. 엔진이 바뀌면 여기도 맞춰야 한다.
# ---------------------------------------------------------------------
BAT_STEEPNESS, BAT_MAX_DROP = 16.0, 0.63      # 야수: 가파른 절벽
PIT_STEEPNESS, PIT_MAX_DROP = 6.5, 0.73       # 투수: 완만하지만 더 깊게
FATIGUE_ALPHA = 0.86                           # 체력비 -> 사건확률 배수 지수
BASE_ERROR_RATE = {"포수": 0.010, "내야수": 0.022, "외야수": 0.014, "투수": 0.018}
MISMATCH_ERROR_MULT = 1.5
MEAN_ERROR_MULT = 1.15                         # 개인 실책성향 U(0.5,1.8)의 평균 (엔진 비공개값)

# 엔진을 돌려 실측한 소모 계수
FIELD_SWINGS_PER_INNING = 4.0                  # 수비 1이닝 = 스윙 3~5회의 평균
SWINGS_PER_PA = 1.555                          # 타석 1번당 평균 스윙 수
PITCHES_PER_PA = 3.33                          # 타석 1번당 평균 투구 수
OUT_RATE_PER_PA = 0.66                         # 타석당 아웃 확률(타순 도달 확률 계산용)

# 사건별 득점 가치(아웃 대비). 세이버메트릭스 선형 가중치.
RUN_VALUE = {"BB": 0.63, "HBP": 0.66, "1B": 0.78, "2B": 1.09,
             "3B": 1.40, "HR": 1.72, "SO": -0.02, "OUT": 0.0}
EVENTS = ("BB", "HBP", "1B", "2B", "3B", "HR", "SO", "OUT")
FAVORABLE = ("BB", "HBP", "1B", "2B", "3B", "HR")   # 타자에게 유리 (체력비가 곱해짐)
# 리그 평균 사건 분포(사전분포). 표본이 적은 선수를 이쪽으로 축소시킨다.
PRIOR = {"BB": 0.090, "HBP": 0.015, "1B": 0.170, "2B": 0.045,
         "3B": 0.005, "HR": 0.025, "SO": 0.190, "OUT": 0.460}
PRIOR_PA, PRIOR_TBF = 60.0, 80.0
MATCHUP_SHRINK_PA = 15.0                       # 엔진과 동일
FATIGUE_FACTOR_CAP = 1.45                      # 체력배수의 최종 상한(하한은 역수). 엔진과 동일

# -- 상대 투수 등판확률 추정 ------------------------------------------
# 이번 이닝에 상대가 누구를 올릴지는 알 수 없다. 그래서 '1명 찍기'가 아니라 분포로 둔다.
OPP_INNING_PITCHES = 14.0     # 하프이닝당 상대 투구수. 실측 평균 13.5·중앙값 12이라
                              # EXP_PA_PER_INNING*PITCHES_PER_PA(=15.1)보다 약간 낮게 잡았다.
                              # 목표치가 15 안팎인 불펜은 이 1~2구 차이로 체력배수가 크게 갈린다.
OPP_SOFTMAX_TAU = 0.15        # "좋은 투수일수록 올라온다"의 강도(런 단위). 작을수록 1명에 몰린다.
OPP_EPSILON = 0.15            # 균등분포를 이만큼 섞는다 (추정이 틀렸을 때의 손해 상한)
OPP_STAY_A, OPP_STAY_B = 1.2, 1.0   # 잔류비율의 Beta 사전분포. 평균 0.55 (실측 0.50~0.57)
OPP_STAY_FLOOR = 0.25         # 체력이 바닥나도 안 바꾸는 상대가 있으므로 잔류확률을 0으로 두지 않는다
OPP_STAY_CAP = 0.85           # 반대로 확신도 금물 — 상한도 둔다
MATCHUP_TRUST = 1.0           # 맞대결 반영 강도. 0이면 맞대결만 통째로 off (A/B 비교용 스위치.
                              # 등판확률·체력 평균은 영향받지 않는다)
MATCHUP_W_CAP = 0.5           # 쌍별 축소가중치 상한. 동봉 데이터의 최대 PA가 15라
                              # PA/(PA+15)=0.5로 상한과 정확히 같다(20013행 중 5행). 즉 현재
                              # 데이터에서 이 상한은 '걸리지만 값을 바꾸지는 않는' 안전장치다.

# 수비 기회 배분(타구가 그 자리로 갈 확률)과 실책 1개의 실점 비용
CHANCE_SHARE = (0.12, 0.12, 0.12, 0.12, 0.15, 0.15, 0.15, 0.02)   # 내야4·외야3·포수
BALLS_IN_PLAY_PER_INNING = 2.9
ERROR_RUN_COST = 0.50

SLOT_POS = ["내야수", "내야수", "내야수", "내야수", "외야수", "외야수", "외야수", "포수", "DH", "투수"]
LAST_INNING = 9

# -- 체력 기회비용 가중치 --------------------------------------------
# 이번 이닝에 소모한 체력 때문에 "남은 이닝에서 잃는 가치"에 곱하는 계수.
# 이 값이 0이면 예전 버전처럼 근시안적으로 매 이닝 최고 타자만 갈아넣게 된다.
FUTURE_COST_W = 0.45
# 남은 이닝 중 그 선수를 다시 쓸 법한 비율(전원을 매 이닝 쓸 수는 없으므로 1보다 작다)
REUSE_FRACTION = 0.45
# 이미 지친 선수를 '또' 기용하는 데 붙는 레벨(level) 페널티. 단위는 런.
#
# 왜 별도 항이 필요한가: 위 FUTURE_COST_W 항은 '이번에 깎이는 양(Δ배수)'에 비례하는
# 한계비용이라, 목표치를 한참 넘겨 이미 바닥에 붙은 선수는 Δ가 0이라 비용도 0이 된다.
# 게다가 엔진이 체력배수를 [1/1.45, 1.45]로 자르기 때문에(FATIGUE_FACTOR_CAP) 체력 44%
# 아래로는 공격 손실도 더 늘지 않는다. 즉 한 번 바닥을 친 선수는 '공짜로 계속 쓸 수 있는'
# 상태가 되고, 실제로 그 결과 소수를 5~7번씩 혹사하는 분포가 나왔다.
# 이 항은 배수가 낮다는 사실 자체에 비용을 매겨(한계가 아니라 레벨) 회전을 유도한다.
#
# 값 선택 근거(신규 시드 180경기씩, 홈/원정 미러):
#   W      승점비율            득실차   8~9회체력   5회+혹사
#   0.00   (기준)                 —        53.7       5.0%
#   0.10   0.5417 ± 0.0373      +32        64.8       3.1%   <- 채택
#   0.20   0.5306 ± 0.0373      -19        68.9       1.9%
# 둘 다 신뢰구간이 0.5를 포함해 **승률은 중립**이다. 즉 이 항은 이기게 해주는 장치가 아니라,
# 같은 승률에서 체력 배분을 덜 극단적으로 만드는 장치다. 0.20은 체력을 4포인트 더 올리지만
# 득실차(승패보다 분산이 작은 지표)의 부호가 뒤집혀서 0.10을 골랐다.
# 주의: 1차 격자탐색에서 0.20이 0.650을 낸 적이 있는데 신규 시드 180경기에서 0.5306으로
# 회귀했다. 양옆(0.10, 0.40)이 모두 0.475였던 '외딴 봉우리'라 잡음이었다. 값을 바꿀 땐
# 반드시 튜닝에 쓰지 않은 시드로 재검증할 것.
OVERUSE_W = 0.10
# 남은 이닝 수에 대한 기회비용의 차수. 1.0 = "남은 이닝 수에 비례"(선형).
# 지금 Δ만큼 체력을 태우면 앞으로 쓸 (horizon x REUSE_FRACTION) 이닝 각각에서
# Δ만큼 손해를 보므로 선형이 이론적으로 맞는 형태다.
#
# 주의(튜닝 결과): 40경기 격자탐색에서 FUTURE_COST_W>0은 W=0보다 확실히 좋았지만
# (승점비율 약 0.64 대 0.53), W와 이 차수의 '정확한 값'까지는 구분되지 않았다.
# 셀당 40경기면 표준오차가 ±0.08이라 0.1~0.2 차이는 잡음이다. 그래서 격자의 최고점을
# 그대로 쓰지 않고, 이론적으로 맞는 차수(1.0)와 두 시드집합에서 가장 일관됐던
# 가중치(0.45)를 골랐다. 값을 바꿀 땐 반드시 튜닝에 쓰지 않은 시드로 재검증할 것.
HORIZON_EXPONENT = 1.0

# -- Tabu Search 파라미터 (10초 제한에 여유 있게) --------------------
TS_ITERS_DEFENSE, TS_ITERS_OFFENSE = 140, 90
NEIGHBORS_PER_ITER = 30
TABU_TENURE = 8
STAGNATION_LIMIT = 25          # 이만큼 최고해 갱신이 없으면 흔들어서 다시 탐색
SAFETY_MARGIN_SEC = 2.5        # 제한시간에서 이만큼 남겨두고 비상 탈출


def _num(v, default):
    """v가 없거나(None) 결측(NaN)이면 default, 0.0처럼 유효한 실측값이면 그대로 반환한다.
    `row.get(col) or default`로 쓰면 진짜 0인 값(OPS 0.000, ERA 0.00 등)까지 "없는 값" 취급해
    default로 바꿔버리는 버그가 생긴다 (파이썬에서 0은 falsy이기 때문)."""
    return default if v is None or pd.isna(v) else v


# ---------------------------------------------------------------------
# 1. 체력 — 엔진의 시그모이드를 그대로 재현하고, '앞으로'를 계산한다
# ---------------------------------------------------------------------
def _sigmoid_mult(count, target, steepness, max_drop):
    """엔진 fatigue.performance_multiplier와 동일. 1.0=쌩쌩, (1-max_drop)=탈진."""
    target = target if target > 0 else 1.0
    x = (steepness / target) * (count - target)
    if x > 40:
        sig = 1.0
    elif x < -40:
        sig = 0.0
    else:
        sig = 1.0 / (1.0 + math.exp(-x))
    return 1.0 - max_drop * sig


def bat_mult(prof, extra_swings=0.0):
    """스윙을 extra_swings만큼 '더 했다고 치고' 계산한 능력배수 (선반영이 핵심)."""
    return _sigmoid_mult(prof["swings"] + extra_swings, prof["swing_target"],
                         BAT_STEEPNESS, BAT_MAX_DROP)


def pit_mult(prof, extra_pitches=0.0):
    return _sigmoid_mult(prof["pitches"] + extra_pitches, prof["pitch_target"],
                         PIT_STEEPNESS, PIT_MAX_DROP)


def _target_from_health(health_pct, max_drop, count, steepness):
    """swing_target/pitch_target이 결측일 때 health_pct로부터 목표치를 역산한다.
    health_pct는 배수를 [1-max_drop, 1] -> [0,100]으로 선형 재매핑한 값이라 되돌릴 수 있다."""
    mult = (1.0 - max_drop) + (max(0.0, min(100.0, health_pct)) / 100.0) * max_drop
    ratio = (1.0 - mult) / max_drop
    if ratio <= 1e-9 or ratio >= 1 - 1e-9 or count <= 0:
        return None                      # 정보가 없다 — 호출부에서 기본값을 쓴다
    # mult = 1 - max_drop*sig(k*(count-target)), k = steepness/target 를 target에 대해 푼다
    logit = math.log(ratio / (1.0 - ratio))
    denom = steepness + logit
    return count * steepness / denom if denom > 1e-9 else None


# ---------------------------------------------------------------------
# 2. 기록 -> 사건확률 -> 기대득점
# ---------------------------------------------------------------------
def _event_rates(row, is_pitcher):
    """실측 사건 횟수에 리그 사전분포를 더해(축소추정) 표본이 적은 선수를 안정화한다."""
    n = float(_num(row.get("TBF_eff" if is_pitcher else "PA_eff"),
                   _num(row.get("TBF" if is_pitcher else "PA"), 0.0)))
    h = float(_num(row.get("H"), 0.0))
    d = float(_num(row.get("2B"), 0.0))
    t = float(_num(row.get("3B"), 0.0))
    hr = float(_num(row.get("HR"), 0.0))
    bb = float(_num(row.get("BB"), 0.0))
    hbp = float(_num(row.get("HBP"), 0.0))
    so = float(_num(row.get("SO"), 0.0))
    counts = {"BB": bb, "HBP": hbp, "1B": max(h - d - t - hr, 0.0), "2B": d, "3B": t, "HR": hr,
              "SO": so, "OUT": max(n - h - bb - hbp - so, 0.0)}
    k = PRIOR_TBF if is_pitcher else PRIOR_PA
    vals = {ev: max(counts[ev], 0.0) + k * PRIOR[ev] for ev in EVENTS}
    total = sum(vals.values())
    return {ev: v / total for ev, v in vals.items()}


def _blend_pooled(rate, entry):
    """맞대결 실적을 섞는다. entry = (W, ebar) = build_pooled_matchup()이 미리 접어둔 한 쌍.

    엔진은 투수가 정해진 뒤 rate <- w*e + (1-w)*rate (w = PA/(PA+15))를 쓴다.
    우리는 이번 이닝 투수가 누구일지 모르므로 그 블렌딩 결과를 등판확률 P_j로 평균내야 하는데,
    블렌딩이 rate에 대해 선형이라 평균이 정확히 같은 모양으로 접힌다:
        sum_j P_j*[w_ji*e_ji + (1-w_ji)*rate] = (1 - W)*rate + W*ebar
    즉 '투수 20여 명에 대한 합산'이 '가중치 1개 + 분포 1개'로 줄어든다 (근사가 아니라 항등식)."""
    if entry is None:
        return rate
    w, ebar = entry
    if w <= 0.0:
        return rate
    mixed = {ev: w * ebar[ev] + (1.0 - w) * rate[ev] for ev in EVENTS}
    total = sum(mixed.values())
    return {ev: v / total for ev, v in mixed.items()}


def _runs_per_pa(rate, batter_mult, pitcher_mult):
    """체력을 엔진과 똑같은 방식으로 확률에 반영한 뒤 기대 득점가치를 낸다.
    유리사건 × (타자배수/투수배수)^0.86, 불리사건(SO/OUT)은 그 역수, 그리고 재정규화.

    엔진은 이 배수를 [1/1.45, 1.45]로 자른다(probability.FATIGUE_FACTOR_CAP). 상한이 없으면
    완전히 탈진한 투수(체력배수 0.27)를 만났을 때 배수를 3.08로 계산하게 되는데 엔진은
    1.45까지만 준다 — 2.1배 과대평가다. 그러면 '지친 투수를 때리는' 가치가 부풀어
    체력 기회비용과의 저울이 망가지므로 여기서도 똑같이 자른다."""
    factor = (batter_mult / max(pitcher_mult, 1e-3)) ** FATIGUE_ALPHA
    factor = min(max(factor, 1.0 / FATIGUE_FACTOR_CAP), FATIGUE_FACTOR_CAP)
    inv = 1.0 / factor
    adj = {ev: rate[ev] * (factor if ev in FAVORABLE else inv) for ev in EVENTS}
    total = sum(adj.values())
    return sum(RUN_VALUE[ev] * v / total for ev, v in adj.items())


# ---------------------------------------------------------------------
# 3. 타순별 '타석에 설 확률' — 타격 가치와 체력 소모를 동시에 결정한다
# ---------------------------------------------------------------------
def pa_probability_by_turn():
    """선두타자 기준 t번째 타자가 이번 이닝에 타석에 설 확률.
    = 앞선 t타석에서 아웃이 3개 미만일 확률 (이항분포의 꼬리)."""
    q = OUT_RATE_PER_PA
    probs = []
    for t in range(9):
        p = 0.0
        for k in (0, 1, 2):
            if k > t:
                break
            comb = (1.0, float(t), t * (t - 1) / 2.0)[k]
            p += comb * (q ** k) * ((1 - q) ** (t - k))
        probs.append(min(1.0, p))
    return probs


TURN_PA = pa_probability_by_turn()
EXP_PA_PER_INNING = sum(TURN_PA)


# ---------------------------------------------------------------------
# 4. 프로필 구축 (DataFrame은 딱 한 번만 훑는다)
# ---------------------------------------------------------------------
def build_profiles(my_team: pd.DataFrame):
    bat, pit = {}, {}
    for row in my_team.to_dict("records"):
        pcode = int(row["pCode"])
        health = float(_num(row.get("health_pct"), 100.0))
        if row["role"] == "타자":
            swings = float(_num(row.get("swing_count"), 0.0))
            target = _num(row.get("swing_target"), None)
            if target is None:
                target = _target_from_health(health, BAT_MAX_DROP, swings, BAT_STEEPNESS) or 10.5
            bat[pcode] = {
                "pos": row["position"],
                "rate": _event_rates(row, False),
                "swings": swings,
                "swing_target": float(target),
            }
        else:
            pitches = float(_num(row.get("pitch_count"), 0.0))
            target = _num(row.get("pitch_target"), None)
            if target is None:
                base = float(_num(row.get("NP_per_G"), 20.0)) * 0.70
                target = _target_from_health(health, PIT_MAX_DROP, pitches, PIT_STEEPNESS) or base
            pit[pcode] = {
                "rate": _event_rates(row, True),
                "pitches": pitches,
                "pitch_target": max(float(target), 10.0),
            }
    return bat, pit


def opp_pitcher_profiles(opponent_team: pd.DataFrame):
    """상대 투수 **전원**의 프로필. 키 이름을 build_profiles의 투수 쪽과 똑같이 맞춰
    pit_mult()를 그대로 재사용할 수 있게 한다.

    opponent_team에는 상대 투수 전원의 시즌기록과 현재 투구수/목표치가 이미 전부 들어 있다.
    '맞대결만 1명으로 막혀 있던' 예전 구조가 오히려 예외였던 셈이다."""
    prof = {}
    if opponent_team is None or opponent_team.empty:
        return prof
    for row in opponent_team.to_dict("records"):
        if row.get("role") != "투수":
            continue
        pitches = float(_num(row.get("pitch_count"), 0.0))
        target = _num(row.get("pitch_target"), None)
        if target is None:                              # 결측 방어 (build_profiles와 같은 순서)
            health = float(_num(row.get("health_pct"), 100.0))
            base = float(_num(row.get("NP_per_G"), 20.0)) * 0.70
            target = _target_from_health(health, PIT_MAX_DROP, pitches, PIT_STEEPNESS) or base
        prof[int(row["pCode"])] = {
            "rate": _event_rates(row, True),
            "pitches": pitches,
            "pitch_target": max(float(target), 10.0),
        }
    return prof


def opp_start_probability(prof, context):
    """상대 투수별 '이번 이닝 등판확률' P_j 와, 그걸로 평균낸 체력배수 p_bar.

    P_j는 세 조각의 합성이다.
      (a) 품질 softmax  : 이번 이닝을 잘 막을 수 있는 투수일수록 올라올 확률이 높다.
      (b) 잔류확률      : 직전 이닝 투수가 그대로 남을 확률.
                          = (상대가 지금까지 투수를 얼마나 이어 썼는가 — 관측으로 온라인 추정)
                          x (한 이닝 더 던질 체력이 남았는가)
      (c) 균등 바닥 eps : (a)(b)가 통째로 틀렸을 때의 손해를 막는 보험.

    context["opp_pitcher_pcode"]를 버리는 게 아니라 **하드필터에서 사전확률로 강등**하는 것이다.
    직전 이닝 투수는 여전히 가장 강한 단일 예측자다 (전략에 따라 잔류비율 0.14~1.00, 평균 0.5).

    반환 확률의 합은 1. 누적 순서가 프로세스마다 달라지지 않도록 **정렬된 정수 키**로만
    순회한다 — 학생 함수는 매 호출 새 프로세스에서 실행되므로 해시 순서에 기대면 안 된다."""
    codes = sorted(prof)
    m = len(codes)
    if m == 0:
        return {}, 1.0
    ip = OPP_INNING_PITCHES

    # (a) 품질 점수 -> softmax. pitcher_value와 같은 3점 적분이되, 상대의 '남은 이닝 체력'까지
    #     대신 걱정해 줄 필요는 없으므로 이번 이닝의 실점 억제력만 본다.
    quality = {}
    for p in codes:
        pr = prof[p]
        runs = 0.0
        for frac in (1.0 / 6.0, 0.5, 5.0 / 6.0):
            runs += _runs_per_pa(pr["rate"], 1.0, pit_mult(pr, ip * frac)) * (EXP_PA_PER_INNING / 3.0)
        quality[p] = -runs
    top = max(quality.values())
    expo = {p: math.exp((quality[p] - top) / OPP_SOFTMAX_TAU) for p in codes}
    z = sum(expo[p] for p in codes)
    base = {p: expo[p] / z for p in codes}

    prob = base
    prev = context.get("opp_pitcher_pcode")              # 1회에는 None
    prev = int(prev) if prev is not None and not pd.isna(prev) else None
    if prev is not None and prev in prof:
        # -- 상대의 교체 습관을 관측으로 추정한다 (Beta 사전분포 + 온라인 갱신) --
        inning = int(_num(context.get("inning"), 1))
        n = max(inning - 1, 0)                           # 상대가 이미 치른 수비 이닝 수
        used = sum(1 for p in codes if prof[p]["pitches"] > 0.0)
        used = max(used, 1) if n >= 1 else used          # n이닝을 치렀다면 최소 1명은 썼다
        repeats = max(n - used, 0)                       # 같은 투수를 이어 쓴 횟수
        stay_rate = (repeats + OPP_STAY_A) / (max(n - 1, 0) + OPP_STAY_A + OPP_STAY_B)

        # 한 이닝 더 던져도 쓸 만한가 (탈진 바닥 1-PIT_MAX_DROP 을 0으로 놓고 정규화)
        floor = 1.0 - PIT_MAX_DROP
        feas = (pit_mult(prof[prev], ip) - floor) / (1.0 - floor)
        feas = min(max(feas, 0.0), 1.0)
        if prof[prev]["pitches"] - ip > prof[prev]["pitch_target"]:
            feas = 1.0          # 이미 목표치를 넘긴 투수를 지난 이닝에 냈다 = 체력을 안 보는 상대
        p_stay = stay_rate * (OPP_STAY_FLOOR + (1.0 - OPP_STAY_FLOOR) * feas)
        if used == 1 and n >= 3:
            p_stay = max(p_stay, 0.80)                   # 한 투수로 끝까지 가는 유형 확정
        p_stay = min(max(p_stay, 0.03), OPP_STAY_CAP)

        rest = 1.0 - base[prev]
        if rest > 1e-9:                                  # 남은 확률을 품질 비율대로 나눠 준다
            prob = {p: (p_stay if p == prev else (1.0 - p_stay) * base[p] / rest) for p in codes}

    # (c) 균등 바닥. 상대가 일부러 기용을 섞어도 손해가 여기서 멈춘다.
    prob = {p: (1.0 - OPP_EPSILON) * prob[p] + OPP_EPSILON / m for p in codes}
    total = sum(prob[p] for p in codes)                  # 부동소수 오차만 정리
    prob = {p: prob[p] / total for p in codes}

    # 유효 체력배수: 기대득점이 (타자배수/투수배수)^ALPHA 에만 의존하므로 E[mult^-ALPHA]를
    # 보존하는 **멱평균**이 맞다. 산술평균을 쓰면 지친 투수의 기여가 과소평가된다.
    acc = 0.0
    for p in codes:
        acc += prob[p] * pit_mult(prof[p], ip * 0.5) ** (-FATIGUE_ALPHA)
    return prob, acc ** (-1.0 / FATIGUE_ALPHA)


def build_pooled_matchup(matchups: pd.DataFrame, prob, my_batters):
    """맞대결 표 전체를 **우리 타자 1명당 (W, ebar) 한 쌍**으로 접는다.

        W    = sum_j P_j * PA_ji/(PA_ji+15)   (풀링 가중치. 엔진 축소상수 15를 그대로 쓴다)
        ebar = P_j 가중 경험 사건분포

    유도는 _blend_pooled 참고. 표를 딱 한 번만 훑으므로 비용이 호출당 수 ms에 머문다.
    W는 저절로 2차 축소가 걸린다: W = sum_j P_j*w_ji <= max_j w_ji. 누가 나올지 모를수록
    맞대결이 알아서 덜 반영되는 셈이라, 여기에 축소를 한 번 더 걸면 신호만 죽는다.

    주의: 이 표에는 '우리 투수 x 상대 타자' 블록도 함께 들어 있다. 반드시 **투수 쪽을 먼저**
    걸러야 한다. 타자 쪽만 보고 걸러도 지금은 우연히 맞지만(우리 투수가 상대한 타자는
    우리 타자가 아니므로), 로스터 처리가 조금만 바뀌면 우리 투수의 피안타 기록이
    우리 타자의 성적으로 조용히 섞여 들어간다."""
    pooled = {}
    if matchups is None or matchups.empty or not prob or MATCHUP_TRUST <= 0.0:
        return pooled
    if "pitcherPCode" not in matchups.columns or "hitterPCode" not in matchups.columns:
        return pooled
    sub = matchups[matchups["pitcherPCode"].isin(set(prob))
                   & matchups["hitterPCode"].isin(set(my_batters))]
    if sub.empty:
        return pooled

    acc = {}
    for r in sub.to_dict("records"):
        pa = float(_num(r.get("PA"), 0.0))
        if pa <= 0.0:
            continue
        pj = prob.get(int(r["pitcherPCode"]), 0.0)
        # 쌍별 축소가중치는 엔진과 동일. 1~2타석짜리 표본이 분포를 통째로 흔들지 못한다.
        w = pj * min(pa / (pa + MATCHUP_SHRINK_PA), MATCHUP_W_CAP) * MATCHUP_TRUST
        if w <= 0.0:
            continue
        # AVG/OPS/SLG 같은 파생 컬럼은 쓰지 않는다 — PA가 1~2면 0.000/1.000으로 튄다.
        h = float(_num(r.get("H"), 0.0))
        d = float(_num(r.get("2B"), 0.0))
        t = float(_num(r.get("3B"), 0.0))
        hr = float(_num(r.get("HR"), 0.0))
        bb = float(_num(r.get("BB"), 0.0))
        hbp = float(_num(r.get("HBP"), 0.0))
        so = float(_num(r.get("SO"), 0.0))
        hitter = int(r["hitterPCode"])
        ent = acc.get(hitter)
        if ent is None:
            ent = acc[hitter] = [0.0, {ev: 0.0 for ev in EVENTS}]
        ent[0] += w
        s = ent[1]
        s["BB"] += w * bb / pa
        s["HBP"] += w * hbp / pa
        s["1B"] += w * max(h - d - t - hr, 0.0) / pa
        s["2B"] += w * d / pa
        s["3B"] += w * t / pa
        s["HR"] += w * hr / pa
        s["SO"] += w * so / pa
        s["OUT"] += w * max(pa - h - bb - hbp - so, 0.0) / pa

    for hitter in sorted(acc):
        w_sum, s = acc[hitter]
        if w_sum <= 1e-9:
            continue
        # ebar는 '자르지 않은' 합으로 나눠야 확률분포가 된다. 상한은 W에만 건다.
        pooled[hitter] = (min(w_sum, 1.0), {ev: s[ev] / w_sum for ev in EVENTS})
    return pooled


# ---------------------------------------------------------------------
# 5. 평가함수 — 이번 이닝 기여 - 남은 이닝의 체력 기회비용
# ---------------------------------------------------------------------
class Evaluator:
    """모든 점수는 '기대 득점(런)' 단위다. 높을수록 좋다."""

    def __init__(self, bat, pit, opp_mult, pooled, inning):
        self.bat, self.pit = bat, pit
        # opp_mult : 상대 투수 체력배수의 P_j 가중 멱평균 (1명 기준이 아니다)
        # pooled   : {우리 타자 pCode: (W, ebar)} — 상대 투수 전원을 미리 접어둔 맞대결
        self.opp_mult = opp_mult
        self.pooled = pooled
        # 남은 이닝이 많을수록 지금 체력을 태우는 게 비싸다. 9회엔 0 -> 전부 태운다.
        self.horizon = max(0, LAST_INNING - inning)
        # 앞으로 그 선수를 더 쓰게 될 이닝 수의 기댓값 = 기회비용의 크기
        self.future_innings = (self.horizon ** HORIZON_EXPONENT) * REUSE_FRACTION
        # 배치와 무관한 값은 미리 캐싱해 둔다 (수천 번 호출되는 경로)
        self._rate_cache = {}
        self._value_cache = {}
        self._slot_cache = {}
        self._off_cache = {}
        self._cost_cache = {}

    # -- 타격 -------------------------------------------------------
    def _rate_vs_opp(self, pcode):
        cached = self._rate_cache.get(pcode)
        if cached is None:
            cached = _blend_pooled(self.bat[pcode]["rate"], self.pooled.get(pcode))
            self._rate_cache[pcode] = cached
        return cached

    def _fresh_value(self, pcode):
        """체력이 온전할 때 그 선수의 타석당 득점가치 (기회비용 계산의 기준)."""
        cached = self._value_cache.get(pcode)
        if cached is None:
            cached = _runs_per_pa(self._rate_vs_opp(pcode), 1.0, 1.0)
            self._value_cache[pcode] = cached
        return cached

    def offense_runs(self, pcode, exp_pa, extra_swings):
        """이번 이닝 그 선수의 기대 득점 기여.
        체력은 이닝 중간 시점(소모의 절반이 진행된 시점)으로 평가한다.
        타순 탐색에서만 2만 번 넘게 불리는데 인자 조합은 (9명 x 9타순)뿐이라 캐싱한다."""
        key = (pcode, exp_pa, extra_swings)
        cached = self._off_cache.get(key)
        if cached is None:
            mult = bat_mult(self.bat[pcode], extra_swings * 0.5)
            cached = exp_pa * _runs_per_pa(self._rate_vs_opp(pcode), mult, self.opp_mult)
            self._off_cache[key] = cached
        return cached

    # -- 수비(실책 실점) --------------------------------------------
    def defense_runs_allowed(self, pcode, slot, extra_swings):
        want = SLOT_POS[slot]
        b = self.bat[pcode]
        mult = bat_mult(b, extra_swings * 0.5)
        p_err = BASE_ERROR_RATE[want] * MEAN_ERROR_MULT
        if b["pos"] != want:
            p_err *= MISMATCH_ERROR_MULT             # 포지션 불일치 = 실책확률 +50%
        p_err *= (1.0 + (1.0 - mult))                # 지칠수록 실책이 는다 (엔진과 동일)
        p_err = max(0.001, min(p_err, 0.35))
        chances = BALLS_IN_PLAY_PER_INNING * CHANCE_SHARE[slot]
        return chances * p_err * ERROR_RUN_COST

    # -- 체력 기회비용 ----------------------------------------------
    def stamina_cost(self, pcode, extra_swings):
        """이번 이닝의 소모 때문에 '남은 이닝'에서 잃게 될 기대 득점.
        절벽 구간에서 Δ배수가 가장 커지므로, 탈진 직전인 선수를 수비에 넣으려 하면
        여기서 큰 페널티가 붙는다 — health_pct를 보고 반응하는 것보다 한 박자 빠르다."""
        if self.future_innings <= 0.0 or extra_swings <= 0.0:
            return 0.0
        key = (pcode, extra_swings)
        cached = self._cost_cache.get(key)
        if cached is not None:
            return cached
        b = self.bat[pcode]
        drop = bat_mult(b, 0.0) - bat_mult(b, extra_swings)
        if drop <= 0.0:
            self._cost_cache[key] = 0.0
            return 0.0
        # 그 선수의 '한 이닝치 타격 가치' × 앞으로 쓸 이닝 수 × 깎인 배수
        per_inning_value = abs(self._fresh_value(pcode)) * EXP_PA_PER_INNING / 9.0
        cost = FUTURE_COST_W * self.future_innings * drop * per_inning_value
        self._cost_cache[key] = cost
        return cost

    def overuse_penalty(self, pcode, extra_swings):
        """이미 지친 선수를 또 내보내는 것 자체에 붙는 레벨 페널티 (OVERUSE_W 주석 참고).
        stamina_cost는 '이번에 깎이는 양'에 비례해서 바닥에 붙은 선수에겐 0이 된다.
        이 항은 '얼마나 낮은 상태인가'에 비례하므로 바닥에서도 사라지지 않는다."""
        if OVERUSE_W <= 0.0:
            return 0.0
        return OVERUSE_W * (1.0 - bat_mult(self.bat[pcode], extra_swings * 0.5))

    # -- 슬롯 하나의 순가치 -----------------------------------------
    def slot_value(self, pcode, slot, exp_pa):
        key = (pcode, slot, round(exp_pa, 3))
        cached = self._slot_cache.get(key)
        if cached is not None:
            return cached
        field_swings = 0.0 if SLOT_POS[slot] == "DH" else FIELD_SWINGS_PER_INNING
        total_swings = exp_pa * SWINGS_PER_PA + field_swings
        value = (self.offense_runs(pcode, exp_pa, total_swings)
                 - self.stamina_cost(pcode, total_swings)
                 - self.overuse_penalty(pcode, total_swings))
        if SLOT_POS[slot] != "DH":
            value -= self.defense_runs_allowed(pcode, slot, total_swings)
        self._slot_cache[key] = value
        return value

    # -- 투수 -------------------------------------------------------
    def pitcher_value(self, pcode):
        """이번 이닝의 실점 억제력을 투구수 누적에 따라 3구간으로 적분한다.
        목표 투구수가 낮은 불펜은 한 이닝을 못 버티고 무너지는 게 그대로 드러난다."""
        p = self.pit[pcode]
        inning_pitches = EXP_PA_PER_INNING * PITCHES_PER_PA
        runs = 0.0
        for frac in (1.0 / 6.0, 0.5, 5.0 / 6.0):       # 이닝의 1/6, 1/2, 5/6 지점
            m = pit_mult(p, inning_pitches * frac)
            runs += _runs_per_pa(p["rate"], 1.0, m) * (EXP_PA_PER_INNING / 3.0)
        value = -runs                                   # 실점은 음수 가치
        # 투수 체력도 남은 이닝의 자산이다 (혹사한 에이스를 계속 올리지 않게)
        if self.future_innings > 0.0:
            drop = pit_mult(p, 0.0) - pit_mult(p, inning_pitches)
            if drop > 0.0:
                strength = abs(_runs_per_pa(p["rate"], 1.0, 1.0)) * EXP_PA_PER_INNING
                value -= FUTURE_COST_W * self.future_innings * drop * strength
        return value


# ---------------------------------------------------------------------
# 6. Tabu Search
# ---------------------------------------------------------------------
def _tabu_search(initial, score_fn, neighbor_fn, iters, rng, deadline):
    """공통 Tabu 탐색기.
    - 이동 속성을 tabu에 기록해 역이동(왔던 자리로 되돌아가기)을 일정 기간 금지
    - 열망 기준: 타부라도 역대 최고해를 넘으면 허용
    - 정체가 길어지면 최고해를 흔들어(perturb) 다른 골짜기로 옮긴다
    """
    cur = list(initial)
    cur_val = score_fn(cur)
    best, best_val = list(cur), cur_val
    tabu = {}
    stagnant = 0

    for it in range(iters):
        if time.perf_counter() > deadline:          # 비상 탈출 (정상 상황에선 걸리지 않는다)
            break
        cand, cand_val, cand_moves = None, float("-inf"), None
        for _ in range(NEIGHBORS_PER_ITER):
            nxt, moves = neighbor_fn(cur, rng)
            if nxt is None:
                continue
            val = score_fn(nxt)
            is_tabu = any(tabu.get(m, -1) > it for m in moves)
            if is_tabu and val <= best_val:          # 열망 기준 미달이면 금지
                continue
            if val > cand_val:
                cand, cand_val, cand_moves = nxt, val, moves
        if cand is None:
            continue
        cur, cur_val = cand, cand_val
        for m in cand_moves:
            tabu[m] = it + TABU_TENURE
        if cand_val > best_val + 1e-12:
            best, best_val = list(cur), cand_val
            stagnant = 0
        else:
            stagnant += 1
            if stagnant >= STAGNATION_LIMIT:         # 다변화: 최고해에서 다시 출발하되 섞는다
                cur = list(best)
                for _ in range(3):
                    i, j = rng.sample(range(len(cur)), 2)
                    cur[i], cur[j] = cur[j], cur[i]
                cur_val = score_fn(cur)
                tabu.clear()
                stagnant = 0
    return best, best_val


def _initial_defense(bat, ev, exp_pa_by_slot):
    """포지션이 맞고 '지금 쓰기 아깝지 않은' 선수부터 채우는 탐욕적 초기해."""
    used = set()
    assign = [None] * 9
    for slot in (8, 7, 0, 1, 2, 3, 4, 5, 6):         # DH·포수를 먼저 확정
        want = SLOT_POS[slot]
        pool = [p for p in bat if p not in used and (want == "DH" or bat[p]["pos"] == want)]
        if not pool:
            pool = [p for p in bat if p not in used]
        if not pool:
            continue
        assign[slot] = max(pool, key=lambda p: ev.slot_value(p, slot, exp_pa_by_slot[slot]))
        used.add(assign[slot])
    return assign


def optimize_defense(bat, ev, rng, deadline):
    """수비 9칸(투수 제외) 배정을 탐색한다.

    타순은 3단계에서 따로 정하고 9명 누구나 어느 타순에도 갈 수 있으므로, 이 단계에서는
    모든 칸에 '평균 기대타석'을 똑같이 준다. 칸마다 다른 기대타석을 주면(예: 타순 가중치를
    슬롯 번호에 그대로 붙이면) DH 칸이 타석이 적은 자리로 잘못 취급돼서, 정작 체력을
    아껴줘야 할 주력 타자를 DH에 앉히지 못하게 된다."""
    exp_pa_by_slot = [EXP_PA_PER_INNING / 9.0] * 9

    def score(assign):
        return sum(ev.slot_value(p, i, exp_pa_by_slot[i]) for i, p in enumerate(assign))

    all_bat = list(bat)

    def neighbor(cur, r):
        if r.random() < 0.5:
            i, j = r.sample(range(9), 2)              # (a) 두 칸 맞바꾸기
            nxt = list(cur)
            nxt[i], nxt[j] = nxt[j], nxt[i]
            return nxt, ((nxt[i], i), (nxt[j], j))
        in_use = set(cur)                             # (b) 벤치 선수로 교체
        pool = [p for p in all_bat if p not in in_use]
        if not pool:
            return None, None
        i = r.randrange(9)
        nxt = list(cur)
        out_player = nxt[i]
        nxt[i] = r.choice(pool)
        return nxt, ((out_player, i),)                # 빠진 선수의 '복귀'를 금지한다

    init = _initial_defense(bat, ev, exp_pa_by_slot)
    if any(p is None for p in init):                  # 후보가 부족한 비정상 상황 방어
        rest = [p for p in all_bat if p not in set(init)]
        init = [p if p is not None else rest.pop() for p in init]
    best, _ = _tabu_search(init, score, neighbor, TS_ITERS_DEFENSE, rng, deadline)
    return best


def optimize_pitcher(pit, ev):
    """후보가 수십 명뿐이라 전수 비교가 무작위 탐색보다 빠르고 정확하다.
    sorted()로 감싸 동점일 때의 선택까지 고정한다(재현성)."""
    return max(sorted(pit), key=ev.pitcher_value)


def optimize_order(defense9, ev, start_index, rng, deadline):
    """확정된 9명의 타순. 슬롯 i의 기대타석은 선두타자 기준 (i - start_index) % 9 번째다."""
    slot_of_defense = {p: i for i, p in enumerate(defense9)}
    exp_pa = [TURN_PA[(i - start_index) % 9] for i in range(9)]

    def swings_for(pcode, i):
        dslot = slot_of_defense[pcode]
        field = 0.0 if SLOT_POS[dslot] == "DH" else FIELD_SWINGS_PER_INNING
        return exp_pa[i] * SWINGS_PER_PA + field

    def score(order):
        total = 0.0
        for i, p in enumerate(order):
            # 수비 슬롯은 그대로 두고 타순만 바꾸므로, 수비 실점은 어느 타순이든 동일하다
            sw = swings_for(p, i)
            total += ev.offense_runs(p, exp_pa[i], sw) - ev.stamina_cost(p, sw)
        return total

    def neighbor(cur, r):
        i, j = r.sample(range(9), 2)
        nxt = list(cur)
        nxt[i], nxt[j] = nxt[j], nxt[i]
        return nxt, ((nxt[i], i), (nxt[j], j))

    # 초기해: 기대타석이 많은 자리에 '체력 반영 후' 잘 치는 선수를 놓는다
    ranked = sorted(defense9,
                    key=lambda p: ev.offense_runs(p, 1.0, FIELD_SWINGS_PER_INNING),
                    reverse=True)
    init = [None] * 9
    for p, slot in zip(ranked, sorted(range(9), key=lambda i: exp_pa[i], reverse=True)):
        init[slot] = p
    best, _ = _tabu_search(init, score, neighbor, TS_ITERS_OFFENSE, rng, deadline)
    return best


# ---------------------------------------------------------------------
# 7. 제출 함수
# ---------------------------------------------------------------------
def decide_lineup(my_team: pd.DataFrame, opponent_team: pd.DataFrame,
                  matchups: pd.DataFrame, context: dict, rng):
    started = time.perf_counter()
    budget = float(_num(context.get("time_budget_sec"), 10.0))
    deadline = started + max(budget - SAFETY_MARGIN_SEC, 1.0)

    bat, pit = build_profiles(my_team)
    inning = int(_num(context.get("inning"), 1))
    start_index = int(_num(context.get("batting_order_start_index"), 0)) % 9

    # 상대 투수는 1명으로 찍지 않고 분포로 다룬다. 비용은 전부 여기(전처리)에서 끝난다 —
    # 탐색 루프는 예전과 똑같이 타자당 사건확률 벡터 1개만 본다.
    opp_prof = opp_pitcher_profiles(opponent_team)
    opp_prob, opp_mult = opp_start_probability(opp_prof, context)
    pooled = build_pooled_matchup(matchups, opp_prob, set(bat))

    ev = Evaluator(bat, pit, opp_mult, pooled, inning)

    # 1) 수비 9칸 배정 (체력 기회비용 포함)
    defense9 = optimize_defense(bat, ev, rng, deadline)
    # 2) 투수는 전수 비교로 정확히
    pitcher = optimize_pitcher(pit, ev)
    # 3) 그 9명의 타순
    offense = optimize_order(defense9, ev, start_index, rng, deadline)

    return {"defense": list(defense9) + [pitcher], "offense": offense}
