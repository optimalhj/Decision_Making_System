P1 작업순서(순열 flow shop, Makespan 최소화)를 지금 IG보다 개선하는 실험을 해줘. CLAUDE.md 의 실험 규칙을 따라.

배경: P1은 하루 ~280~310 jobs라 잔업이 기본이고, makespan 100 줄이면 잔업 1시간(200,000원) 절감. 현재 IG(Ruiz & Stützle)로 NEH 대비 ~2%, SPT 대비 ~15% 짧다.

규칙
- 주 지표: 같은 시간 예산(예: 인스턴스당 30초)에서의 makespan 평균. 채택 마진 0.5%.
- 인스턴스: P1 5개 요일 파일 × n ∈ {260, 280, 300, 320}. 선택용 = mon/tue/wed, 판정용 = thu/fri (판정용은 마지막에 한 번만).
- 기준선: SPT, NEH, 현재 `iterated_greedy` (seed 5개 평균·편차).
- 대조군: NEH + 무작위 재시작/무작위 삽입 탐색에 **더 큰** 시간 예산. 새 방법이 이걸 못 이기면 방법의 기여라고 말하지 않는다.
- `iog/scheduling.makespan` 은 공식 코드와 같아야 한다. 새 코드도 `tests/test_scheduling.py` 의 공식 코드 동등성 테스트를 통과해야 함.
- 결정성: 같은 seed·같은 반복 수면 결과가 같아야 한다 (시간 제한 대신 반복 수로 비교).

해볼 만한 것 (가설로 적고 하나씩)
1. IG 파라미터(d, 온도, 지역탐색 방식) — 민감도 스윕으로 효과 없는 파라미터는 제거
2. Taillard 가속을 쓴 insertion + swap 이웃, VNS
3. 여러 seed 병렬 실행 후 최선 선택 (멀티코어)
4. (선택) 강화학습 기반 스케줄러 — 같은 시간 예산에서 IG를 이겨야 채택

결과는 `experiments/LOG.md` 에 채택·기각 모두 기록하고, 선택용·판정용 개선폭과 과적합 격차를 표로 보여줘. 채택되면 `iog/planning.sequence_for` 에 연결하고 `scripts/weekly_plan.py` 로 한 주 돌려서 생산비 절감액을 보여줘.
