# iog-optimizer

IOG(㈜K-Bottle) 운영 최적화 도구 — 팀 **26_DGU_KSH**. Claude Code에서 돌리는 것을 전제로 만들었다 (`CLAUDE.md`, `prompts/`).

## 빠른 시작

```bash
pip install -r requirements.txt
python -m pytest -q                      # 21개 테스트 (장부 재현·공식 코드 동등성 포함)
python scripts/daily_orders.py           # 오늘 자재 발주안
python scripts/weekly_plan.py            # 다음 주 수요예측 + 생산계획 + 작업순서
```

Claude Code에서는 `claude "$(cat prompts/02_daily_ops.md)"` 처럼 프롬프트를 그대로 넘기면 된다. 목록은 `prompts/00_README.md`.

## 하루·일주일 흐름

| 언제 | 할 일 | 명령 |
|---|---|---|
| 매일 21:00 이후 또는 다음 날 16:00 이후 | 상태 동기화 + 장부 점검 | `sync_state.py --fetch` · `verify_ledger.py --fetch` |
| 매일 16:00~19:30 | 자재 발주안 → 사람이 입력 → 기록 | `daily_orders.py` → 입력 → `daily_orders.py --record` |
| 토요일 19:30 전 | 다음 주 계획 → 사람이 입력 → 기록 | `weekly_plan.py` → 입력 → `weekly_plan.py --record` |

입력할 값은 `outputs/daily_*.md`, `outputs/weekly_*.md` (사람용 표)와 `*_form.json` (화면 칸 이름 → 값)에 나온다.

## 구성

- `iog/` 라이브러리: 설정·달력·처리시간표·스케줄러(SPT/NEH/IG)·생산비·시뮬레이터·예측·계획·상태·대시보드 파서
- `scripts/` 실행 스크립트
- `data/` 처리시간표 zip, 수요 이력(2026-04~), 비용곡선, 팀 상태(state.json)
- `tests/` 장부 재현·공식 코드 동등성·계획·파서 테스트 (+ 2026-09-28 대시보드 HTML 스냅샷)
- `experiments/LOG.md` 실험 로그 (채택·기각 모두)

## 검증된 것과 가정

- 비용식과 시뮬레이터는 2026-09-27 장부 3팀과 원 단위까지 일치.
- 스케줄러 makespan은 공식 `scheduling_lib.py` 와 동일 (`tests/fixtures/official_scheduling_lib.py`).
- 가정(`config.py` 의 `[ASSUMED]`): 구매비는 입고일 과금, 미충족 수요 이월 없음, 자재 부족 시 P1 먼저, P1 BOM(캔 1 + 원두 2), 할인 탄력성.

## 보안

비밀번호·인증키는 이 저장소 어디에도 넣지 않는다 (`.gitignore` 에 흔한 파일명 패턴 포함). 로그인과 게임 입력은 사람이 직접 한다.
