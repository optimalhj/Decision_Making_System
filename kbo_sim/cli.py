"""
cli.py
------
"너, 내 구단주가 돼라" 실행 진입점.

사용 예:
    python -m kbo_sim.cli --a-name 김학생 --a-team 삼성 --a-algo submissions/kim.py \
                           --b-name 이학생 --b-team KT   --b-algo submissions/lee.py \
                           --seed 20260902 --timeout 10 --out output

실행하면:
  1) (구현고려 #5) 데이터 초기 설정
  2) 두 학생 알고리즘 파일을 드랍받아 함수 존재 여부 등 기본 검사 (운영방식 #3,#4)
  3) 3연전(운영방식 #7) 자동 진행, 각 경기 이닝별 학생 알고리즘 실행시간 콘솔 출력
  4) output/game{1,2,3}.json 생성 (viewer/broadcast_viewer.html로 열람)

야구는 3경기만으로는 실력과 우연을 가리기 어렵습니다. `--repeat N`을 주면 3연전을 N번
반복해 시리즈·경기 단위 종합 결과(승/무/패, 95% 신뢰구간, 경기당 평균 득실차)를 함께
계산합니다. 반복 결과는 `--out/rep01/`, `--out/rep02/`, ... 하위 폴더에, 종합 결과는
`--out/repeat_summary.json`에 저장됩니다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys

if hasattr(sys.stdout, "reconfigure"):
    # Windows 콘솔 기본 코드페이지(cp949)는 ✓/✗ 같은 문자를 못 담아 UnicodeEncodeError로
    # 죽는다. 출력 인코딩을 명시적으로 utf-8로 맞춰 어떤 콘솔에서도 죽지 않게 한다.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .bootstrap import ensure_data
from .match import Contestant, Match
from .student_check import full_check


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """이항비율 Wilson 95% 신뢰구간 (표본이 작아도 정규근사보다 안정적)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def sanity_check_submission(path: str, label: str, league, team: str, timeout: float) -> bool:
    """제출 코드 검증. 정적 검사(코드를 실행하지 않음) + 격리된 자식 프로세스에서의 1회 호출 테스트.
    ⚠️ 제출 파일을 채점 프로세스에서 그대로 import하면 모듈 최상위 코드(무한루프·파일쓰기 등)가
    아무 보호 없이 실행되므로, 반드시 student_check를 거친다."""
    print(f"[검사] {label} 제출 파일: {path}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{label} 파일을 찾을 수 없습니다: {path}")
    rep = full_check(path, league, team, None, timeout)
    for e in rep["errors"]:
        print(f"   🚫 {e}")
    for w in rep["warnings"]:
        print(f"   ⚠  {w}")
    for i in rep["infos"]:
        print(f"   ℹ  {i}")
    if rep.get("smoke"):
        for c in rep["smoke"]["cases"]:
            mark = "✓" if c["status"] == "ok" else "✗"
            print(f"   {mark} {c['label']} {c['elapsed_sec']}초"
                  + (f" — {c['detail']}" if c.get("detail") else ""))
    print(f"[검사] {label} {'통과' if rep['ok'] else '실패'}")
    return rep["ok"]


