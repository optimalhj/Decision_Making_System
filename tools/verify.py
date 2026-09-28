"""
tools/verify.py
----------------
엔진 전체 자체 점검 스크립트. 상수를 수정했거나 코드를 손본 뒤 이걸 돌려서
깨진 곳이 없는지 확인한다.

실행: python tools/verify.py

점검 항목
1. 데이터 무결성      - 10개 구단이 라인업(내야4/외야3/포수1/투수)을 구성할 인원을 갖췄는가
2. 타석 처리 fuzz     - 무작위 팀 조합으로 수천 타석을 돌려 예외/주자 중복이 없는가
3. 스텝 == 배치       - play_next_half()를 끝까지 돈 결과가 run()과 완전히 같은가
4. 학생코드 격리      - 타임아웃/예외/규칙위반이 폴백으로 안전 처리되는가
5. 3연전 대진표       - 같은 시드면 항상 같은 대진, 팀 교대 규칙이 맞는가
6. JSON 직렬화        - 브라우저 JSON.parse가 받아들이는 표준 JSON인가 (NaN 금지)
7. 득점 환경          - 정상 운영 시 KBO 현실 범위(4~6점)에 들어오는가
"""
import json
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbo_sim.atbat import empty_bases, resolve_plate_appearance
from kbo_sim.broadcast_export import export_game, _json_safe
from kbo_sim.data_pipeline import load_league_data
from kbo_sim.game import Game
from kbo_sim.match import Contestant, build_series_schedule
from kbo_sim.models import GameRosterState, build_team

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 저장소에 실제로 존재하는 예제만 쓴다. 없는 파일을 가리키면 두 팀 모두 매 이닝
# FileNotFoundError -> algo_fallback 으로 떨어지고, 폴백 라인업은 당연히 결정적이라
# [3]의 재현성 검사가 학생 코드를 한 줄도 실행하지 않은 채 초록불이 된다(공회전).
TABU = os.path.join(BASE, "examples", "example_tabu_lineup.py")
NAIVE = os.path.join(BASE, "examples", "strategy_naive_best.py")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


