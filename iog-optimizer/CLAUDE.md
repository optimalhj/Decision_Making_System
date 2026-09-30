# IOG 운영 최적화 — Claude Code 작업 지침

팀 **26_DGU_KSH** (동국대 의사결정시스템). IOG(Industrial Optimization Ground) 경진대회에서 가상 음료회사 ㈜K-Bottle을 운영한다.
목표는 **Balance(누적 순이익) 최대화**. 게임: http://play.ioground.kr:5005 · 공지: IOG Notice (Notion)

## 절대 규칙

1. **비밀번호·인증키를 읽거나 입력하거나 저장하지 않는다.** 로그인은 사람이 직접 한다. 자격증명 파일을 찾거나 열지 않는다.
2. **게임에 아무것도 제출하지 않는다.** 이 저장소는 "입력할 값"만 만든다 (`outputs/*.md`, `outputs/*_form.json`). 입력은 사람이 확인 후 직접 한다.
3. 게임 서버 요청은 `scripts/sync_state.py --fetch`, `scripts/verify_ledger.py --fetch` 의 공개 페이지 GET만, 하루 몇 번 이내.
4. `iog/config.py` 에서 `[LEDGER]`·`[CONFIGS]` 로 표시된 값은 장부나 Configs 화면 근거 없이 바꾸지 않는다. `[ASSUMED]` 값은 확인되면 근거와 함께 주석을 고친다.
5. 기존 주석·docstring은 관련 코드를 바꿀 때가 아니면 그대로 둔다. 관련 없는 코드 포맷을 바꾸지 않는다.
6. 작업을 끝내기 전에 `python -m pytest -q` 가 통과해야 한다.

## 게임 핵심 (검증된 것)

| 항목 | 값 |
|---|---|
| 제품 | P1 아메리카노(수요 = 삼성전자 종가, KRX 개장일만) · P2 카푸치노(수요 = BTC 종가 USD, 매일) |
| 판매가 | P1 250 · P2 350 |
| 자재(개당) | P1 = M1 캔 1 + M2 원두 2 (⚠️ 9/28 장부로 확인 예정) · P2 = M3 병 1 + M2 원두 2 |
| 생산비 | 셋업 P1 5,000,000 / P2 4,000,000 + 인건비 H×100,000 + max(H−70,0)×100,000, H = floor(makespan/100) |
| 작업순서 | n jobs × 20공정 순열 flow shop. 그날 n개면 그 요일 파일의 JobID 1~n. 1 job = 1,000개, 하루 최대 500 |
| 처리시간표 | `data/ProcessingTimeTable_P1_P2.zip`: `t_500_20_{요일}` = P1, `t2_500_20_{요일}` = P2 (P2는 요일마다 처리시간이 크게 다름) |
| 자재 | 리드타임 M1 3일, M2 8일(긴급 2일·단가 2배), M3 5일 · 주문비 100만/300만/200만 · 보관비 1/1/2 원/개·일 |
| 패널티 | 결품 250/개 · 완제품 보관 30/개·일 |
| 마감 | 수요 갱신 16:00 · 입력 마감 19:30 · 운영 20:00. 주간계획(다음 주 수요예측+생산)은 토요일까지, 일계획(자재·할인)은 매일 |
| 입력 화면 | 자재는 **오늘 발주분만** 입력 가능. 각 화면 Auto 가 ON 이면 수동 칸이 잠김 → 수동 입력하려면 OFF |

2026-09-27 장부 3팀(우리 $390,000 / AUTO $3,335,280 / 교수팀 $3,022,000)을 시뮬레이터가 원 단위까지 재현한다 (`tests/test_costs_and_simulator.py`).

**아직 가정인 것** (`[ASSUMED]`): 구매비 과금 시점(입고일로 추정), 미충족 수요 이월 없음, 자재 부족 시 P1 먼저 배분, P1 BOM, 할인 탄력성(1.0 가정).
새 장부 줄이 생기면 `scripts/verify_ledger.py` 로 확인하고, 맞지 않으면 시뮬레이터부터 고친다. **시뮬레이터가 현실과 어긋나면 그 위의 모든 개선은 무의미하다.**

