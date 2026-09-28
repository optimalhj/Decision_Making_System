"""
student_api.py
---------------
학생이 제출하는 "출전선수 결정 함수"의 계약(interface)과, 그 함수를 안전하게(강제 타임아웃 포함)
격리 실행하는 러너를 정의한다.

============================================================
학생이 구현해야 하는 함수 시그니처 (정확히 이 이름/인자 순서를 지켜야 함)
============================================================

    def decide_lineup(my_team, opponent_team, matchups, context, rng):
        ...
        return {"defense": [pCode]*10, "offense": [pCode]*9}

이 함수는 **한 번의 호출로 이번 이닝의 공격 타순과 수비 배치를 모두** 정해서 반환합니다.

인자
----
my_team : pandas.DataFrame
    우리 팀 전체 선수(타자+투수). 각 행 = 선수 1명. 실제 KBO 2025시즌 기록 컬럼들
    (AVG, OPS, ERA, WHIP 등) + 이번 경기 진행 중 상태 컬럼이 합쳐져 있습니다:
      - pCode (진짜 키), displayId(표시용, "팀명+등번호+이름"), name, position, role('타자'/'투수')
      - health_pct : 0~100, 현재 체력 (100=쌩쌩, 0=탈진)
      - pitch_count / pitch_target : 투수만 유효 (그 외 NaN)
      - swing_count / swing_target : 타자(수비수 포함)만 유효 (그 외 NaN)
    체력은 절대 회복되지 않고 경기 내내 누적됩니다.

opponent_team : pandas.DataFrame
    상대 팀 전체 선수 데이터. my_team과 동일한 스키마 (상대 팀 선수들의 체력도 전부 포함됩니다).

matchups : pandas.DataFrame
    투수-타자 맞대결 통산 기록 (pitcherPCode, hitterPCode, AVG, PA, H, HR, SO ... 등).
    "우리 전체 투수 x 상대 전체 타자" 조합과 "상대 전체 투수 x 우리 전체 타자" 조합이
    담겨 있습니다. 기록이 없는 조합은 그냥 이 표에 나타나지 않습니다 — 즉 없으면 개인 스탯
    기반으로 시뮬레이션 엔진이 알아서 처리하니, 학생 알고리즘은 이 표에 있는 것만 참고하면 됩니다.
    **1회부터** 상대 투수 전원(선발+불펜)이 조회 대상에 들어갑니다. 다만 '대상'과 '실제 행'은
    다릅니다 — 통산 맞대결이 한 번도 없는 조합은 행 자체가 없어서, 실제로 행이 붙는 상대 투수는
    로스터의 평균 76%(대진에 따라 59~90%)입니다. 즉 등판 후보 중 일부는 맞대결 정보가 아예
    없으니, 그런 투수는 개인 시즌기록으로만 평가해야 합니다.
    이번 이닝에 누가 등판할지는 알 수 없으므로, 특정 투수 1명만 보지 말고 등판 후보 전체에 대한
    기댓값(예: PA 가중평균, 체력·불펜 잔여로 가중)으로 다루세요.
    조회용 dict를 만들 때는 반드시 (pitcherPCode, hitterPCode) **두 개를 함께 키로** 쓰세요.
    한 타자에 상대 투수 여러 명(중앙값 10명, 최대 24명)의 행이 붙으므로 타자 pCode 단독 키로
    만들면 엉뚱한 투수의 기록을 집어옵니다.

호출 시점 (중요)
--------------
이 함수는 **이닝이 시작될 때 두 팀에 대해 한 번씩, 총 2번** 호출됩니다.
초/말 공수교대 때는 다시 호출되지 않고, 이닝 시작에 확정된 명단이 그 이닝 내내 쓰입니다.

    N회 시작 → ① 홈팀 decide_lineup  ② 원정팀 decide_lineup
             → N회초 진행 (원정팀 공격 / 홈팀 수비)
             → N회말 진행 (홈팀 공격 / 원정팀 수비)
             → (N+1)회 시작 → ...

양 팀 명단을 이닝 시작에 동시에 정하므로, **이번 이닝에 상대할 투수/포수는 아직 알 수 없습니다.**
context의 opp_pitcher_pcode / opp_catcher_pcode는 "상대의 직전 이닝 수비" 기준이며 1회에는 None입니다.
이 값은 맞대결 표의 범위를 고르는 열쇠가 아니라 **직전 이닝 투수가 누구였는지 알려주는 힌트**일
뿐입니다 (matchups에는 상대 투수 전원이 이미 들어 있습니다). 그 투수가 이번 이닝에도 그대로
올라온다고 단정하기보다, 등판 후보 전원에 대한 기댓값으로 평가하는 편이 낫습니다.

context : dict
    {
      "inning": int,                       # 현재 이닝 (기본 1~9, 연장 없음)
      "half": "top" | "bottom",             # 우리 팀이 공격하는 하프 (원정=top, 홈=bottom)
      "my_score": int, "opponent_score": int,
      "outs": 0,                            # 하프이닝 시작 시점이므로 항상 0
      "batting_order_start_index": int,     # 0~8. 반환할 offense 9명 중 몇 번 인덱스부터 타순이 시작되는지
      "my_prev_offense": [pCode,...] | None,   # 우리 팀 직전 이닝 타순
      "my_prev_defense": [pCode,...] | None,   # 우리 팀 직전 이닝 수비 배치
      "opp_prev_offense": [pCode,...] | None,  # 상대 팀 직전 이닝 타순
      "opp_prev_defense": [pCode,...] | None,  # 상대 팀 직전 이닝 수비 배치
      "opp_pitcher_pcode": int | None,      # 상대 직전 이닝 투수 (opp_prev_defense[9]). 1회엔 None
                                            #   (matchups는 상대 투수 전원을 담으므로 이건 힌트일 뿐)
      "opp_catcher_pcode": int | None,      # 상대 직전 이닝 포수 (opp_prev_defense[7]). 1회엔 None
      "time_budget_sec": 10.0,              # 이 함수에 주어진 제한시간(초). 초과 시 폴백 처리됨
    }

rng : random.Random
    이 알고리즘 호출 전용 난수 생성기. **반드시 이 rng만 사용하세요.** 전역 random 모듈이나
    random.seed()를 호출해도 이 게임의 시뮬레이션 결과(안타/삼진 등)에는 절대 영향을 주지
    않습니다 (엔진 RNG와 완전히 분리되어 있음). 다만 여러분의 알고리즘 자체가 재현 가능하게
    동작하길 원한다면 이 rng를 사용하는 게 유일한 방법입니다.

반환값
------
{"defense": [...10개...], "offense": [...9개...]} 형태의 dict.
- defense : pCode 정수 10개. 순서 고정
      [내야수, 내야수, 내야수, 내야수, 외야수, 외야수, 외야수, 포수, DH, 투수]
      (앞 4개 내야수, 다음 3개 외야수, 8번째 포수, 9번째 DH, 마지막(10번째)이 투수)
- offense : pCode 정수 9개. **투수 제외**, 타순 순서대로.
      반드시 defense의 앞 9명(=투수 제외 야수 9명, DH 포함)을 재배열한 것이어야 합니다.

규칙/제약
--------
- 투수 슬롯(defense의 마지막)에는 반드시 my_team에서 position == '투수'인 선수만 가능합니다.
  (요구사항: "투수는 항상 투수임. 외야수를 투수에 배치 불가.")
- 그 외 슬롯(내야수/외야수/포수/DH)은 투수가 아닌 임의의 우리 팀 타자를 넣을 수 있지만,
  슬롯이 요구하는 포지션과 그 선수의 실제 고정 포지션이 다르면 **그 선수가 관여하는 수비
  플레이의 실책 확률이 50% 증가**합니다. DH 슬롯은 수비를 보지 않으므로 포지션 불일치 페널티가
  적용되지 않습니다.
- defense/offense 각각 같은 사람을 두 번 넣을 수 없습니다.
- offense는 defense의 앞 9명과 정확히 같은 9명이어야 합니다 (순서만 다름). DH를 포함한 그 9명이
  공수에 함께 참여하며 투수는 타격하지 않습니다. 교체는 다음 이닝 시작에만 가능합니다.
- 이닝 도중 교체는 없습니다 — 이닝 시작 시 그 이닝에 쓸 전체 명단을 제출하는 방식이며,
  이닝 중간이나 공수교대 때 개별 선수를 바꾸는 절차는 존재하지 않습니다.
- 함수는 제한시간(기본 10초) 안에 반환해야 합니다. 초과하거나 예외가 발생하거나 반환값이
  규칙을 위반하면, 직전 이닝에 제출했던 유효한 명단으로 자동 대체되고(폴백), 그마저 없으면
  (예: 1이닝) 기본 수비 명단과 그 앞 9명의 타순으로 출전 처리됩니다.

권장 사항 (실행시간 관련, 사전 벤치마크 결과 참고)
------------------------------------------------
- population/generation, tabu iteration 등은 pop<=100, gens<=200, tabu iters<=300 수준을
  권장합니다. 이 정도면 대부분의 순수 파이썬 구현이 10초 제한에 여유 있게 들어옵니다.
- 적합도 함수 안에서 매번 DataFrame을 필터링(df[df['team']==x] 등)하지 말고, 함수 시작 시
  한 번 dict/array로 변환해 캐싱해서 쓰세요. (반복 필터링은 수천 번 호출 시 수 초의 오버헤드를
  추가로 만듭니다.)
- 적합도 평가 내부에서 몬테카를로 반복(수십 회 이상)을 도는 습관은 매우 위험합니다 — population과
  결합되면 쉽게 10초를 넘깁니다.
"""
from __future__ import annotations

