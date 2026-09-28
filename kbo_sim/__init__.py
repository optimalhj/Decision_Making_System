"""
kbo_sim — "너, 내 구단주가 돼라" KBO 야구 시뮬레이션 엔진
메타휴리스틱(Tabu Search / PSO / GA) 강의용 학생 프로젝트 게임 엔진.

패키지 구성
-----------
data_pipeline  : 4개 원천 CSV(teams/pitchers/batters/matchup) 로드 및 정제, 리그 평균 계산
models         : Player/Team/PlayerGameState 등 데이터 구조
traits         : 실제 KBO 기록에 없는 성향(도루/번트 적극성 등)을 결정론적으로 합성 (문서화된 가정, 실제 기록 아님)
fatigue        : 시그모이드 체력 저하 모델
rng            : 학생 코드용 RNG와 엔진용 RNG를 물리적으로 분리하는 관리자
probability    : log5 기반 매치업 확률 산출 (실제 매치업 기록 있으면 그것을 우선 사용)
atbat          : 타석 결과 확정 → 투구 시퀀스 역생성
defense        : 타구 처리 및 수비 실책 판정
student_api    : 학생 제출 함수 인터페이스 정의 + 격리 실행기(하드 타임아웃)
student_check  : 제출 코드 검증 (AST 정적검사 + 실제 1회 호출 스모크 테스트)
game           : 9이닝 단일 경기 엔진 (배치 실행 run() / 하프이닝 스텝 실행 play_next_half())
match          : 3연전(홈/원정 교대 + 랜덤 배정) 배치 오케스트레이션 (CLI 채점용)
live_session   : 3연전 라이브 진행 관리 (하프이닝 단위)
server         : 라이브 중계 HTTP 서버 (브라우저에서 이닝을 눌러 진행)
broadcast_export: 문자중계 뷰어(HTML)가 읽는 JSON 이벤트 로그로 변환
cli            : 배치 실행 진입점 / bootstrap: 데이터 초기 설정

실행 방법
---------
라이브 중계 : python -m kbo_sim.server      -> 브라우저에서 http://127.0.0.1:8000
일괄 채점   : python -m kbo_sim.cli --a-name ... --b-name ...
밸런스 점검 : python tools/calibrate.py
"""

__version__ = "0.1.0"
