"""
tools/tournament_random_teams.py
----------------------------------
tools/tournament_multiteam.py의 후속. 고정된 5개 팀 조합 대신, 매 시행마다
10개 구단 중 2개를 무작위로 뽑아 맞대결시킨다. 목적은 두 가지:

1. 특정 로스터 궁합(예: 키움/한화처럼 둘 다 약체인 조합)이 결과를 좌우하지 않도록
   가능한 모든 팀 조합(45개)에 노출을 넓게 분산시킨다.
2. 표본을 크게 늘려, 상위권 알고리즘끼리(TS/GA/PSO/rotation/planner)의 우열이
   진짜 신호인지 표본 노이즈인지 95% 신뢰구간으로 직접 판정한다.

팀-알고리즘 맞바꿈(미러링)은 tournament.py/tournament_multiteam.py와 동일하게 유지한다:
같은 시드에서 두 알고리즘이 서로의 팀·홈/원정을 맞바꿔 한 번씩 치르므로,
그 팀 조합 안에서의 전력차는 두 알고리즘에 똑같이 적용되어 상쇄된다.
팀 조합 자체는 시드에서 파생된 난수로 뽑으므로 재현 가능하다.

실행:
    python tools/tournament_random_teams.py                 # 기본: 알고리즘 쌍당 200시행(=400경기)
    python tools/tournament_random_teams.py 300 8           # 시행 300개, 병렬 8프로세스
"""
import itertools
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbo_sim.bootstrap import ensure_data
from kbo_sim.game import Game
from kbo_sim.models import build_team

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(BASE, "examples")

ENTRIES = [
    ("random",   os.path.join(EX, "baseline_random_algorithm.py")),
    ("naive",    os.path.join(EX, "strategy_naive_best.py")),
    ("rotation", os.path.join(EX, "strategy_fatigue_rotation.py")),
    ("planner",  os.path.join(EX, "strategy_planner.py")),
    ("GA",       os.path.join(EX, "example_ga_lineup.py")),
    ("TS",       os.path.join(EX, "example_tabu_lineup.py")),
    ("PSO",      os.path.join(EX, "example_pso_lineup.py")),
]

_LEAGUE = None


def _league():
    global _LEAGUE
    if _LEAGUE is None:
        _LEAGUE = ensure_data(verbose=False)
    return _LEAGUE


