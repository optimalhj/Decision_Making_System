# 프롬프트 모음 (Claude Code용)

저장소 폴더에서 `claude` 를 켜고 아래 파일 내용을 붙여넣거나, 한 번에 실행:

```bash
claude "$(cat prompts/02_daily_ops.md)"
```

| 파일 | 언제 | 하는 일 |
|---|---|---|
| `01_setup_and_verify.md` | 처음 한 번 | 설치, 테스트, 비용곡선, 장부 점검 |
| `02_daily_ops.md` | 매일 16:00~19:30 | 상태 동기화 → 장부 점검 → 오늘 자재 발주안 |
| `03_weekly_plan.md` | 토요일 19:30 전 | 다음 주 수요예측 + 생산계획 + 작업순서 |
| `04_scheduler_improvement.md` | 여유 있을 때 | IG보다 나은 스케줄러 실험 (P1 생산비 절감) |
| `05_forecast_experiments.md` | 여유 있을 때 | 주가 예측 모델 실험 (naive 이기기) |
| `06_round_prep_R2602C.md` | 10/16~10/25 | 본선 라운드 준비 |

공통: 게임 입력은 사람이 한다. Claude Code는 입력할 값만 만든다 (CLAUDE.md 절대 규칙).