def main():
    print("데이터 로드 중…")
    ld = load_league_data()
    print()

    # 1. 데이터 무결성 -------------------------------------------------
    print("[1] 데이터 무결성")
    bad = []
    for name in ld.teams_list():
        t = build_team(ld, name)
        if (len(t.roster_by_position.get("내야수", [])) < 4
                or len(t.roster_by_position.get("외야수", [])) < 3
                or len(t.roster_by_position.get("포수", [])) < 1
                or len(t.pitcher_pcodes) < 1
                or len(t.batter_pcodes) < 9):
            bad.append(name)
    check("10개 구단 모두 라인업 구성 가능", not bad, f"부족: {bad}" if bad else
          f"{len(ld.batter_by_pcode)}타자 / {len(ld.pitcher_by_pcode)}투수")
    check("pCode 중복 없음",
          len(set(ld.batter_by_pcode) & set(ld.pitcher_by_pcode)) == 0)
    print()

    # 2. 타석 fuzz -----------------------------------------------------
    print("[2] 타석 처리 fuzz (무작위 팀 조합)")
    err = None
    total_pa = 0
    for trial in range(6):
        rng = random.Random(7000 + trial)
        t1, t2 = rng.sample(ld.teams_list(), 2)
        A, B = build_team(ld, t1), build_team(ld, t2)
        rs = GameRosterState(ld, game_seed=trial)
        dl = (B.roster_by_position["내야수"][:4] + B.roster_by_position["외야수"][:3]
              + [B.roster_by_position["포수"][0], B.batter_pcodes[-1], B.pitcher_pcodes[0]])
        bases, outs = empty_bases(), 0
        try:
            for i in range(1500):
                if outs >= 3:
                    outs, bases = 0, empty_bases()
                before = len([v for v in bases.values() if v])
                res = resolve_plate_appearance(ld, A.batter_pcodes[i % len(A.batter_pcodes)],
                                                B.pitcher_pcodes[0], dl, rs, rng, bases, outs)
                after = len([v for v in bases.values() if v])
                outs += res.outs_added
                total_pa += 1
                occ = [v for v in bases.values() if v]
                assert len(occ) == len(set(occ)), f"주자 중복: {bases}"
                assert 0 <= res.outs_added <= 3, f"이상한 아웃카운트 {res.outs_added}"
                # 주자 보존 법칙: (들어온 사람) == (득점 + 아웃 + 루에 남은 사람)
                # 주자가 증발하거나 복제되면 여기서 걸린다
                entered = before + (0 if res.event == "CS_INNING_END" else 1)
                assert entered == res.runs + res.outs_added + after, (
                    f"주자 보존 위반 event={res.event} before={before} after={after} "
                    f"runs={res.runs} outs={res.outs_added}")
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            break
    check(f"{total_pa:,}타석 무결점 처리", err is None, err or "예외/중복 없음")
    print()

    # 3. 스텝 == 배치 --------------------------------------------------
    print("[3] 스텝 실행 == 배치 실행 (같은 시드)")
    home, away = build_team(ld, "KT"), build_team(ld, "삼성")
    paths = {"KT": TABU, "삼성": NAIVE}
    g1 = Game(ld, home, away, paths, seed=4242)
    r1 = g1.run()
    g2 = Game(ld, home, away, paths, seed=4242)
    steps = 0
    while not g2.finished:
        g2.play_next_half()
        steps += 1
    check("최종 결과 동일", r1 == g2.result, f"{r1['away_score']}:{r1['home_score']}, {steps}하프이닝")
    check("이벤트 개수 동일", len(g1.events) == len(g2.events), f"{len(g1.events)}개")

    # 프로세스를 새로 띄워도 같은 결과가 나오는가 (PYTHONHASHSEED 영향 확인)
    import subprocess
    code = (
        "import json,sys;sys.path.insert(0,%r);"
        "from kbo_sim.data_pipeline import load_league_data;"
        "from kbo_sim.models import build_team;from kbo_sim.game import Game;"
        "ld=load_league_data();h=build_team(ld,'KT');a=build_team(ld,'삼성');"
        "g=Game(ld,h,a,{'KT':%r,'삼성':%r},seed=4242);print(json.dumps(g.run()))" % (BASE, TABU, NAIVE)
    )
    outs = []
    for hs in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        outs.append(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:])
    check("다른 프로세스/PYTHONHASHSEED에서도 동일 결과", outs[0] == outs[1] and outs[0] == json.dumps(r1),
          json.loads(outs[0])["winner"] if outs[0].startswith("{") else outs[0][:120])
    print()

    # 3-b. 2단계(라인업 선발 / 공격 진행) 실행 ---------------------------
    print("[3-b] 2단계 실행 (이닝 시작에 라인업 선발 -> 하프이닝별 공격 진행)")
    g3 = Game(ld, home, away, paths, seed=4242)
    phase_ok = True
    n_half = 0
    n_prepare = 0
    calls_per_prepare = []
    while not g3.finished:
        prep = g3.prepare_next_inning()
        if prep.get("prepared"):
            n_prepare += 1
            types_prep = {e["type"] for e in prep["events"]}
            if not types_prep <= {"lineup_decision", "inning_lineup", "algo_fallback"}:
                phase_ok = False
            calls_per_prepare.append(
                len([e for e in prep["events"] if e["type"] == "lineup_decision"]))
        play = g3.play_prepared_half()
        types_play = {e["type"] for e in play["events"]}
        if "lineup_decision" in types_play or "inning_lineup" in types_play:
            phase_ok = False
        n_half += 1
    check("1단계는 라인업만, 2단계는 타석만 처리", phase_ok, f"{n_half}하프이닝")
    check("라인업 선발은 이닝당 1회 (하프이닝마다 X)",
          n_prepare * 2 >= n_half and n_prepare <= (n_half + 1) // 2 + 1,
          f"이닝 선발 {n_prepare}회 / 하프이닝 {n_half}개")
    check("이닝 선발 1회당 알고리즘 2회 호출(양팀 각 1회)",
          all(c == 2 for c in calls_per_prepare), f"{calls_per_prepare}")
    check("2단계 실행 결과도 배치와 동일", g3.result == r1)
    # 같은 이닝을 두 번 준비하지 않는지 (초가 끝나 말이 남아 있을 때도)
    g4 = Game(ld, home, away, paths, seed=99)
    g4.prepare_next_inning()
    again = g4.prepare_next_inning()
    check("라인업 선발 중복 호출 방어", again.get("already") is True and not again["events"])
    g4.play_prepared_half()          # 초 진행
    mid = g4.prepare_next_inning()   # 말 시작 전 — 다시 뽑으면 안 됨
    check("초가 끝나도 말에는 다시 뽑지 않음", mid.get("already") is True and not mid["events"])
    print()

    # 3-c. 제출 코드 검증기 ---------------------------------------------
    print("[3-c] 제출 코드 검증기")
    from kbo_sim.student_check import full_check, static_check
    tmp_c = tempfile.mkdtemp()
    cases = {
        "sig": ("def decide_lineup(a, b, c, d, e):\n    return {}\n", "error", "인자 이름"),
        "nofunc": ("def other():\n    return []\n", "error", "함수가 없습니다"),
        "syntax": ("def decide_lineup(x)\n    pass\n", "error", "문법 오류"),
        "globalrandom": ("import random\ndef decide_lineup(my_team, opponent_team,"
                          " matchups, context, rng):\n    random.seed(1)\n    "
                          "return {'defense': [], 'offense': []}\n", "warning", "rng"),
        "noreturn": ("def decide_lineup(my_team, opponent_team, matchups, context, rng):\n"
                      "    x = 1\n", "error", "return"),
    }
    for key, (src, kind, needle) in cases.items():
        fp = os.path.join(tmp_c, key + ".py")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(src)
        res = static_check(fp)
        bucket = res.errors if kind == "error" else res.warnings
        hit = any(needle in m for m in bucket)
        check(f"{key} 검출", hit, (bucket[0][:60] + "…") if bucket else "검출 실패")

    for good in (TABU, NAIVE, os.path.join(BASE, "examples", "student_algorithm_template.py")):
        rep = full_check(good, ld, "한화", "LG", 10.0)
        check(f"정상 예제 통과: {os.path.basename(good)}", rep["ok"],
              f"오류{len(rep['errors'])} 경고{len(rep['warnings'])} "
              f"실행 {[c['status'] for c in (rep['smoke']['cases'] if rep['smoke'] else [])]}")
    print()

    # 4. 학생코드 격리 -------------------------------------------------
    print("[4] 학생 코드 격리 / 폴백")
    tmp = tempfile.mkdtemp()
    files = {
        "timeout": "import time\ndef decide_lineup(my_team, opponent_team, matchups, context, rng):\n    time.sleep(60)\n",
        "crash": "def decide_lineup(my_team, opponent_team, matchups, context, rng):\n    raise RuntimeError('boom')\n",
        "invalid": "def decide_lineup(my_team, opponent_team, matchups, context, rng):\n    p = my_team['pCode'].tolist()\n    return {'defense': [p[0]] * 10, 'offense': [p[0]] * 9}\n",
    }
    for key, src in files.items():
        with open(os.path.join(tmp, key + ".py"), "w", encoding="utf-8") as f:
            f.write(src)
    for key in files:
        h, a = build_team(ld, "NC"), build_team(ld, "롯데")
        g = Game(ld, h, a, {"NC": os.path.join(tmp, key + ".py"), "롯데": NAIVE},
                 seed=11, timeout_sec=2, max_innings=2)
        g.run()
        fb = [e for e in g.events if e["type"] == "algo_fallback" and e["team"] == "NC"]
        statuses = {e["status"] for e in fb}
        check(f"{key} 처리", bool(fb) and g.result is not None,
              f"폴백 {len(fb)}회, status={statuses}")
    print()

    # 5. 대진표 --------------------------------------------------------
    print("[5] 3연전 대진표")
    a = Contestant("김학생", TABU, "삼성")
    b = Contestant("이학생", NAIVE, "KT")
    s1 = build_series_schedule(a, b, random.Random(99))
    s2 = build_series_schedule(a, b, random.Random(99))
    check("같은 시드 → 같은 대진",
          [(g.home.team_name, g.away.team_name, g.seed) for g in s1]
          == [(g.home.team_name, g.away.team_name, g.seed) for g in s2])
    g1s, g2s = s1[0], s1[1]

    def team_of(sched, student):
        return sched.home.team_name if sched.home.student_name == student else sched.away.team_name

    def is_home(sched, student):
        return sched.home.student_name == student

    # "팀 교대" = 각 학생이 상대가 1차전에 쓰던 팀을 2차전에 맡는다
    team_swapped = (team_of(g1s, "김학생") == team_of(g2s, "이학생")
                    and team_of(g1s, "이학생") == team_of(g2s, "김학생")
                    and team_of(g1s, "김학생") != team_of(g2s, "김학생"))
    # "홈원정 교대" = 1차전 홈이던 학생이 2차전엔 원정
    ha_swapped = is_home(g1s, "김학생") != is_home(g2s, "김학생")
    detail = (f"1차 {g1s.away.student_name}({g1s.away.team_name})@{g1s.home.student_name}({g1s.home.team_name}) / "
              f"2차 {g2s.away.student_name}({g2s.away.team_name})@{g2s.home.student_name}({g2s.home.team_name})")
    check("2차전에서 학생↔팀 교대", team_swapped, detail)
    check("2차전에서 홈/원정 교대", ha_swapped)
    check("3차전은 무작위 배정", {s1[2].home.team_name, s1[2].away.team_name} == {"삼성", "KT"})
    check("3경기 편성", len(s1) == 3)
    print()

    # 6. JSON 직렬화 ---------------------------------------------------
    print("[6] JSON 직렬화 (브라우저 호환)")
    data = _json_safe(export_game(ld, g1))
    try:
        text = json.dumps(data, ensure_ascii=False, allow_nan=False)
        ok, detail = True, f"{len(text)//1024}KB"
    except ValueError as e:
        ok, detail = False, str(e)
    check("NaN/Inf 없는 표준 JSON", ok, detail)
    hs = [e for e in g1.events if e["type"] == "half_start"]
    check("half_start에 출전명단 포함", bool(hs) and len(hs[0]["batting_order"]) == 9
          and len(hs[0]["defense"]) == 10)
    check("half_start에 팀별 계산시간 포함", bool(hs) and "decisions" in hs[0]
          and set(hs[0]["decisions"]) == {"offense", "defense"})
    print()

    # 7. 득점 환경 -----------------------------------------------------
    print("[7] 득점 환경 (정상 운영 시나리오)")
    from tools.calibrate import sim_managed
    A = build_team(ld, "삼성")
    B = build_team(ld, "KT")
    order = sorted(A.batter_pcodes, key=lambda p: ld.batter(p)["OPS"], reverse=True)[:9]
    rot = sorted(B.pitcher_pcodes, key=lambda p: ld.pitcher(p)["NP_per_G"], reverse=True)[:4]
    dbase = (B.roster_by_position["내야수"][:4] + B.roster_by_position["외야수"][:3]
             + [B.roster_by_position["포수"][0], B.batter_pcodes[-1], rot[0]])
    runs = []
    for i in range(40):
        rng = random.Random(1234 + i)
        rs = GameRosterState(ld, game_seed=1234 + i)
        r, _ = sim_managed(ld, rng, rs, order, rot, dbase)
        runs.append(r)
    avg = sum(runs) / len(runs)
    check("9이닝 평균 득점이 KBO 현실 범위(3.5~7.0)", 3.5 <= avg <= 7.0, f"평균 {avg:.2f}점")
    print()

    # 8. 과거에 실제로 있었던 버그들의 재발 방지 회귀 테스트 ---------------
    print("[8] 회귀 테스트 (과거 발견된 버그)")
    import random as _rnd
    # empty_bases / resolve_plate_appearance는 이미 모듈 상단에서 import했다.
    # 여기서 다시 import하면 함수 전체에서 지역변수로 취급돼 [2]에서 UnboundLocalError가 난다.
    from kbo_sim.atbat import _resolve_bunt
    from kbo_sim.rng import GameRNG

    # (1) 학생 RNG가 호출마다 갱신되는가 (자식 프로세스가 소비해도)
    grng = GameRNG(seed=42)
    streams = []
    for _ in range(4):
        r = grng.student_rng("삼성:off")
        streams.append(tuple(round(r.random(), 6) for _ in range(3)))
    grng2 = GameRNG(seed=42)
    repeat = []
    for _ in range(4):
        r2 = grng2.student_rng("삼성:off")     # 호출당 한 번만 받아와야 한다
        repeat.append(tuple(round(r2.random(), 6) for _ in range(3)))
    check("학생 RNG가 호출마다 다른 난수열", len(set(streams)) == 4,
          f"고유 스트림 {len(set(streams))}/4")
    check("같은 시드면 학생 RNG도 재현됨", [s[0] for s in streams] == [r[0] for r in repeat])

    # (2) 도루자가 3아웃째면 그 타자는 타순을 넘기지 않는다
    src = ("def decide_lineup(my_team, opponent_team, matchups, context, rng):\n"
           "    b = my_team[my_team['role']=='타자']\n"
           "    p = my_team[my_team['role']=='투수']\n"
           "    i = b[b['position']=='내야수']['pCode'].tolist()[:4]\n"
           "    o = b[b['position']=='외야수']['pCode'].tolist()[:3]\n"
           "    c = b[b['position']=='포수']['pCode'].tolist()[:1]\n"
           "    used = set(i+o+c)\n"
           "    dh = [x for x in b['pCode'].tolist() if x not in used][:1]\n"
           "    d = i+o+c+dh+p['pCode'].tolist()[:1]\n"
           "    return {'defense': d, 'offense': d[:9]}\n")
    fixed_path = os.path.join(tmp, "fixed.py")
    with open(fixed_path, "w", encoding="utf-8") as f:
        f.write(src)
    cs_ok, cs_seen = True, 0
    for s in range(40):
        gg = Game(ld, build_team(ld, "KT"), build_team(ld, "삼성"),
                  {"KT": fixed_path, "삼성": fixed_path}, seed=6000 + s)
        gg.run()
        # 하프이닝별로 (타순 시작 + 실제 타석 수) == 다음 시작 인지 검증
        pas, start = {}, {}
        for e in gg.events:
            if e["type"] == "half_start":
                key = (e["inning"], e["half"])
                start[key] = (e["batting_team"], e["batting_order_start_index"])
                pas[key] = 0
            elif e["type"] == "pa_result":
                key = (e["inning"], e["half"])
                if e["event"] == "CS_INNING_END":
                    cs_seen += 1
                else:
                    pas[key] = pas.get(key, 0) + 1
        by_team = {}
        # (이닝, 초/말) 순으로 정렬 — 문자열 그대로 정렬하면 'bottom' < 'top' 이라 말이 초보다 먼저 온다
        for key in sorted(start, key=lambda k: (k[0], 0 if k[1] == "top" else 1)):
            team, idx = start[key]
            if team in by_team and by_team[team] != idx:
                cs_ok = False
            by_team[team] = (idx + pas.get(key, 0)) % 9
    check("도루 3아웃 시 타순 포인터 정확", cs_ok, f"CS 이닝종료 {cs_seen}회 관측")

    # (3) 2·3루 주자 상황 희생번트에서 주자 소멸 없음
    bunt_ok = True
    for s in range(200):
        r = _rnd.Random(s)
        bases = {1: None, 2: 202, 3: 303}
        before = 2
        res = _resolve_bunt(bases, 999, r)
        after = len([v for v in bases.values() if v])
        if before + 1 != res.runs + res.outs_added + after:
            bunt_ok = False
            break
    check("희생번트(2·3루) 주자 보존", bunt_ok)

    # (4)(5) 만루 실책·병살 득점 규칙은 [2]의 주자 보존 법칙이 상시 검증함
    #        여기서는 병살로 3아웃째가 될 때 득점이 인정되지 않는지 직접 확인
    dp_ok, dp_seen = True, 0
    A2, B2 = build_team(ld, "삼성"), build_team(ld, "KT")
    dl2 = (B2.roster_by_position["내야수"][:4] + B2.roster_by_position["외야수"][:3]
           + [B2.roster_by_position["포수"][0], B2.batter_pcodes[-1], B2.pitcher_pcodes[0]])
    # 주자는 실제 선수 pCode여야 한다 (엔진이 주자 성향을 조회하므로 가짜 번호는 KeyError)
    runner_1b, runner_3b = A2.batter_pcodes[1], A2.batter_pcodes[2]
    for s in range(1200):
        r = _rnd.Random(20000 + s)
        bases = {1: runner_1b, 2: None, 3: runner_3b}
        rs2 = GameRosterState(ld, game_seed=1)
        res = resolve_plate_appearance(ld, A2.batter_pcodes[0], B2.pitcher_pcodes[0], dl2,
                                        rs2, r, bases, 1)   # 1아웃에서 시작
        if res.description == "병살타":
            dp_seen += 1
            if 1 + res.outs_added >= 3 and res.runs > 0:
                dp_ok = False
                break
    check("이닝종료 병살에서 득점 불인정", dp_ok, f"병살 {dp_seen}회 관측")

    # (6)(7)(8) 끝내기 / 콜드게임 표기 / 콜드게임 시점 — 한 번의 경기 루프에서 함께 확인
    home_t, away_t = build_team(ld, "KT"), build_team(ld, "삼성")
    walkoff_found, walkoff_ok = 0, True
    mercy_ok, cold_ok, big_full, mercy_games = True, True, 0, 0
    for s in range(45):
        gg = Game(ld, home_t, away_t, {"KT": fixed_path, "삼성": fixed_path}, seed=9000 + s)
        r = gg.run()
        wo = [e for e in gg.events if e["type"] == "walkoff"]
        if wo:
            walkoff_found += 1
            idx = gg.events.index(wo[0])
            # 끝내기 뒤에는 타석이 더 없어야 하고, 홈팀 승리로 끝나야 한다
            if any(e["type"] == "pa_result" for e in gg.events[idx:]):
                walkoff_ok = False
            if r["winner"] != gg.home.name:
                walkoff_ok = False
        margin = abs(r["home_score"] - r["away_score"])
        if r["final_inning"] >= 9 and not r["mercy"] and margin >= 10:
            big_full += 1
        if r["mercy"]:
            mercy_games += 1
            if r["final_inning"] >= 9:
                mercy_ok = False        # 9이닝 완주인데 콜드게임 표기
            # 콜드게임은 하프이닝을 끝까지 치른 뒤 선언돼야 한다
            if not any(e["type"] == "half_end" and e["inning"] == r["final_inning"]
                        for e in gg.events):
                cold_ok = False
    check("끝내기 시 즉시 종료", walkoff_ok, f"{walkoff_found}경기에서 끝내기 발생")
    check("9이닝 완주 경기는 콜드게임 표시 안 함", mercy_ok,
          f"큰 점수차 완주 {big_full}건 / 콜드게임 {mercy_games}건")
    check("콜드게임은 하프이닝 종료 후 선언", cold_ok)

    # (9) 제출 파일 사전검사가 채점 프로세스를 막지 않음 (모듈 최상위 무한대기 방어)
    hang_path = os.path.join(tmp, "hang.py")
    with open(hang_path, "w", encoding="utf-8") as f:
        f.write("import time\ntime.sleep(120)\n"
                "def decide_lineup(my_team, opponent_team, matchups, context, rng):\n"
                "    return {}\n")
    t0 = time.time()
    rep = full_check(hang_path, ld, "삼성", "KT", 2.0)
    dt = time.time() - t0
    check("모듈 최상위에서 멈추는 제출물도 검사가 끝남", dt < 20 and not rep["ok"],
          f"{dt:.1f}초 만에 반환, ok={rep['ok']}")
    print()

    # Deterministic coverage of uploads, walkoffs and duplicate student names.
    import unittest
    from tools.test_regressions import RegressionTests
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RegressionTests)
    regression_result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("업로드·끝내기·동명이인 회귀 테스트", regression_result.wasSuccessful())

    # 결과 요약 --------------------------------------------------------
    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 60)
    print(f"결과: {passed} / {len(results)} 통과")
    if passed != len(results):
        print("실패 항목:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    print("모든 점검 통과 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
