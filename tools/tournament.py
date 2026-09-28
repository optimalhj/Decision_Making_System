"""
tools/tournament.py
--------------------
"알고리즘/전략에 따라 결과가 실제로 달라지는가?"를 정량적으로 확인하는 실험 스크립트.

수업 과제가 성립하려면 **더 좋은 알고리즘이 더 자주 이겨야** 한다. 야구는 원래 분산이 큰
스포츠라, 한두 경기 결과만 보면 우연인지 실력인지 구분할 수 없다. 그래서 이 스크립트는:

1. 참가 알고리즘들을 **라운드로빈**으로 전부 맞붙인다.
2. 팀 전력 차이를 없애기 위해, 같은 시드에서 **홈/원정과 팀을 서로 바꿔** 두 번씩 치른다
   (A가 한화·B가 LG인 경기 + B가 한화·A가 LG인 경기). 팀빨/홈빨이 상쇄된다.
3. 승률과 함께 **이항분포 95% 신뢰구간**을 계산해, 그 차이가 우연으로 설명되는지 판단한다.

실행:
    python tools/tournament.py                 # 기본: 시드 12개 (=쌍당 24경기)
    python tools/tournament.py 20              # 시드 20개
    python tools/tournament.py 12 4            # 시드 12개, 병렬 4프로세스
"""
import itertools
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbo_sim.bootstrap import ensure_data
from kbo_sim.game import Game
from kbo_sim.models import build_team

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(BASE, "examples")

# 참가 알고리즘 (이름, 파일)
ENTRIES = [
    ("random",   os.path.join(EX, "baseline_random_algorithm.py")),
    ("naive",    os.path.join(EX, "strategy_naive_best.py")),
    ("rotation", os.path.join(EX, "strategy_fatigue_rotation.py")),
    ("planner",  os.path.join(EX, "strategy_planner.py")),
    ("GA",       os.path.join(EX, "example_ga_lineup.py")),
    ("TS",       os.path.join(EX, "example_tabu_lineup.py")),
    ("PSO",      os.path.join(EX, "example_pso_lineup.py")),
]

TEAM_A, TEAM_B = "한화", "LG"     # 두 알고리즘이 번갈아 맡는 두 구단

_LEAGUE = None


def _league():
    global _LEAGUE
    if _LEAGUE is None:
        _LEAGUE = ensure_data(verbose=False)
    return _LEAGUE


def play_one(job):
    """(홈알고, 원정알고, 홈팀, 원정팀, 시드) -> 결과 dict"""
    home_name, home_path, away_name, away_path, home_team, away_team, seed = job
    ld = _league()
    g = Game(ld, build_team(ld, home_team), build_team(ld, away_team),
             {home_team: home_path, away_team: away_path}, seed=seed, timeout_sec=10.0)
    r = g.run()
    if r["winner"] is None:
        winner = None
    else:
        winner = home_name if r["winner"] == home_team else away_name
    return {
        "home_algo": home_name, "away_algo": away_name, "winner": winner,
        "home_runs": r["home_score"], "away_runs": r["away_score"],
        "runs": {home_name: r["home_score"], away_name: r["away_score"]},
        "allowed": {home_name: r["away_score"], away_name: r["home_score"]},
        "mercy": r["mercy"],
    }