import importlib.util
import multiprocessing as mp
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from .data_pipeline import LeagueData
from .models import GameRosterState, Team

REQUIRED_FUNC_NAME = "decide_lineup"
DEFAULT_TIMEOUT_SEC = 10.0


class StudentAlgoError(Exception):
    pass


# ---------------------------------------------------------------------------
# 학생 팀 데이터(pd.DataFrame) 구성
# ---------------------------------------------------------------------------

STATUS_COLS_BATTER = ["health_pct", "swing_count", "swing_target"]
STATUS_COLS_PITCHER = ["health_pct", "pitch_count", "pitch_target"]


def team_status_dataframe(league: LeagueData, team: Team, roster_state: GameRosterState) -> pd.DataFrame:
    rows = []
    for pcode in team.batter_pcodes:
        rec = dict(league.batter(pcode))
        rt = roster_state.get(pcode)
        rec["role"] = "타자"
        rec["health_pct"] = rt.health_pct()
        rec["swing_count"] = rt.swing_count
        rec["swing_target"] = rt.swing_target
        rec["pitch_count"] = None
        rec["pitch_target"] = None
        rows.append(rec)
    for pcode in team.pitcher_pcodes:
        rec = dict(league.pitcher(pcode))
        rt = roster_state.get(pcode)
        rec["role"] = "투수"
        rec["health_pct"] = rt.health_pct()
        rec["pitch_count"] = rt.pitch_count
        rec["pitch_target"] = rt.pitch_target
        rec["swing_count"] = None
        rec["swing_target"] = None
        rows.append(rec)
    df = pd.DataFrame(rows)
    # event_rate/event_counts 같은 내부용 dict 컬럼은 학생에게 굳이 노출할 필요 없어 제거
    for c in ("event_rate", "event_counts"):
        if c in df.columns:
            df = df.drop(columns=[c])
    return df


