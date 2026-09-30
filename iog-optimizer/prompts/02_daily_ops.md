오늘 IOG 일일 운영 준비를 해줘. CLAUDE.md 절대 규칙을 지켜 (비밀번호 금지, 게임에 제출 금지).

1. 지금 시각 확인. 16:00 전이면 오늘 수요가 아직 갱신 안 됐다고 알려주고 계속 진행. 19:30 이 지났으면 오늘 입력은 이미 늦었다고 먼저 알려줘.
2. 상태 동기화: `python scripts/sync_state.py --fetch --dry-run` → 변화가 합리적이면 `--dry-run` 빼고 실행.
   - 발주 목록은 비로그인으로 안 보인다. 내가 로그인한 브라우저에서 Material 페이지를 저장해 `inbox/` 에 넣어두면 `--html-dir inbox` 로 다시 동기화해.
3. `python scripts/verify_ledger.py --fetch` 로 어제 장부 점검. 불일치나 처음 보는 Action 이 있으면 **다른 작업보다 먼저** 보고하고, 원인이 config 가정이면 근거와 함께 고치자고 제안해 (바로 고치지 말고 물어봐).
4. `python scripts/daily_orders.py` 실행.
5. 결과를 이렇게 정리해줘:
   - 오늘 Material 화면에 넣을 값 4칸 (m1_d_1, m2_n_1, m2_u_1, m3_d_1) — 0이 아닌 것만 강조
   - 앞으로 필요한 발주 일정
   - 생산이 모자라는 날과 이유
   - 재고 추이에서 눈에 띄는 것 (자재가 안전재고 아래로 가는 날, 완제품이 쌓이는 날)
   - 입력 전 체크: 자재 Auto OFF 인지, Apply 후 목록에 MANUAL 행이 생겼는지
6. 내가 "입력했어"라고 하면 `python scripts/daily_orders.py --record` 로 기록하고, 다시 `sync_state` 로 맞는지 확인.

생산량·발주 기준(k, cover_days, 안전재고)을 바꾸고 싶어지면 바로 바꾸지 말고, 근거(백테스트 또는 시뮬레이터 비교)를 먼저 보여줘.