## 구조

```
16:00 수요 갱신 → sync_state → ① forecast (예측값 + 지평별 오차)
  → ② 목표 = 예측×(1+k) → ③ lot_size DP (요일별 비용곡선) ◀ ④ scheduling (NEH→IG)
  → ⑤ plan_material_orders (자재 시뮬레이션하며 발주) → ⑥ 할인
⑦ simulator 가 계획을 게임 비용식으로 채점
```

| 파일 | 역할 |
|---|---|
| `iog/config.py` | 모든 파라미터·휴장일·가정 |
| `iog/scheduling.py` | makespan(공식 코드와 동일), SPT, NEH, Iterated Greedy |
| `iog/costs.py` | 생산비, 요일별 비용곡선 (`data/cost_curves.csv`, `scripts/build_cost_curves.py`) |
| `iog/simulator.py` | 하루 단위 시뮬레이터 (장부 형식), 보존 검사 |
| `iog/forecast.py` · `iog/evaluate.py` | 기준선 예측, 토요일 계획 흉내 백테스트, 불일치 비용 채점 |
| `iog/planning.py` | 목표·DP·IG 호출·MRP·생산 전망 |
| `iog/state.py` · `data/state.json` | 팀 상태 (as_of 운영 후 재고, 미입고 발주, 입력된 생산계획) |
| `iog/dashboard.py` | 대시보드 HTML 파싱 |
| `scripts/*.py` | daily_orders · weekly_plan · sync_state · verify_ledger · backtest_forecast · build_cost_curves |
| `prompts/*.md` | 반복 작업용 프롬프트 |

## 매일 / 매주

- 매일 16:00~19:30: `prompts/02_daily_ops.md`
- 토요일 19:30 전: `prompts/03_weekly_plan.md`
- 입력은 사람이 한 뒤 `--record` 로 state.json 에 기록 (게임과 state.json 이 어긋나지 않게)

## 실험 규칙 (개선 주장은 기본값이 '거짓')

1. 주 지표 하나를 먼저 적는다: 예측 = 불일치 비용(원/수요일), 스케줄러 = makespan(같은 시간 예산), 전체 = 시뮬레이터 Balance.
2. 기준선 여러 개(naive·ma·ses·drift / SPT·NEH·IG)를 같이 돌리고, 가장 강한 것을 이겨야 한다.
3. 데이터를 선택용과 판정용으로 나누고, 판정용은 마지막에 한 번만 본다 (예측: 2026-09-01 이후가 판정용).
4. 채택 마진을 미리 정한다 (예측 5%, 스케줄러 0.5%). 도중에 바꾸지 않는다.
5. 채택·기각 모두 `experiments/LOG.md` 에 한 줄씩: 가설 / 근거 / 코드 / 측정값 / 판정 / 기각 사유.
6. 난수는 seed 고정, 재현 명령 한 줄을 같이 남긴다. 같은 입력에 결과가 다르면 기각.
7. 보고할 때 선택용·판정용 개선폭과 그 차이(과적합 격차)를 나란히 적는다.

## 알려진 함정

- 매뉴얼의 5 jobs 예시(SPT 1,395 / 최적 1,362)는 공식 코드로 재현이 안 된다 (1,474 / 1,252). 순서는 같다. 공식 코드 기준으로 계산.
- Configs 화면의 "M1~M3 는 P1/P2 공통" 설명은 틀림 (9/27 P2 생산 때 M1 출고 0).
- 휴장일(P1) 수요예측은 0 으로 입력 → 정확도 계산에서 빠진다. 2026-10-05, 10-09 휴장. R2602C(11/1~12/4)에는 휴장일 없음.
- 라운드마다 초기 자재가 새로 주어지고, 라운드 끝에 남은 자재는 버려진다 → 라운드 막판엔 필요한 만큼만 발주.
- IG 곡선: 비용곡선은 NEH(상한)로 만들고, 실제 생산일에만 IG로 순서를 다시 짠다.