def matchup_dataframe(league: LeagueData, pitcher_pcodes: List[int], hitter_pcodes: List[int]) -> pd.DataFrame:
    # 반드시 matchup_index(dict, CSV 행 순서로 삽입됨)를 순회하고 set은 멤버십 검사에만 쓴다.
    # set을 순회하는 방식("for p in p_set: for h in h_set")으로 바꾸면 행 순서가 달라지고,
    # 학생 알고리즘의 동점 처리를 통해 같은 seed의 경기 결과까지 바뀐다.
    #
    # 주의 — 이건 '프로세스마다 달라지는' 문제가 아니다. 키가 (int, int)라 PYTHONHASHSEED의
    # 영향을 받지 않아(정수는 해시 랜덤화 대상이 아님) set 순회도 프로세스 간에는 재현된다.
    # 실측: 네 가지 PYTHONHASHSEED에서 set 순회 결과 해시가 전부 동일했고, 다만 dict 순서와는
    # 달랐다. 따라서 tools/verify.py의 'PYTHONHASHSEED에서도 동일 결과' 검사는 이 변경을
    # 잡아내지 못한다. 과거 결과와 조용히 어긋나는 쪽이라 오히려 더 위험하니 손대지 말 것.
    p_set, h_set = set(int(p) for p in pitcher_pcodes), set(int(h) for h in hitter_pcodes)
    rows = [row for (p, h), row in league.matchup_index.items() if p in p_set and h in h_set]
    if not rows:
        # 실제 경기에서는 1회부터 양 팀 투수진 전원이 들어가 사실상 비지 않는다.
        # 이 폴백은 빈 인자로 부르는 테스트/벤치마크용 경로다 (컬럼 스키마는 실제 데이터와 동일).
        return pd.DataFrame(columns=["pitcherPCode", "hitterPCode", "AVG", "PA", "AB", "H", "2B", "3B", "HR",
                                      "RBI", "BB", "HBP", "SO", "SLG", "OBP", "OPS"])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 격리 실행 (하드 타임아웃)
