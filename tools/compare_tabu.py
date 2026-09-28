"""두 제출 알고리즘을 같은 시드에서 홈/원정을 뒤바꿔 맞붙여 비교한다.

기본 비교 대상은 **작업트리의 example_tabu_lineup.py 와 그 파일의 git HEAD 버전**이다.
즉 "내가 방금 고친 게 실제로 나아졌나?"를 바로 잴 수 있다. --a/--b 로 아무 두 파일이나
지정할 수 있고, --b 에 HEAD 를 주면 --a 파일의 git HEAD 버전을 임시파일로 꺼내 쓴다.

기본(빠른) 모드는 신뢰하는 로컬 파일만 같은 프로세스에서 실행한다. 실제 경기 엔진과 명단
검증은 그대로 쓰지만 하드 타임아웃과 프로세스 격리는 적용하지 않으므로, 이 모드의 호출시간을
실제 채점 시간으로 해석하면 안 된다. --isolated 는 실제 서버와 같은 서브프로세스 러너를 쓴다.
--check 는 10개 구단 실제 업로드 검사와 지친 상태 결정성 검사를 함께 돌린다.

예시:
  python tools/compare_tabu.py --seeds 2 --pairs 5                  # 작업트리 vs HEAD, 20경기
  python tools/compare_tabu.py --a examples/example_tabu_lineup.py \
                               --b examples/strategy_naive_best.py  # 임의의 두 파일
  python tools/compare_tabu.py --check --seeds 1 --pairs 1          # 제출 검사까지
"""
import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbo_sim.data_pipeline import load_league_data
from kbo_sim.game import Game
from kbo_sim.models import GameRosterState, build_team
from kbo_sim.student_api import (DecisionOutcome, load_student_module, team_status_dataframe,
    matchup_dataframe, validate_lineups)
from kbo_sim.student_check import full_check

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_A = ROOT / "examples/example_tabu_lineup.py"
PAIRS = [("삼성", "KT"), ("LG", "KIA"), ("두산", "NC"), ("롯데", "SSG"), ("한화", "키움")]


def resolve_head(path: Path) -> Path:
    """--b HEAD: path 파일의 git HEAD 버전을 임시파일로 꺼낸다."""
    rel = path.resolve().relative_to(ROOT).as_posix()
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                          capture_output=True, check=True).stdout
    out = Path(tempfile.gettempdir()) / f"HEAD_{path.name}"
    out.write_bytes(blob)
    return out