def run_repeated_series(league, a: Contestant, b: Contestant, args) -> int:
    """3연전을 --repeat번 반복해 시리즈·경기 단위 종합 결과를 낸다.

    반복마다 다른 시드를 쓰지 않으면 매번 같은 대진·결과가 나오므로, --seed로 시드열
    자체를 재현 가능하게 하되(같은 --seed면 반복 시드열도 항상 같음) 반복 사이에는
    서로 다른 시드를 쓴다.
    """
    n = args.repeat
    width = len(str(n))
    series_rng = random.Random(args.seed)  # --seed 생략 시 실행마다 다른 반복 시드열
    os.makedirs(args.out, exist_ok=True)

    print(f"\n=== {a.student_name}({a.team_name}) vs {b.student_name}({b.team_name}) "
          f"3연전 {n}회 반복 시작 ===\n")

    repeats = []
    series_wins = {"A": 0, "B": 0, "draw": 0}
    game_wl = {"A": [0, 0, 0], "B": [0, 0, 0]}  # [승, 무, 패] (경기 단위)
    run_diff_total = 0.0
    total_games = 0

    for i in range(1, n + 1):
        match_seed = series_rng.randrange(2 ** 31)
        sub_out = os.path.join(args.out, f"rep{i:0{width}d}")
        match = Match(league, a, b, seed=match_seed, timeout_sec=args.timeout, output_dir=sub_out)
        mr = match.run_series()

        series_wins[mr.winner if mr.winner else "draw"] += 1
        for g in mr.games:
            total_games += 1
            res = g.result
            home_pid, away_pid = g.home_id, g.away_id
            a_score = res["home_score"] if home_pid == "A" else res["away_score"]
            b_score = res["away_score"] if home_pid == "A" else res["home_score"]
            run_diff_total += a_score - b_score
            if res["winner"] is None:
                game_wl["A"][1] += 1; game_wl["B"][1] += 1
            else:
                winner_pid = home_pid if res["winner"] == g.home_team else away_pid
                loser_pid = "B" if winner_pid == "A" else "A"
                game_wl[winner_pid][0] += 1
                game_wl[loser_pid][2] += 1

        winner_label = mr.winner if mr.winner else "무승부"
        print(f"[반복 {i}/{n}] (시드 {match_seed}) 3연전 승점 {a.student_name} {mr.score['A']:.1f} : "
              f"{mr.score['B']:.1f} {b.student_name}  승자: {winner_label}  -> {sub_out}/")
        repeats.append({"repeat_no": i, "seed": match_seed, "score": mr.score, "winner": mr.winner,
                         "out_dir": sub_out})

    series_total = series_wins["A"] + series_wins["B"] + series_wins["draw"]
    series_rate_a = (series_wins["A"] + 0.5 * series_wins["draw"]) / series_total if series_total else 0.0
    series_rate_b = (series_wins["B"] + 0.5 * series_wins["draw"]) / series_total if series_total else 0.0
    points_a = game_wl["A"][0] + 0.5 * game_wl["A"][1]
    points_b = game_wl["B"][0] + 0.5 * game_wl["B"][1]
    p_a, lo_a, hi_a = wilson(points_a, total_games)
    p_b, lo_b, hi_b = wilson(points_b, total_games)
    avg_diff = run_diff_total / total_games if total_games else 0.0

    print("\n" + "=" * 70)
    print(f"종합 결과 ({n}회 반복 = {total_games}경기)")
    print("=" * 70)
    print(f"[3연전 단위, 총 {series_total}회]")
    print(f"  {a.student_name}: {series_wins['A']}승 {series_wins['draw']}무 {series_wins['B']}패 "
          f"(승률 {series_rate_a*100:.1f}%)")
    print(f"  {b.student_name}: {series_wins['B']}승 {series_wins['draw']}무 {series_wins['A']}패 "
          f"(승률 {series_rate_b*100:.1f}%)")
    print(f"\n[경기 단위, 총 {total_games}경기]")
    print(f"  {a.student_name}: {game_wl['A'][0]}승 {game_wl['A'][1]}무 {game_wl['A'][2]}패 "
          f"(승률 {p_a*100:.1f}%, 95% 신뢰구간 {lo_a*100:.1f}~{hi_a*100:.1f}%)")
    print(f"  {b.student_name}: {game_wl['B'][0]}승 {game_wl['B'][1]}무 {game_wl['B'][2]}패 "
          f"(승률 {p_b*100:.1f}%, 95% 신뢰구간 {lo_b*100:.1f}~{hi_b*100:.1f}%)")
    print(f"\n경기당 평균 득실차 ({a.student_name} 기준): {avg_diff:+.2f}")
    if lo_a > 0.5:
        print(f"-> 경기 단위 신뢰구간 하한이 50%를 넘었습니다: {a.student_name}이(가) 우연이 아니라 더 잘합니다.")
    elif hi_a < 0.5:
        print(f"-> 경기 단위 신뢰구간 상한이 50%에 못 미칩니다: {b.student_name}이(가) 우연이 아니라 더 잘합니다.")
    else:
        print("-> 경기 단위 신뢰구간이 50%를 포함합니다. 이 표본으로는 우열을 가릴 수 없습니다 (--repeat를 늘려보세요).")

    summary_path = os.path.join(args.out, "repeat_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "students": {"A": a.student_name, "B": b.student_name},
            "data_files": {k: os.path.basename(v) for k, v in league.source_files.items()},
            "repeat": n, "seed": args.seed, "repeats": repeats,
            "series_record": {"A": [series_wins["A"], series_wins["draw"], series_wins["B"]],
                               "B": [series_wins["B"], series_wins["draw"], series_wins["A"]]},
            "series_point_rate": {"A": series_rate_a, "B": series_rate_b},
            "game_record": {"A": game_wl["A"], "B": game_wl["B"]},
            "game_point_rate": {"A": p_a, "B": p_b},
            "game_point_rate_ci95": {"A": [lo_a, hi_a], "B": [lo_b, hi_b]},
            "avg_run_diff_A": avg_diff,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n종합 결과 저장: {summary_path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="너, 내 구단주가 돼라 - KBO 메타휴리스틱 시뮬레이션")
    ap.add_argument("--a-name", required=True)
    ap.add_argument("--a-team", required=True, help="예: 삼성, KT, LG, KIA, 두산, NC, 롯데, SSG, 한화, 키움")
    ap.add_argument("--a-algo", required=True, help="학생 A 제출 .py 파일 경로")
    ap.add_argument("--b-name", required=True)
    ap.add_argument("--b-team", required=True)
    ap.add_argument("--b-algo", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--out", default="output")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--repeat", type=int, default=1,
                     help="3연전을 이 횟수만큼 반복해 종합 결과를 냅니다 (기본 1회, 반복 없음). "
                          "반복 결과는 --out/rep01, rep02, ... 하위 폴더에, 종합 결과는 "
                          "--out/repeat_summary.json에 저장됩니다.")
    args = ap.parse_args(argv)
    if args.repeat < 1:
        print("오류: --repeat는 1 이상이어야 합니다.", file=sys.stderr)
        return 1

    league = ensure_data(args.data_dir) if args.data_dir else ensure_data()

    valid_teams = set(league.teams_list())
    for label, t in (("A", args.a_team), ("B", args.b_team)):
        if t not in valid_teams:
            print(f"오류: 팀 이름 '{t}'을(를) 찾을 수 없습니다. 사용 가능: {sorted(valid_teams)}", file=sys.stderr)
            return 1

    ok_a = sanity_check_submission(args.a_algo, args.a_name, league, args.a_team, args.timeout)
    ok_b = sanity_check_submission(args.b_algo, args.b_name, league, args.b_team, args.timeout)
    if not (ok_a and ok_b):
        print("\n오류: 제출 코드 검증을 통과하지 못했습니다. 위 항목을 고친 뒤 다시 실행하세요.",
              file=sys.stderr)
        return 1

    a = Contestant(args.a_name, args.a_algo, args.a_team)
    b = Contestant(args.b_name, args.b_algo, args.b_team)

    if args.repeat > 1:
        return run_repeated_series(league, a, b, args)

    match = Match(league, a, b, seed=args.seed, timeout_sec=args.timeout, output_dir=args.out)

    print(f"\n=== {a.student_name}({a.team_name}) vs {b.student_name}({b.team_name}) 3연전 시작 ===\n")
    mr = match.run_series()

    for g in mr.games:
        r = g.result
        print(f"\n[{g.game_no}경기] {g.away_student}({g.away_team}) {r['away_score']} : "
              f"{r['home_score']} {g.home_student}({g.home_team})"
              f"{'  [콜드게임]' if r['mercy'] else ''} -> JSON: {g.json_path}")

    display_scores = {f"{name} ({pid})": mr.score[pid] for pid, name in mr.students.items()}
    print(f"\n=== 매치 결과: {display_scores} ===")
    winner_label = f"{mr.students[mr.winner]} ({mr.winner})" if mr.winner else "무승부"
    print(f"승자: {winner_label}\n")

    summary_path = os.path.join(args.out, "match_summary.json")
    os.makedirs(args.out, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"score": mr.score, "winner": mr.winner, "students": mr.students,
                   "data_files": {k: os.path.basename(v) for k, v in league.source_files.items()},
                   "games": [{"game_no": g.game_no, "home_id": g.home_id, "away_id": g.away_id, "home_student": g.home_student, "away_student": g.away_student,
                              "home_team": g.home_team, "away_team": g.away_team, "result": g.result,
                              "json_path": g.json_path} for g in mr.games]}, f, ensure_ascii=False, indent=2)
    print(f"요약 저장: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