def play_one(job):
    from unittest.mock import patch
    from kbo_sim.student_api import DecisionOutcome, load_student_module

    def direct(filepath, module_name, *, timeout_sec, **kwargs):
        module = load_student_module(filepath, module_name)
        lineups = module.decide_lineup(**kwargs)
        return DecisionOutcome("ok", 0.0, lineups, None)

    home_name, home_path, away_name, away_path, home_team, away_team, seed = job
    ld = _league()
    g = Game(ld, build_team(ld, home_team), build_team(ld, away_team),
             {home_team: home_path, away_team: away_path}, seed=seed, timeout_sec=10.0)
    with patch("kbo_sim.game.run_student_decision", direct):
        r = g.run()
    winner = None if r["winner"] is None else (home_name if r["winner"] == home_team else away_name)
    return {"home_algo": home_name, "away_algo": away_name, "winner": winner,
            "runs": {home_name: r["home_score"], away_name: r["away_score"]},
            "allowed": {home_name: r["away_score"], away_name: r["home_score"]}}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def main(n_trials=200, workers=8):
    t0 = time.time()
    ld = _league()
    all_teams = ld.teams_list()
    jobs = []
    for (na, pa), (nb, pb) in itertools.combinations(ENTRIES, 2):
        for s in range(n_trials):
            seed = 200000 + s * 6421
            team_rng = random.Random((hash((na, nb)) ^ seed) & 0xFFFFFFFF)
            ta, tb = team_rng.sample(all_teams, 2)
            jobs.append((na, pa, nb, pb, ta, tb, seed))
            jobs.append((nb, pb, na, pa, ta, tb, seed))
    names = [n for n, _ in ENTRIES]
    print(f"참가 알고리즘 {len(names)}개 · 알고리즘 쌍당 {n_trials}시행(팀 무작위) · 총 {len(jobs)}경기\n")

    results = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(play_one, jobs, chunksize=20), 1):
                results.append(r)
                if i % 1000 == 0:
                    print(f"   … {i}/{len(jobs)} 경기 완료 ({time.time()-t0:.0f}초)")
    else:
        for i, j in enumerate(jobs, 1):
            results.append(play_one(j))

    # ---- 전체 성적표 ----
    rec = {n: {"w": 0, "l": 0, "d": 0, "rf": 0, "ra": 0, "g": 0} for n in names}
    for r in results:
        h, a = r["home_algo"], r["away_algo"]
        for n in (h, a):
            rec[n]["g"] += 1
            rec[n]["rf"] += r["runs"][n]
            rec[n]["ra"] += r["allowed"][n]
        if r["winner"] is None:
            rec[h]["d"] += 1; rec[a]["d"] += 1
        else:
            lo = a if r["winner"] == h else h
            rec[r["winner"]]["w"] += 1; rec[lo]["l"] += 1

    print("\n" + "=" * 92)
    print(f"전체 성적 (팀 무작위 배정, 승률 높은 순, 알고리즘당 {rec[names[0]]['g']}경기)")
    print("=" * 92)
    order = sorted(names, key=lambda n: -(rec[n]["w"] + 0.5 * rec[n]["d"]) / max(rec[n]["g"], 1))
    for n in order:
        v = rec[n]
        pts = v["w"] + 0.5 * v["d"]
        p, lo, hi = wilson(pts, v["g"])
        print(f"{n:<10}{v['g']:>5}{v['w']:>5}{v['d']:>4}{v['l']:>5}{p*100:>7.1f}%"
              f"{'[' + f'{lo*100:.0f}-{hi*100:.0f}%' + ']':>16}"
              f"{v['rf']/v['g']:>11.2f}{v['ra']/v['g']:>11.2f}")

    # ---- 21쌍 전부 직접 대결 승률 + 신뢰구간 ----
    print("\n" + "=" * 92)
    print("알고리즘 쌍별 직접 대결 (팀 무작위 배정으로 합산, 95% 신뢰구간이 50%를 벗어나면 유의미)")
    print("=" * 92)
    h2h = {}
    for r in results:
        h, a = r["home_algo"], r["away_algo"]
        h2h.setdefault((h, a), [0, 0, 0])
        h2h.setdefault((a, h), [0, 0, 0])
        if r["winner"] is None:
            h2h[(h, a)][1] += 1; h2h[(a, h)][1] += 1
        else:
            lo = a if r["winner"] == h else h
            h2h[(r["winner"], lo)][0] += 1
            h2h[(lo, r["winner"])][2] += 1
    sig, nosig = [], []
    for na, nb in itertools.combinations(names, 2):
        w, d, l = h2h[(na, nb)]
        n = w + d + l
        p, lo, hi = wilson(w + 0.5 * d, n)
        line = f"{na:<10} vs {nb:<10} {w:>3}승 {d:>3}무 {l:>3}패  승률 {p*100:5.1f}%  95%CI [{lo*100:.0f}-{hi*100:.0f}%]"
        if lo > 0.5 or hi < 0.5:
            sig.append(line + "  <- 유의미 (신뢰구간이 50% 안 걸침)")
        else:
            nosig.append(line + "  (50%와 통계적으로 구분 안 됨)")
    for line in sig:
        print(line)
    print(f"\n--- 아래 {len(nosig)}쌍은 이 표본으로는 우열을 가릴 수 없음 ---")
    for line in nosig:
        print(line)

    print(f"\n유의미하게 갈린 쌍: {len(sig)}/{math.comb(len(names),2)}")
    print(f"총 소요 {time.time()-t0:.0f}초")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    main(n, w)