def check_example(ld, target: Path):
    """실제 업로드 검사 + '지친 상태에서도 같은 rng면 같은 결과'를 10개 구단 전부 확인한다."""
    reports = []
    for name in ld.teams_list():
        opp_name = next(t for t in ld.teams_list() if t != name)
        report = full_check(str(target), ld, name, opp_name, 10.0)
        assert report["ok"], report
        assert not report["warnings"], report["warnings"]
        reports.append(report)
        team, opp = build_team(ld, name), build_team(ld, opp_name)
        state = GameRosterState(ld, 19)
        for p in team.batter_pcodes + team.pitcher_pcodes:
            rt = state.get(p)
            rt.swing_count = 30.0
            rt.pitch_count = 100.0
        # 맞대결 표는 두 가지로 태운다:
        #  (1) 빈 표 — 데이터를 갈아끼웠을 때의 방어 경로
        #  (2) 실제 경기와 같은 표 — game.py _decide 와 문자 그대로 같은 조합(양 팀 투수 전원)
        tables = {
            "empty": matchup_dataframe(ld, [], []),
            "full": matchup_dataframe(ld,
                                      list(team.pitcher_pcodes) + list(opp.pitcher_pcodes),
                                      list(opp.batter_pcodes) + list(team.batter_pcodes)),
        }
        module = load_student_module(str(target), "compare_check")
        for label, mu in tables.items():
            kwargs = dict(my_team=team_status_dataframe(ld, team, state),
                opponent_team=team_status_dataframe(ld, opp, state), matchups=mu,
                context={"inning": 9, "half": "bottom", "batting_order_start_index": 7,
                         "opp_pitcher_pcode": opp.pitcher_pcodes[0], "time_budget_sec": 10.0})
            first = module.decide_lineup(**kwargs, rng=random.Random(17))
            second = module.decide_lineup(**kwargs, rng=random.Random(17))
            assert first == second, (name, label)
            assert not isinstance(validate_lineups(ld, team, first), str), (name, label, first)
        # 1회 조건(직전 이닝 투수 없음)도 한 번 태운다 — 맞대결 표는 1회에도 가득 찬다
        kwargs = dict(my_team=team_status_dataframe(ld, team, GameRosterState(ld, 19)),
            opponent_team=team_status_dataframe(ld, opp, GameRosterState(ld, 19)),
            matchups=tables["full"],
            context={"inning": 1, "half": "top", "batting_order_start_index": 0,
                     "opp_pitcher_pcode": None, "time_budget_sec": 10.0})
        first = module.decide_lineup(**kwargs, rng=random.Random(5))
        assert not isinstance(validate_lineups(ld, team, first), str), (name, "inning1", first)
        print(f"CHECK {name}: 업로드검사 + 지친상태/빈표/전체표/1회 결정성 통과", flush=True)
    return reports


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default=str(DEFAULT_A), help="비교 대상 A (기본: 작업트리 example_tabu_lineup.py)")
    ap.add_argument("--b", default="HEAD", help="비교 대상 B. 'HEAD'면 A의 git HEAD 버전 (기본)")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--seed-start", type=int, default=100000)
    ap.add_argument("--pairs", type=int, default=5, choices=range(1, 6))
    ap.add_argument("--isolated", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default="output/tabu_comparison.json")
    args = ap.parse_args()
    if args.seeds < 0:
        ap.error("--seeds must be nonnegative")

    a_path = Path(args.a)
    if not a_path.exists():
        ap.error(f"--a 파일이 없습니다: {a_path}")
    b_path = resolve_head(a_path) if args.b == "HEAD" else Path(args.b)
    if not b_path.exists():
        ap.error(f"--b 파일이 없습니다: {b_path}")
    A, B = str(a_path), str(b_path)
    a_name, b_name = a_path.name, ("HEAD:" + a_path.name if args.b == "HEAD" else b_path.name)
    print(f"A = {a_name}  ({A})\nB = {b_name}  ({B})\n", flush=True)

    ld = load_league_data()
    checks = check_example(ld, a_path) if args.check else []
    results, durations = [], {"a": [], "b": []}
    fallbacks = {"a": 0, "b": 0}
    modules = {}

    def direct(filepath, module_name, *, timeout_sec, **kwargs):
        t0 = time.perf_counter()
        if filepath not in modules:
            modules[filepath] = load_student_module(filepath, module_name)
        lineups = modules[filepath].decide_lineup(**kwargs)
        elapsed = time.perf_counter() - t0
        status = "ok" if elapsed <= timeout_sec else "timeout"
        return DecisionOutcome(status, elapsed, lineups if status == "ok" else None,
                               None if status == "ok" else "soft time budget exceeded")

    started = time.perf_counter()
    for pair_no, (home, away) in enumerate(PAIRS[:args.pairs]):
        for index in range(args.seeds):
            seed = args.seed_start + pair_no * 10007 + index * 977
            for a_home in (True, False):
                paths = {home: A if a_home else B, away: B if a_home else A}
                game = Game(ld, build_team(ld, home), build_team(ld, away), paths, seed=seed)
                if args.isolated:
                    result = game.run()
                else:
                    with patch("kbo_sim.game.run_student_decision", direct):
                        result = game.run()
                a_team = home if a_home else away
                b_team = away if a_home else home
                for timing in game.timings:
                    durations["a" if timing.team == a_team else "b"].append(timing.elapsed_sec)
                for event in game.events:
                    if event["type"] == "algo_fallback":
                        fallbacks["a" if event["team"] == a_team else "b"] += 1
                results.append(dict(seed=seed, home=home, away=away, a_home=a_home,
                    a_runs=game.score[a_team], b_runs=game.score[b_team],
                    winner=None if result["winner"] is None else
                           "a" if result["winner"] == a_team else "b"))
            print(f"{len(results)} games: {home}/{away}, seed={seed}", flush=True)

    wins = sum(r["winner"] == "a" for r in results)
    losses = sum(r["winner"] == "b" for r in results)
    draws = len(results) - wins - losses
    n = len(results)
    point_rate = (wins + 0.5 * draws) / n if n else None
    # 승점비율의 표준오차 — 이 정도 표본에서 무엇을 구분할 수 있는지 같이 적어 둔다
    stderr = (0.25 / n) ** 0.5 if n else None
    summary = dict(lineup_policy="locked_roster_10",
        mode="isolated" if args.isolated else "trusted in-process (no hard timeout)",
        a=a_name, b=b_name, a_path=A, b_path=B,
        seeds_per_pair=args.seeds, seed_start=args.seed_start, pairs=PAIRS[:args.pairs],
        games=n, a_wins=wins, draws=draws, b_wins=losses,
        point_rate=point_rate, point_rate_stderr=stderr,
        detectable_margin=None if stderr is None else round(1.96 * stderr, 4),
        run_difference=sum(r["a_runs"] - r["b_runs"] for r in results),
        fallbacks=fallbacks,
        timing={label: {"calls": len(v), "mean_sec": sum(v)/len(v) if v else None,
                        "max_sec": max(v) if v else None} for label, v in durations.items()},
        elapsed_sec=time.perf_counter() - started, smoke_checks=checks, results=results)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("results", "smoke_checks")},
                     ensure_ascii=False, indent=2), flush=True)
    if point_rate is not None:
        print(f"\nA({a_name}) 승점비율 {point_rate:.4f} ± {stderr:.4f}  "
              f"(이 표본으로 구분 가능한 최소 차이 ±{1.96*stderr:.3f})", flush=True)


if __name__ == "__main__":
    main()
