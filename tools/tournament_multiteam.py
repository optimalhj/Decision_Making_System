"""
tools/tournament_multiteam.py
------------------------------
tools/tournament.py를 여러 팀 조합으로 확장한 대규모 검증 스크립트.

README의 미해결 항목 "좋은 알고리즘이 팀을 우선하는지 대대적 검증 필요"에 대한 답을 구한다.
tournament.py는 한 팀 조합(한화/LG)에서만 홈/원정·팀을 맞바꿔 팀빨을 상쇄했다.
이 스크립트는 **10개 구단을 모두 덮는 5개 팀 조합**에서 같은 방식을 반복해,
"더 좋은 알고리즘이 이긴다"는 결론이 어떤 팀을 맡아도 유지되는지 확인한다.

방법:
1. 5개 팀 조합(삼성/KT, LG/KIA, 두산/NC, 롯데/SSG, 한화/키움)에서 각각 라운드로빈을 돌린다.
2. 같은 시드에서 두 알고리즘의 팀·홈/원정을 맞바꿔 두 번씩 치른다 (팀빨/홈빨 상쇄, tournament.py와 동일).
3. 전체 합산 순위와 함께, **팀 조합별 순위**를 따로 계산해 순위가 뒤집히는지 관찰한다.
4. 알고리즘 쌍(A,B)마다 "A가 B를 이기는 방향"이 5개 팀 조합 모두에서 같은 부호인지 확인한다
   (부호가 바뀐다면 그 결과 차이는 알고리즘이 아니라 어떤 팀을 맡았는지로 설명된다는 뜻).

예제 알고리즘만 신뢰하고 실행하므로 프로세스 격리 없이 같은 프로세스에서 직접 호출한다
(tools/compare_tabu.py와 동일한 방식). 이 덕분에 게임당 실행 시간이 subprocess 격리 대비
100배 이상 빨라져, 대규모 표본을 현실적인 시간 안에 모을 수 있다.

실행:
    python tools/tournament_multiteam.py                 # 기본: 팀 조합당 시드 20개
    python tools/tournament_multiteam.py 30               # 팀 조합당 시드 30개
    python tools/tournament_multiteam.py 30 4             # 시드 30개, 병렬 4프로세스
"""
import itertools
import math
import os
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

# 10개 구단을 한 번씩 덮는 5개 팀 조합 (tools/compare_tabu.py의 PAIRS와 동일)
TEAM_PAIRS = [("삼성", "KT"), ("LG", "KIA"), ("두산", "NC"), ("롯데", "SSG"), ("한화", "키움")]

_LEAGUE = None


def _league():
    global _LEAGUE
    if _LEAGUE is None:
        _LEAGUE = ensure_data(verbose=False)
    return _LEAGUE


def play_one(job):
    """(홈알고, 원정알고, 홈팀, 원정팀, 시드) -> 결과 dict. 신뢰된 예제만 in-process 직접 호출."""
    from unittest.mock import patch
    from kbo_sim.student_api import DecisionOutcome, load_student_module

    def direct(filepath, module_name, *, timeout_sec, **kwargs):
        t0 = time.perf_counter()
        module = load_student_module(filepath, module_name)
        lineups = module.decide_lineup(**kwargs)
        elapsed = time.perf_counter() - t0
        return DecisionOutcome("ok", elapsed, lineups, None)

    home_name, home_path, away_name, away_path, home_team, away_team, seed = job
    ld = _league()
    g = Game(ld, build_team(ld, home_team), build_team(ld, away_team),
             {home_team: home_path, away_team: away_path}, seed=seed, timeout_sec=10.0)
    with patch("kbo_sim.game.run_student_decision", direct):
        r = g.run()
    if r["winner"] is None:
        winner = None
    else:
        winner = home_name if r["winner"] == home_team else away_name
    return {
        "team_pair": (home_team, away_team) if home_team < away_team else (away_team, home_team),
        "home_algo": home_name, "away_algo": away_name, "winner": winner,
        "runs": {home_name: r["home_score"], away_name: r["away_score"]},
        "allowed": {home_name: r["away_score"], away_name: r["home_score"]},
        "mercy": r["mercy"],
    }


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def standings(results, names):
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
    return rec


def print_standings(title, rec, names):
    print(f"\n{title}")
    order = sorted(names, key=lambda n: -(rec[n]["w"] + 0.5 * rec[n]["d"]) / max(rec[n]["g"], 1))
    print(f"{'알고리즘':<10}{'경기':>5}{'승':>5}{'무':>4}{'패':>5}{'승률':>8}"
          f"{'경기당 득점':>11}{'경기당 실점':>11}")
    for n in order:
        v = rec[n]
        if v["g"] == 0:
            continue
        pts = v["w"] + 0.5 * v["d"]
        p = pts / v["g"]
        print(f"{n:<10}{v['g']:>5}{v['w']:>5}{v['d']:>4}{v['l']:>5}{p*100:>7.1f}%"
              f"{v['rf']/v['g']:>11.2f}{v['ra']/v['g']:>11.2f}")
    return order


