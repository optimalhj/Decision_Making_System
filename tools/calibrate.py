"""
tools/calibrate.py
-------------------
시뮬레이션 엔진의 득점 환경이 현실적인지 점검하는 보정/검증 스크립트.

기준선(비교 대상): KBO 정규시즌은 대체로 팀당 경기 4~5.5득점, 이닝당 타석 4.2~4.6개 수준.
이 스크립트는 학생 알고리즘 없이 엔진의 확률 모델만 직접 두드려서
  (1) 체력 영향을 제거했을 때의 순수 득점 환경
  (2) 체력을 반영했을 때(같은 투수를 계속 쓰는 최악의 경우 포함)
를 각각 측정한다.

실행: python -m tools.calibrate      (또는 python tools/calibrate.py)
"""
import random
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbo_sim.atbat import empty_bases, resolve_plate_appearance
from kbo_sim.data_pipeline import load_league_data
from kbo_sim.models import GameRosterState, build_team

MAX_PA_PER_INNING = 30  # 무한루프 방지


def sim_team_game(ld, rng, rs, batting_order, pitcher, defense, reset_fatigue_each_inning):
    total_runs = 0
    total_pa = 0
    slot = 0
    for _ in range(9):
        if reset_fatigue_each_inning:
            for p in list(batting_order) + list(defense):
                st = rs.get(p)
                st.pitch_count = 0.0
                st.swing_count = 0.0
        outs = 0
        bases = empty_bases()
        inning_pa = 0
        while outs < 3 and inning_pa < MAX_PA_PER_INNING:
            res = resolve_plate_appearance(ld, batting_order[slot % 9], pitcher, defense, rs, rng,
                                            bases, outs)
            outs += res.outs_added
            total_runs += res.runs
            slot += 1
            inning_pa += 1
            total_pa += 1
    return total_runs, total_pa


def sim_managed(ld, rng, rs, batting_order, pitcher_rotation, defense_base):
    """3이닝마다 새 투수로 교체하는 '정상 운영' 시나리오."""
    total_runs = 0
    total_pa = 0
    slot = 0
    for inn in range(9):
        pitcher = pitcher_rotation[min(inn // 3, len(pitcher_rotation) - 1)]
        defense = list(defense_base)
        defense[9] = pitcher
        outs = 0
        bases = empty_bases()
        inning_pa = 0
        while outs < 3 and inning_pa < MAX_PA_PER_INNING:
            res = resolve_plate_appearance(ld, batting_order[slot % 9], pitcher, defense, rs, rng,
                                            bases, outs)
            outs += res.outs_added
            total_runs += res.runs
            slot += 1
            inning_pa += 1
            total_pa += 1
    return total_runs, total_pa


def run(n_games=60, teams=("삼성", "KT"), seed0=500):
    ld = load_league_data()
    A = build_team(ld, teams[0])
    B = build_team(ld, teams[1])
    batting_order = sorted(A.batter_pcodes, key=lambda p: ld.batter(p)["OPS"], reverse=True)[:9]
    starter = max(B.pitcher_pcodes, key=lambda p: ld.pitcher(p)["NP_per_G"])
    rotation = sorted(B.pitcher_pcodes, key=lambda p: ld.pitcher(p)["NP_per_G"], reverse=True)[:4]
    defense = (B.roster_by_position["내야수"][:4] + B.roster_by_position["외야수"][:3]
               + [B.roster_by_position["포수"][0], B.batter_pcodes[-1], starter])

    print(f"타순: {teams[0]} OPS 상위 9명 / 상대 선발: "
          f"{ld.pitcher(starter)['displayId']} (ERA {ld.pitcher(starter)['ERA']}, "
          f"등판당 평균 {ld.pitcher(starter)['NP_per_G']:.0f}구)\n")

    scenarios = [
        ("① 체력 영향 제거 (순수 확률 모델만)", "reset"),
        ("② 정상 운영 (3이닝마다 투수 교체)", "managed"),
        ("③ 혹사 (한 투수로 9이닝 완투)", "complete"),
    ]
    for label, mode in scenarios:
        runs, pas = [], []
        for gi in range(n_games):
            rng = random.Random(seed0 + gi)
            rs = GameRosterState(ld, game_seed=seed0 + gi)
            if mode == "managed":
                r, pa = sim_managed(ld, rng, rs, batting_order, rotation, defense)
            else:
                r, pa = sim_team_game(ld, rng, rs, batting_order, starter, defense,
                                       reset_fatigue_each_inning=(mode == "reset"))
            runs.append(r)
            pas.append(pa / 9)
        print(f"[{label}]")
        print(f"   9이닝 득점  평균 {statistics.mean(runs):5.2f} / 중앙값 {statistics.median(runs):4.1f} "
              f"/ 최소 {min(runs)} / 최대 {max(runs)}")
        print(f"   이닝당 타석 평균 {statistics.mean(pas):5.2f}\n")

    print("참고 기준: KBO 정규시즌 팀당 경기 득점 대략 4~5.5점, 이닝당 타석 4.2~4.6개")
    print("설계 의도: ②(정상 운영)는 현실 수준, ③(혹사)은 확실히 무너지되 이닝은 정상적으로 끝나야 함")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run(n_games=n)
