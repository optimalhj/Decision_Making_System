이 저장소(IOG 운영 최적화) 처음 세팅과 점검을 해줘. CLAUDE.md 를 먼저 읽고 절대 규칙을 지켜.

1. `python --version` 확인 (3.10 이상). `pip install -r requirements.txt` 로 설치. numba 설치가 실패하면 알려주고 계속 진행 (느려질 뿐).
2. `python -m pytest -q` 실행. 실패하면 원인을 찾아 고치되, 테스트 기대값(장부 재현 값)은 바꾸지 마. 기대값이 틀렸다고 판단되면 근거를 보여주고 나한테 물어봐.
3. `data/cost_curves.csv` 가 없으면 `python scripts/build_cost_curves.py` 실행하고, 출력된 "개당 생산비" 표를 보여줘.
4. `python scripts/verify_ledger.py --fetch` 로 게임 장부가 우리 비용식과 맞는지 점검. 처음 보는 Action(예: 구매비 줄)이 있으면 무엇인지, config.py 의 어떤 가정과 관련 있는지 정리해줘.
5. `python scripts/sync_state.py --fetch --dry-run` 결과를 보여주고, 바뀌는 값이 합리적이면 `--dry-run` 없이 다시 실행.

끝나면 설치 결과, 테스트 결과, 장부 점검 결과, state.json 의 as_of·재고를 짧게 정리해줘.