def head_to_head_by_pair(results, names, pair_labels):
    """알고리즘 쌍(A,B)마다 팀 조합별 승점률을 계산해, A>B 방향이 뒤집히는 조합이 있는지 확인."""
    h2h = {}  # (a,b,pair_label) -> [w,d,l] for a
    for r in results:
        h, a = r["home_algo"], r["away_algo"]
        pl = r["team_pair"]
        for (x, y) in ((h, a), (a, h)):
            h2h.setdefault((x, y, pl), [0, 0, 0])
        if r["winner"] is None:
            h2h[(h, a, pl)][1] += 1; h2h[(a, h, pl)][1] += 1
        else:
            lo = a if r["winner"] == h else h
            h2h[(r["winner"], lo, pl)][0] += 1
            h2h[(lo, r["winner"], pl)][2] += 1

    flips = []
    for na, nb in itertools.combinations(names, 2):
        per_pair_rate = {}
        for pl in pair_labels:
            w, d, l = h2h.get((na, nb, pl), [0, 0, 0])
            n = w + d + l
            if n == 0:
                continue
            per_pair_rate[pl] = (w + 0.5 * d) / n
        if len(per_pair_rate) < 2:
            continue
        rates = list(per_pair_rate.values())
        if max(rates) > 0.5 and min(rates) < 0.5:
            flips.append((na, nb, per_pair_rate))
    return flips


def main(n_seeds=20, workers=4):
    t0 = time.time()
    _league()
    jobs = []
    for (ta, tb) in TEAM_PAIRS:
        for (na, pa), (nb, pb) in itertools.combinations(ENTRIES, 2):
            for s in range(n_seeds):
                seed = 100000 + s * 977
                jobs.append((na, pa, nb, pb, ta, tb, seed))
                jobs.append((nb, pb, na, pa, ta, tb, seed))
    names = [n for n, _ in ENTRIES]
    pair_labels = [tuple(sorted(p)) for p in TEAM_PAIRS]
    print(f"참가 알고리즘 {len(names)}개 · 팀 조합 {len(TEAM_PAIRS)}개 · 총 {len(jobs)}경기")
    print(f"팀 조합: {TEAM_PAIRS}\n")

    results = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(play_one, jobs, chunksize=20), 1):
                results.append(r)
                if i % 500 == 0:
                    print(f"   … {i}/{len(jobs)} 경기 완료 ({time.time()-t0:.0f}초)")
    else:
        for i, j in enumerate(jobs, 1):
            results.append(play_one(j))
            if i % 500 == 0:
                print(f"   … {i}/{len(jobs)} ({time.time()-t0:.0f}초)")

    print("\n" + "=" * 92)
    overall = standings(results, names)
    overall_order = print_standings("전체 합산 성적 (전 팀 조합, 승률 높은 순)", overall, names)

    print("\n" + "=" * 92)
    print("팀 조합별 성적 (같은 알고리즘이 어떤 구단을 맡아도 순위가 유지되는가)")
    print("=" * 92)
    per_pair_order = {}
    for pl in pair_labels:
        sub = [r for r in results if r["team_pair"] == pl]
        rec = standings(sub, names)
        order = print_standings(f"[{pl[0]} / {pl[1]}]", rec, names)
        per_pair_order[pl] = order

    print("\n" + "=" * 92)
    print("순위 일관성 확인")
    print("=" * 92)
    top_overall = overall_order[0]
    bottom_overall = overall_order[-1]
    top_hits = sum(1 for pl in pair_labels if per_pair_order[pl] and per_pair_order[pl][0] == top_overall)
    bottom_hits = sum(1 for pl in pair_labels
                       if per_pair_order[pl] and per_pair_order[pl][-1] == bottom_overall)
    print(f"전체 1위 '{top_overall}' — 팀 조합 {len(pair_labels)}개 중 {top_hits}개에서도 1위")
    print(f"전체 꼴찌 '{bottom_overall}' — 팀 조합 {len(pair_labels)}개 중 {bottom_hits}개에서도 꼴찌")

    flips = head_to_head_by_pair(results, names, pair_labels)
    print(f"\n알고리즘 쌍 중 팀 조합에 따라 우열이 뒤바뀐 경우: {len(flips)}개"
          f" (전체 {math.comb(len(names), 2)}쌍 중)")
    for na, nb, rates in flips:
        detail = ", ".join(f"{pl[0]}/{pl[1]}={r*100:.0f}%" for pl, r in rates.items())
        print(f"  {na} vs {nb}: {detail}")

    best, worst = overall_order[0], overall_order[-1]
    wdl = [0, 0, 0]
    for r in results:
        h, a = r["home_algo"], r["away_algo"]
        if best not in (h, a) or worst not in (h, a):
            continue
        if r["winner"] is None:
            wdl[1] += 1
        elif r["winner"] == best:
            wdl[0] += 1
        else:
            wdl[2] += 1
    n = sum(wdl)
    p, lo, hi = wilson(wdl[0] + 0.5 * wdl[1], n)
    print(f"\n최상위 '{best}' vs 최하위 '{worst}' (전 팀 조합 합산): {wdl[0]}승 {wdl[1]}무 {wdl[2]}패 "
          f"→ 승률 {p*100:.1f}% (95% CI {lo*100:.0f}–{hi*100:.0f}%)")
    if lo > 0.5:
        print("  ✅ 신뢰구간 하한이 50%를 넘음 — 어떤 팀을 맡았는지와 무관하게 알고리즘 차이로 설명됩니다.")
    else:
        print("  ⚠ 신뢰구간이 50%를 포함 — 이 표본으로는 우연과 구분되지 않습니다.")

    print(f"\n총 소요 {time.time()-t0:.0f}초")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    main(n, w)