# ---------------------------------------------------------------------------

def load_student_module(filepath: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    if spec is None or spec.loader is None:
        raise StudentAlgoError(f"파일을 불러올 수 없습니다: {filepath}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, REQUIRED_FUNC_NAME):
        raise StudentAlgoError(f"'{REQUIRED_FUNC_NAME}' 함수가 정의되어 있지 않습니다: {filepath}")
    return mod


def _worker(filepath, module_name, kwargs, result_queue):
    try:
        mod = load_student_module(filepath, module_name)
        fn = getattr(mod, REQUIRED_FUNC_NAME)
        result = fn(**kwargs)
        result_queue.put(("ok", result))
    except Exception as e:  # noqa: BLE001 - 학생 코드는 무슨 예외를 던질지 알 수 없음
        result_queue.put(("error", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}"))


@dataclass
class DecisionOutcome:
    status: str            # 'ok' | 'timeout' | 'error' | 'crash' | 'invalid'
    elapsed: float
    lineups: Optional[dict]   # {"offense": [...9], "defense": [...10]} 형태의 학생 반환값 (원본)
    error: Optional[str]


def run_student_decision(filepath: str, module_name: str, *, my_team: pd.DataFrame,
                          opponent_team: pd.DataFrame, matchups: pd.DataFrame, context: dict,
                          rng, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> DecisionOutcome:
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    q = ctx.Queue()
    kwargs = dict(my_team=my_team, opponent_team=opponent_team,
                  matchups=matchups, context=context, rng=rng)
    p = ctx.Process(target=_worker, args=(filepath, module_name, kwargs, q))
    t0 = time.time()
    p.start()
    p.join(timeout_sec)
    elapsed = time.time() - t0
    if p.is_alive():
        p.terminate()
        p.join(1.0)
        if p.is_alive():
            p.kill()
            p.join()
        return DecisionOutcome("timeout", elapsed, None, f"{timeout_sec:.0f}초 제한시간 초과 (강제 종료됨)")
    if q.empty():
        return DecisionOutcome("crash", elapsed, None, "학생 프로세스가 결과 없이 종료되었습니다 (예외적 크래시)")
    status, payload = q.get()
    if status == "error":
        return DecisionOutcome("error", elapsed, None, payload)
    return DecisionOutcome("ok", elapsed, payload, None)


# ---------------------------------------------------------------------------
# 반환 라인업 검증 + 기본(폴백) 라인업
# ---------------------------------------------------------------------------

def default_offense_lineup(team: Team) -> List[int]:
    """폴백 타순 — 기본 수비 배치의 앞 9명(투수 제외)을 그대로 타순으로 쓴다."""
    return list(default_defense_lineup(team)[:9])


def default_defense_lineup(team: Team) -> List[int]:
    ifs = team.roster_by_position.get("내야수", [])[:4]
    ofs = team.roster_by_position.get("외야수", [])[:3]
    c = team.roster_by_position.get("포수", [])[:1]
    used = set(ifs) | set(ofs) | set(c)
    dh = next((p for p in team.batter_pcodes if p not in used), None)
    pitcher = team.pitcher_pcodes[0] if team.pitcher_pcodes else None
    lineup = list(ifs) + list(ofs) + list(c) + [dh] + [pitcher]
    return lineup


def _as_int_list(value):
    """학생 반환값의 한 리스트를 정수 리스트로 변환. 실패하면 (None, 에러메시지)."""
    if value is None:
        return None, "명단이 None입니다"
    try:
        return [int(x) for x in value], None
    except Exception:  # noqa: BLE001 - 학생이 뭘 반환할지 알 수 없음
        # int(x)는 x가 무한대/NaN이면 TypeError/ValueError가 아니라 OverflowError를 던지고,
        # 그 외에도 커스텀 __int__ 구현 등 예측 불가능한 예외가 나올 수 있어 광범위하게 잡는다.
        return None, "명단을 정수 pCode 리스트로 변환할 수 없습니다 (무한대/NaN이거나 정수가 아닌 값 포함)"


def validate_lineups(league: LeagueData, team: Team, result):
    """학생이 반환한 {"defense": [...10], "offense": [...9]} dict를 검증한다.
    문제 없으면 (offense_list, defense_list) 튜플, 문제 있으면 에러 메시지 문자열을 반환한다."""
    if not isinstance(result, dict):
        return "반환값은 {'defense': [...10], 'offense': [...9]} 형태의 dict여야 합니다"
    if "defense" not in result or "offense" not in result:
        return "반환한 dict에 'defense' 또는 'offense' 키가 없습니다"

    defense, err = _as_int_list(result["defense"])
    if err:
        return f"defense: {err}"
    offense, err = _as_int_list(result["offense"])
    if err:
        return f"offense: {err}"

    roster = set(team.batter_pcodes) | set(team.pitcher_pcodes)
    pitchers = set(team.pitcher_pcodes)

    if len(defense) != 10:
        return f"defense 길이가 {len(defense)}입니다 (기대값 10)"
    if len(set(defense)) != len(defense):
        return "defense에 중복된 선수가 있습니다"
    for p in defense:
        if p not in roster:
            return f"defense의 pCode {p}는 우리 팀 선수가 아닙니다"
    if defense[9] not in pitchers:
        return "defense의 마지막(10번째) 자리는 반드시 실제 투수여야 합니다"
    for p in defense[:9]:
        if p in pitchers:
            return "투수는 defense의 마지막 자리(10번째)에만 위치할 수 있습니다"

    if len(offense) != 9:
        return f"offense 길이가 {len(offense)}입니다 (기대값 9)"
    if len(set(offense)) != len(offense):
        return "offense에 중복된 선수가 있습니다"
    if set(offense) != set(defense[:9]):
        return "offense는 defense의 앞 9명(투수 제외)을 재배열한 것이어야 합니다 (선수 교체 불가)"

    return offense, defense
