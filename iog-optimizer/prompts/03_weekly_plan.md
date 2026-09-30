다음 주 IOG 주간계획(수요예측 + 생산계획 + 작업순서)을 만들어줘. 마감은 토요일 19:30. CLAUDE.md 절대 규칙을 지켜.

1. `python scripts/sync_state.py --fetch` 로 최신 상태 반영 (토요일 16:00 이후면 토요일 수요까지 들어옴).
2. `python scripts/weekly_plan.py --ig-seconds 30` 실행 (시간 여유 없으면 10).
3. 결과를 검토해서 이상한 점을 먼저 짚어줘:
   - P1 이 토·일·휴장일에 잡혀 있지 않은지 (10/5, 10/9 휴장)
   - 날짜별 생산량 ÷ 예측이 1.0~1.1 근처인지 (PFR). 금요일 P2 몰아생산은 예외지만, 주 합계로는 1.0~1.1
   - 자재 점검에서 생산이 모자라는 날이 있는지 → 있으면 발주로 막을 수 있는지, 아니면 생산계획을 줄여야 하는지
   - IG 가 SPT 보다 나쁜 날이 없는지 (있으면 버그)
4. 입력용으로 정리해줘:
   - Demand forecasting 화면: 날짜별 P1·P2 예측값 (휴장일 P1 = 0). Auto forecasting 은 OFF
   - Production 화면: 날짜·제품별 'Job sequence' 문자열 — `outputs/weekly_<날짜>_form.json` 의 `seq_*` 값. Auto production scheduling 은 OFF
   - 생산비 요약 (IG vs SPT 절감액)
5. 내가 "입력했어"라고 하면 같은 인자에 `--record` 를 붙여 다시 실행해 state.json 에 기록하고 `sync_state --fetch` 로 게임의 Planned 행과 job 수가 같은지 확인.

k(안전재고)나 예측 방법을 바꾸려면 `prompts/05_forecast_experiments.md` 절차로 근거를 먼저 만들어.