def wilson(k, n, z=1.96):
    """이항비율 Wilson 95% 신뢰구간 (표본이 작아도 정규근사보다 안정적)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def main(n_seeds=12, workers=4):
    t0 = time.time()
    _league()
    jobs = []
    for (na, pa), (nb, pb) in itertools.combinations(ENTRIES, 2):
        for s in range(n_seeds):
            seed = 100000 + s * 977
            # 같은 시드로 두 번: (A=한화 홈) / (B=한화 홈) — 팀·홈 이점을 상쇄
            jobs.append((na, pa, nb, pb, TEAM_A, TEAM_B, seed))
            jobs.append((nb, pb, na, pa, TEAM_A, TEAM_B, seed))
    print(f"참가 알고리즘 {len(ENTRIES)}개 · 총 {len(jobs)}경기 (쌍당 {n_seeds*2}경기)")
    print(f"팀 배정: 홈={TEAM_A} / 원정={TEAM_B}, 매 시드마다 두 알고리즘이 홈·원정을 맞바꿔 1경기씩\n")

    results = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(play_one, jobs, chunksize=1), 1):
                results.append(r)
                if i % 20 == 0:
                    print(f"   … {i}/{len(jobs)} 경기 완료 ({time.time()-t0:.0f}초)")
    else:
        for i, j in enumerate(jobs, 1):
            results.append(play_one(j))
            if i % 20 == 0:
                print(f"   … {i}/{len(jobs)}")

    names = [n for n, _ in ENTRIES]
    # ---- 전체 성적표 ----
    rec = {n: {"w": 0, "l": 0, "d": 0, "rf": 0, "ra": 0, "g": 0} for n in names}
    head = {(a, b): [0, 0, 0] for a in names for b in names if a != b}   # a 기준 [승,무,패]
    for r in results:
        h, a = r["home_algo"], r["away_algo"]
        for n in (h, a):
            rec[n]["g"] += 1
            rec[n]["rf"] += r["runs"][n]
            rec[n]["ra"] += r["allowed"][n]
        if r["winner"] is None:
            rec[h]["d"] += 1; rec[a]["d"] += 1
            head[(h, a)][1] += 1; head[(a, h)][1] += 1
        else:
            lo = a if r["winner"] == h else h
            rec[r["winner"]]["w"] += 1; rec[lo]["l"] += 1
            head[(r["winner"], lo)][0] += 1; head[(lo, r["winner"])][2] += 1

    print("\n" + "=" * 92)
    print("전체 성적 (승률 높은 순)")
    print("=" * 92)
    print(f"{'알고리즘':<10}{'경기':>5}{'승':>5}{'무':>4}{'패':>5}{'승률':>8}"
          f"{'95% 신뢰구간':>18}{'경기당 득점':>11}{'경기당 실점':>11}")
    order = sorted(names, key=lambda n: -(rec[n]["w"] + 0.5 * rec[n]["d"]) / max(rec[n]["g"], 1))
    for n in order:
        v = rec[n]
        pts = v["w"] + 0.5 * v["d"]
        p, lo, hi = wilson(pts, v["g"])
        print(f"{n:<10}{v['g']:>5}{v['w']:>5}{v['d']:>4}{v['l']:>5}{p*100:>7.1f}%"
              f"{'[' + f'{lo*100:.0f}–{hi*100:.0f}%' + ']':>18}"
              f"{v['rf']/v['g']:>11.2f}{v['ra']/v['g']:>11.2f}")

    print("\n" + "=" * 92)
    print("상대전적 (행 기준 승-무-패)")
    print("=" * 92)
    print(f"{'':<10}" + "".join(f"{n:>13}" for n in order))
    for a in order:
        line = f"{a:<10}"
        for b in order:
            line += f"{'-':>13}" if a == b else f"{'-'.join(map(str, head[(a,b)])):>13}"
        print(line)

    # ---- 통계적 유의성: 최상위 vs 최하위 ----
    best, worst = order[0], order[-1]
    w, d, l = head[(best, worst)]
    n = w + d + l
    p, lo, hi = wilson(w + 0.5 * d, n)
    print("\n" + "=" * 92)
    print("판정")
    print("=" * 92)
    print(f"최상위 '{best}' vs 최하위 '{worst}': {w}승 {d}무 {l}패 "
          f"→ 승률 {p*100:.1f}% (95% CI {lo*100:.0f}–{hi*100:.0f}%)")
    if lo > 0.5:
        print("  ✅ 신뢰구간 하한이 50%를 넘음 — 우연이 아니라 알고리즘 차이로 설명됩니다.")
    else:
        print("  ⚠ 신뢰구간이 50%를 포함 — 이 표본으로는 우연과 구분되지 않습니다(시드를 늘려보세요).")
    spread = (rec[order[0]]["w"] + 0.5 * rec[order[0]]["d"]) / rec[order[0]]["g"] \
        - (rec[order[-1]]["w"] + 0.5 * rec[order[-1]]["d"]) / rec[order[-1]]["g"]
    print(f"최상위-최하위 승률 격차: {spread*100:.1f}%p")
    print(f"경기당 득점 폭: {min(rec[n]['rf']/rec[n]['g'] for n in names):.2f} ~ "
          f"{max(rec[n]['rf']/rec[n]['g'] for n in names):.2f}")
    print(f"경기당 실점 폭: {min(rec[n]['ra']/rec[n]['g'] for n in names):.2f} ~ "
          f"{max(rec[n]['ra']/rec[n]['g'] for n in names):.2f}")
    print(f"\n총 소요 {time.time()-t0:.0f}초")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    main(n, w)
