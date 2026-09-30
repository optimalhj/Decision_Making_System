"""IOG 게임 파라미터.

출처 표기
- [CONFIGS]  http://play.ioground.kr:5005/show_configs (2026-09-28 확인)
- [LEDGER]   팀 장부로 역산해서 원 단위까지 맞춘 값 (2026-09-27 장부 3팀)
- [FORM]     로그인 후 입력 화면에서 확인한 규칙 (2026-09-28)
- [NOTICE]   Notion 공지 / IOG News
- [ASSUMED]  아직 확인 못 한 가정. 확인되면 주석을 고칠 것
"""
from __future__ import annotations

import datetime as dt

# ---------------------------------------------------------------- 제품·판매
PRODUCTS = ("P1", "P2")
PRICE = {"P1": 250, "P2": 350}                      # [CONFIGS] 원/개
DEMAND_CODE = {"P1": "005930", "P2": "BTC"}         # [CONFIGS] 삼성전자 종가(원), 비트코인 종가(USD)
DEMAND_ON_WEEKEND = {"P1": False, "P2": True}       # [CONFIGS] P1은 KRX 개장일만 수요
FG_HOLD_COST = 30                                   # [CONFIGS][LEDGER] 완제품 보관비 원/개·일 (그날 판매 후 남은 재고)
STOCKOUT_COST = 250                                 # [CONFIGS][LEDGER] 결품 원/개 (그날 못 채운 수요)
BACKLOG_UNMET_DEMAND = False                        # [ASSUMED] 미충족 수요는 다음 날로 이월되지 않는다고 가정

# ---------------------------------------------------------------- 생산
SETUP_COST = {"P1": 5_000_000, "P2": 4_000_000}     # [CONFIGS][LEDGER] 그날 생산하면 발생
LABOR_COST = 100_000                                # [CONFIGS] 원/시간
MAKESPAN_CAPA = 7_000                               # [CONFIGS] 정규시간 = 7,000 makespan 단위 = 70시간
EXT_PROD_COST = 1.0                                 # [CONFIGS][LEDGER] 초과시간 할증률 (정규의 +100%)
MAKESPAN_PER_HOUR = 100                             # [LEDGER] H = floor(makespan / 100)
JOB_SIZE = 1_000                                    # [CONFIGS] 1 job = 1,000개
MAX_JOBS = 500                                      # [CONFIGS][FORM] 하루·제품당 최대 job 수
N_MACHINES = 20                                     # [CONFIGS]
P1_WEEKDAY_PRODUCTION_ONLY = True                   # [FORM] P1은 토·일 입력칸이 0으로 막혀 있음 (공휴일 평일은 입력 가능)
PRODUCTION_ORDER_WHEN_SHORT = ("P1", "P2")          # [ASSUMED] 자재가 모자랄 때 먼저 자재를 가져가는 제품 순서

# 처리시간표: data/ProcessingTimeTable_P1_P2.zip
#   t_500_20_{mon..sun}.csv  -> P1   [LEDGER로 간접 확인: 매뉴얼 예시 5 jobs ID순 1,649 재현]
#   t2_500_20_{mon..sun}.csv -> P2   [LEDGER: 9/27 P2 76 jobs makespan 11,588이 t2_sun과 일치]
PTIME_PREFIX = {"P1": "t_500_20_", "P2": "t2_500_20_"}
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ---------------------------------------------------------------- 자재
BOM = {                                             # 제품 1개당 자재 소요량
    "P1": {"M1": 1, "M2": 2},                       # [ASSUMED→9/28 운영 후 장부로 확인할 것] 캔 + 원두 2
    "P2": {"M3": 1, "M2": 2},                       # [LEDGER] 9/27 P2 76,000개 → M2 152,000·M3 76,000 출고, M1 0
}
MATERIALS = ("M1", "M2", "M3")
LEAD_TIME = {("M1", "normal"): 3, ("M2", "normal"): 8, ("M2", "urgent"): 2, ("M3", "normal"): 5}  # [CONFIGS] 일 (주말·공휴일 포함)
UNIT_COST = {("M1", "normal"): 30, ("M2", "normal"): 25, ("M2", "urgent"): 50, ("M3", "normal"): 50}  # [CONFIGS]
ORDER_COST = {"M1": 1_000_000, "M2": 3_000_000, "M3": 2_000_000}   # [CONFIGS] 회당 (주문 1건당이라고 가정)
MAT_HOLD_COST = {"M1": 1, "M2": 1, "M3": 2}                        # [CONFIGS][LEDGER] 원/개·일 (그날 운영 후 재고)
INIT_MATERIAL = {"M1": 300_000, "M2": 3_000_000, "M3": 500_000}    # [CONFIGS] 라운드마다 새로 주어짐 [NOTICE]
PURCHASE_COST_AT = "arrival"                        # [ASSUMED] 9/27 자동발주가 그날 장부에 안 잡혀서 입고일 과금으로 추정. 9/30 장부로 확인
AUTO_REORDER = {"M1": (300_000, 300_000), "M2": (2_000_000, 2_000_000), "M3": (500_000, 500_000)}  # [CONFIGS] (재주문점, 주문량)

# ---------------------------------------------------------------- 일정·마감
DEMAND_UPDATE_TIME = "16:00"    # [NOTICE]
INPUT_DEADLINE = "19:30"        # [NOTICE] 주간계획(토)·일계획(매일) 모두
OPERATION_TIME = "20:00"        # [NOTICE]

ROUNDS = {                       # [CONFIGS 홈 화면 Round Selection]
    "R2602B": (dt.date(2026, 9, 27), dt.date(2026, 10, 16)),
    "R2602C": (dt.date(2026, 11, 1), dt.date(2026, 12, 4)),
}

# KRX 휴장일 (평일만). ✓ = 수요 이력 공백 또는 뉴스로 확인
KRX_HOLIDAYS = {
    dt.date(2026, 1, 1),                          # 신정 (미확인)
    dt.date(2026, 2, 16), dt.date(2026, 2, 17), dt.date(2026, 2, 18),  # 설 (미확인)
    dt.date(2026, 3, 2),                          # 삼일절 대체 (미확인)
    dt.date(2026, 5, 1),                          # 근로자의 날 ✓
    dt.date(2026, 5, 5),                          # 어린이날 ✓
    dt.date(2026, 5, 25),                         # 부처님오신날 대체 ✓
    dt.date(2026, 6, 3),                          # 지방선거 ✓
    dt.date(2026, 7, 17),                         # 제헌절 ✓
    dt.date(2026, 8, 17),                         # 광복절 대체 ✓
    dt.date(2026, 9, 24), dt.date(2026, 9, 25),   # 추석 ✓
    dt.date(2026, 10, 5),                         # 개천절 대체 ✓
    dt.date(2026, 10, 9),                         # 한글날 ✓
    dt.date(2026, 12, 25),                        # 성탄절 (미확인)
    dt.date(2026, 12, 31),                        # 연말 휴장 (미확인)
}

# ---------------------------------------------------------------- 계획 기본값 (백테스트로 다시 정할 것)
SAFETY_FACTOR = {"P1": 0.06, "P2": 0.02}   # 생산량 = 예측 × (1 + k). 2026-04~09 naive 백테스트 최적값
MATERIAL_COVER_DAYS = {"M1": 3, "M2": 4, "M3": 5}   # 한 번 발주로 덮을 생산일 수 (단순 EOQ 근사)
MATERIAL_SAFETY_UNITS = {"M1": 20_000, "M2": 60_000, "M3": 15_000}
