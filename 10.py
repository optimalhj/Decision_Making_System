import math
import random
import time

import pandas as pd

# ------------------------------------------------------------------------
# 타석 결과 확률은 엔진(kbo_sim/data_pipeline.py, probability.py)과 같은 방식으로 계산한다.
#   1) 타자/투수의 사건별 기록(볼넷·사구·삼진·1루타·2루타·3루타·홈런·아웃)을 리그 평균 쪽으로 축소
#      (타자 60타석, 투수 80타자 분량의 리그평균을 섞음 — 표본이 적은 선수의 극단값 방지)
#   2) log5: 타자율 × 투수율 / 리그율
#   3) 맞대결 기록이 있으면 PA/(PA+15) 비중으로 섞음
#   4) 체력 배수: 출루 사건 ×f, 아웃 사건 ×1/f 후 재정규화
# ERA/OPS 같은 요약 지표는 엔진이 쓰지 않는 값이라 잡음이 크므로 쓰지 않는다.
# ------------------------------------------------------------------------
EVENTS = ("BB", "HBP", "SO", "1B", "2B", "3B", "HR", "OUT")
ON_BASE = ("BB", "HBP", "1B", "2B", "3B", "HR")
BASES = {"1B": 1, "2B": 2, "3B": 3, "HR": 4}
ENGINE_BATTER_SHRINK = 60.0
ENGINE_PITCHER_SHRINK = 80.0
ENGINE_MATCHUP_SHRINK = 15.0
# 사건별 득점 가치(아웃 대비 선형가중치). RV_SCALE로 '이닝 안에서의 기여' 크기로 맞춘다
RUN_WEIGHTS = {"BB": 0.58, "HBP": 0.60, "1B": 0.74, "2B": 1.04, "3B": 1.31, "HR": 1.67}
RV_SCALE = 0.45

# ---- 상대 투수 예측 ----
PRED_CONFIDENCE = 0.60     # 1회: 예측한 선발에게 줄 확률. 나머지는 다른 투수들에게 나눠 준다
# 2회 이후: 직전 투수의 (누적 투구수 / 목표 투구수) 구간별로 "이번 이닝도 던질" 확률
STAY_PROB_BY_LOAD = ((0.5, 0.75), (0.8, 0.55), (1.0, 0.30), (float("inf"), 0.15))

# ---- 체력 모델 (엔진 kbo_sim/fatigue.py 의 시그모이드를 이 파일 안에서 그대로 재현) ----
BATTER_STEEPNESS = 16.0
BATTER_MAX_DROP = 0.63
PITCHER_STEEPNESS = 6.5
PITCHER_MAX_DROP = 0.73
FATIGUE_EXP = 0.86         # 확률 배수 = (타자배수/투수배수)^0.86
FATIGUE_CAP = 1.45         # 확률 배수 상한
FIELDING_SWINGS = 4.0      # 수비 하프이닝 1번 = 스윙 3~5회 환산 (평균 4)
SWINGS_PER_PA = 1.55       # 타석 1번에 소모하는 평균 스윙 수
PITCHES_PER_INNING = 16.0  # 투수가 한 이닝에 던지는 평균 투구수
# 이닝 중 투구수 지점과 가중치 (긴 이닝일수록 뒤쪽 지점까지 간다)
PITCH_SAMPLE_POINTS = ((2, 1.0), (6, 1.0), (10, 1.0), (14, 0.9), (18, 0.6), (24, 0.3))
PA_PER_BATTER_INNING = 0.48  # 타자 1명이 한 이닝에 평균적으로 서는 타석 수

# ---- 목적함수 가중치 (단위: 대략 '득점') ----
CONNECT_W = 0.25           # 앞 타자 출루 × 뒤 타자 루타 연결 효과
ERR_COST = 0.02            # 수비수 1명이 한 이닝에 실책으로 내주는 기대 실점(기준값)
MISMATCH_MULT = 1.5        # 포지션 불일치 시 실책 배수 (엔진 규칙)
CATCHER_MISMATCH_COST = 0.005  # 포수 자리는 타구 처리를 안 하지만, 혹시 몰라 아주 작게만 감점
GAMMA_BATTER = 2.0         # 지금 체력을 쓰면 이후 이닝에 잃는 타자 가치(대체 선수 대비)의 반영 비율
REPLACEMENT_PCT = 0.5      # 우리 타자 중 이 분위수의 타격 가치를 '대체 선수 수준'으로 본다
GAMMA_PITCHER = 0.3        # 같은 개념의 투수 버전

# ---- Tabu Search 파라미터 ----
TS_MAX_ITERS = 300
TS_NEIGHBORS = 40          # 반복마다 평가하는 이웃 후보 수 (candidate list)
TS_TENURE = 7              # 타부 기간(반복 수)
TS_STAGNATION = 40         # 최고해가 이만큼 개선되지 않으면 다변화(재시작)
TS_PERTURB_MOVES = 4       # 다변화할 때 최고해에 가하는 무작위 이동 수
TS_TIME_LIMIT_SEC = 5.0    # 안전장치. 보통은 반복 수 제한으로 먼저 끝난다

SLOT_GROUPS = ("내야수", "내야수", "내야수", "내야수",
               "외야수", "외야수", "외야수", "포수", "DH")


def _num(v, default):
    """v가 없거나(None) 결측(NaN)이면 default, 0.0처럼 유효한 실측값이면 그대로 반환한다.
    `row.get(col) or default` 식으로 쓰면 진짜 0인 값(OPS 0.000, ERA 0.00, health_pct 0 등)까지
    "없는 값" 취급해 default로 바꿔버리는 버그가 생긴다 (파이썬에서 0은 falsy이기 때문)."""
    return default if v is None or pd.isna(v) else v


def _event_counts(row, is_pitcher):
    """엔진과 같은 방식으로 사건별 횟수와 분모(타자 PA / 투수 TBF)를 만든다."""
    h = _num(row.get("H"), 0.0)
    d = _num(row.get("2B"), 0.0)
    t = _num(row.get("3B"), 0.0)
    hr = _num(row.get("HR"), 0.0)
    bb = _num(row.get("BB"), 0.0)
    hbp = _num(row.get("HBP"), 0.0)
    so = _num(row.get("SO"), 0.0)
    if is_pitcher:
        n = _num(row.get("TBF"), 0.0)
    else:
        n = _num(row.get("PA"), 0.0)
        if n <= 0:
            n = (_num(row.get("AB"), 0.0) + bb + hbp
                 + _num(row.get("SF"), 0.0) + _num(row.get("SAC"), 0.0))
    counts = {"BB": bb, "HBP": hbp, "SO": so, "1B": max(h - d - t - hr, 0.0), "2B": d, "3B": t,
              "HR": hr, "OUT": max(n - bb - hbp - h - so, 0.0)}
    return counts, n


def _normalize(rate):
    s = sum(rate.values())
    return {ev: v / s for ev, v in rate.items()} if s > 0 else {ev: 1.0 / len(rate) for ev in rate}


def _shrunk_rate(counts, n, k, league):
    """counts/n 을 리그평균 k타석 분량과 섞는다 (엔진 _rate_dict와 같음)."""
    return _normalize({ev: (counts[ev] + league[ev] * k) / (n + k) for ev in EVENTS})


def _log5(bat, pit, league):
    return _normalize({ev: max(bat[ev] * pit[ev] / max(league[ev], 1e-6), 1e-9) for ev in EVENTS})


def _blend_matchup(rate, row):
    """맞대결 기록이 있으면 PA/(PA+15) 비중으로 섞는다 (엔진 blend_with_matchup과 같음)."""
    if row is None:
        return rate
    counts, pa = _event_counts(row, is_pitcher=False)
    if pa <= 0:
        return rate
    w = pa / (pa + ENGINE_MATCHUP_SHRINK)
    return _normalize({ev: w * counts[ev] / pa + (1 - w) * rate[ev] for ev in EVENTS})


def _fatigued(rate, f):
    """출루 사건 ×f, 아웃 사건 ×1/f 후 재정규화 (엔진 apply_fatigue_and_jitter와 같음, 잡음 제외)."""
    return _normalize({ev: p * (f if ev in ON_BASE else 1.0 / f) for ev, p in rate.items()})


def _summary(rate):
    """사건 확률 → (출루율, 타석당 루타, 타석당 득점 가치)."""
    obp = sum(rate[ev] for ev in ON_BASE)
    tb = sum(rate[ev] * n for ev, n in BASES.items())
    rv = RV_SCALE * sum(rate[ev] * w for ev, w in RUN_WEIGHTS.items())
    return obp, tb, rv


def _inning_runs(stats):
    """[(출루율, 루타, 득점가치), ...] 순서로 타석에 설 때 3아웃까지의 기대 득점 근사."""
    probs = [1.0, 0.0, 0.0]
    total = 0.0
    prev_obp = None
    n = len(stats)
    for t in range(12):
        obp, tb, rv = stats[t % n]
        reach = probs[0] + probs[1] + probs[2]
        if reach < 1e-3:
            break
        total += reach * rv
        if prev_obp is not None:
            total += CONNECT_W * reach * prev_obp * tb
        out = 1.0 - obp
        probs = [probs[0] * obp, probs[1] * obp + probs[0] * out, probs[2] * obp + probs[1] * out]
        prev_obp = obp
    return total


def _perf_mult(count, target, steepness, max_drop):
    """엔진과 같은 체력 시그모이드. 1.0 = 정상, 1 - max_drop = 완전 탈진."""
    if target <= 0:
        target = 1.0
    x = steepness / target * (count - target)
    if x > 40:
        sig = 1.0
    elif x < -40:
        sig = 0.0
    else:
        sig = 1.0 / (1.0 + math.exp(-x))
    return 1.0 - max_drop * sig


def _bat_mult(count, target):
    return _perf_mult(count, target, BATTER_STEEPNESS, BATTER_MAX_DROP)


def _pit_mult(count, target):
    return _perf_mult(count, target, PITCHER_STEEPNESS, PITCHER_MAX_DROP)


def _pit_mult_inning(count, target):
    """한 이닝을 던지는 동안의 평균 체력 배수. 이닝 중간(+8구) 한 점만 보면, 목표 투구수가 10~12개인
    불펜 투수가 이닝 도중(16구 전후) 급격히 무너지는 것을 놓친다 (목표 11구 → 16구째 배수 약 0.3)."""
    num = den = 0.0
    for extra, w in PITCH_SAMPLE_POINTS:
        num += w * _pit_mult(count + extra, target)
        den += w
    return num / den


def _fatigue_factor(batter_mult, pitcher_mult):
    """엔진(probability.apply_fatigue_and_jitter)과 같은 체력 배수. 상하한 [1/1.45, 1.45]."""
    f = (batter_mult / max(pitcher_mult, 1e-3)) ** FATIGUE_EXP
    return min(max(f, 1.0 / FATIGUE_CAP), FATIGUE_CAP)


def _pitch_target(row):
    tgt = _num(row.get("pitch_target"), None)
    if tgt is None:
        tgt = 0.7 * _num(row.get("NP_per_G"), 40.0)
    return max(float(tgt), 10.0)


def decide_lineup(my_team: pd.DataFrame, opponent_team: pd.DataFrame,
                   matchups: pd.DataFrame, context: dict, rng: random.Random):
    t_start = time.perf_counter()

    # ------------------------------------------------------------------
    # 0) 우리 팀 / 상대 팀 데이터를 딕셔너리로 캐싱 (탐색 중에는 DataFrame을 건드리지 않는다)
    # ------------------------------------------------------------------
    ifs, ofs, cs, pits = {}, {}, {}, {}
    bats = {}                                   # 포지션 상관없이 우리 타자 전원
    for row in my_team.to_dict("records"):
        pcode = int(row.pop("pCode"))

        if row["role"] == "타자":
            bats[pcode] = row
            pos = row["position"]
            if pos == "내야수":
                ifs[pcode] = row
            elif pos == "외야수":
                ofs[pcode] = row
            elif pos == "포수":
                cs[pcode] = row
        else:
            pits[pcode] = row

    opp_pits, opp_bats = {}, {}
    for row in opponent_team.to_dict("records"):
        opp_pcode = int(row.pop("pCode"))
        if row["role"] == "투수":
            opp_pits[opp_pcode] = row
        else:
            opp_bats[opp_pcode] = row

    # 맞대결 표: (투수 pCode, 타자 pCode) 두 개를 함께 키로 쓴다. 기록이 없는 조합은 키가 없다.
    mu = {}
    if matchups is not None and len(matchups):
        for row in matchups.to_dict("records"):
            mu[(int(row["pitcherPCode"]), int(row["hitterPCode"]))] = row

    inning = int(context.get("inning", 1))
    remaining = max(0, 9 - inning)              # 이번 이닝 이후 남은 이닝 수
    is_home = context.get("half") == "bottom"   # 홈팀은 수비(초)를 먼저 하고 공격(말)한다
    start_idx = int(_num(context.get("batting_order_start_index"), 0)) % 9

    # ------------------------------------------------------------------
    # 0-2) 엔진과 같은 사건별 확률: 리그 평균(양 팀 합산) → 선수별 축소 비율
    # ------------------------------------------------------------------
    def league_rate(rows, is_pitcher):
        tot, n_tot = {ev: 0.0 for ev in EVENTS}, 0.0
        for row in rows:
            counts, n = _event_counts(row, is_pitcher)
            for ev in EVENTS:
                tot[ev] += counts[ev]
            n_tot += n
        return _normalize({ev: tot[ev] / n_tot for ev in EVENTS}) if n_tot > 0 else None

    default_rate = {"BB": 0.09, "HBP": 0.012, "SO": 0.19, "1B": 0.16, "2B": 0.045,
                    "3B": 0.004, "HR": 0.025, "OUT": 0.474}
    lg_bat = league_rate(list(bats.values()) + list(opp_bats.values()), False) or default_rate
    lg_pit = league_rate(list(pits.values()) + list(opp_pits.values()), True) or default_rate

    bat_rate, pit_rate = {}, {}
    for pool in (bats, opp_bats):
        for b, row in pool.items():
            counts, n = _event_counts(row, False)
            bat_rate[b] = _shrunk_rate(counts, n, ENGINE_BATTER_SHRINK, lg_bat)
    for pool in (pits, opp_pits):
        for q, row in pool.items():
            counts, n = _event_counts(row, True)
            pit_rate[q] = _shrunk_rate(counts, n, ENGINE_PITCHER_SHRINK, lg_pit)

    def pa_rate(b, q, f=1.0):
        """타자 b vs 투수 q 한 타석의 사건 확률 (q=None이면 리그 평균 투수)."""
        rate = _log5(bat_rate[b], pit_rate[q] if q is not None else lg_pit, lg_bat)
        if q is not None:
            rate = _blend_matchup(rate, mu.get((q, b)))
        return _fatigued(rate, f) if f != 1.0 else rate

    # ------------------------------------------------------------------
    # 1) 상대 투수 예측 — 한 명을 '가장 유력'으로 찍고, 나머지 후보에도 작은 확률을 남긴다
    # ------------------------------------------------------------------
    top_bats = sorted(bats, key=lambda b: -_summary(pa_rate(b, None))[2])[:12]

    def opp_pitcher_quality(q):
        """상대 투수 q가 얼마나 '내보낼 만한' 투수인지 (상대 입장에서 좋을수록 큼).
        우리 주력 타자들을 상대로 한 타석당 허용 득점가치(맞대결 반영)가 낮을수록 좋은 투수."""
        row = opp_pits[q]
        allowed = sum(_summary(pa_rate(b, q))[2] for b in top_bats) / max(len(top_bats), 1)
        health = _num(row.get("health_pct"), 100.0) / 100.0
        return (1.0 / max(allowed, 0.02)) ** 2 * (0.2 + 0.8 * health) ** 2

    def finding_opp_pit_rule(opp_pits, prior_pit_pcode=None):
        """반환값: {상대 투수 pCode: 이번 이닝 등판 확률}  (확률 합 = 1)

        - 1회(prior_pit_pcode=None): 모두 쌩쌩하므로 기록이 가장 좋은 투수를 선발로 예측.
        - 2회 이후: 직전 투수가 아직 여력이 있으면 계속 던진다고 보고, 지쳤으면 교체를 예측.
        """
        if not opp_pits:
            return {}
        quality = {q: opp_pitcher_quality(q) for q in opp_pits}

        if prior_pit_pcode is None or int(prior_pit_pcode) not in opp_pits:
            predicted = max(quality, key=quality.get)
            main_prob = PRED_CONFIDENCE
        else:
            predicted = int(prior_pit_pcode)
            row = opp_pits[predicted]
            load = _num(row.get("pitch_count"), 0.0) / _pitch_target(row)
            main_prob = next(p for limit, p in STAY_PROB_BY_LOAD if load < limit)

        others = {q: w for q, w in quality.items() if q != predicted}
        total = sum(others.values())
        if total <= 0:
            return {predicted: 1.0}
        dist = {q: (1 - main_prob) * w / total for q, w in others.items()}
        dist[predicted] = main_prob
        return dist

    opp_dist = finding_opp_pit_rule(opp_pits, context.get("opp_pitcher_pcode"))

    # ------------------------------------------------------------------
    # 2) 우리 타자별 기대 출루율/장타율 (예측 분포에 대한 기댓값) — 맞대결이 없으면 추정치 사용
    #    fld=1: 이번 이닝에 수비도 나가는 경우, fld=0: DH (수비 체력 소모 없음)
    # ------------------------------------------------------------------
    opp_pm = {q: _pit_mult_inning(_num(opp_pits[q].get("pitch_count"), 0.0), _pitch_target(opp_pits[q]))
              for q in opp_dist}

    swing_cnt, swing_tgt = {}, {}
    for pool in (bats, opp_bats):
        for b, row in pool.items():
            swing_cnt[b] = _num(row.get("swing_count"), 0.0)
            swing_tgt[b] = max(_num(row.get("swing_target"), 10.5), 1.0)

    # exp_obp/exp_slg/exp_rv[b][fld] : 출루율, 타석당 루타, 타석당 득점가치 (상대 투수 분포에 대한 기댓값)
    exp_obp, exp_slg, exp_rv = {}, {}, {}
    for b in bats:
        exp_obp[b], exp_slg[b], exp_rv[b] = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]
        for fld in (0, 1):
            # 홈팀 수비수는 수비(초)를 먼저 하고 나서 타석에 서므로 그만큼 지친 상태로 친다
            cnt = swing_cnt[b] + (FIELDING_SWINGS if fld and is_home else 0.0) + SWINGS_PER_PA / 2
            bm = _bat_mult(cnt, swing_tgt[b])
            if not opp_dist:
                mix = pa_rate(b, None, _fatigue_factor(bm, 1.0))
            else:
                mix = {ev: 0.0 for ev in EVENTS}
                for q, prob in opp_dist.items():
                    rate = pa_rate(b, q, _fatigue_factor(bm, opp_pm[q]))
                    for ev in EVENTS:
                        mix[ev] += prob * rate[ev]
            o, s, v = _summary(mix)
            exp_obp[b][fld], exp_slg[b][fld], exp_rv[b][fld] = min(max(o, 0.05), 0.75), s, v

    # ------------------------------------------------------------------
    # 3) 수비/투수 비용 미리 계산
    # ------------------------------------------------------------------
    fresh_value = {b: _summary(pa_rate(b, None))[2] for b in bats}
    # 체력 공급(타자 약 27명 × 목표 10.5스윙)이 수요(수비 8자리 × 9이닝 + 타석)보다 적어서
    # 경기 후반에는 누군가 반드시 탈진한다. 그 탈진을 '대체 선수 수준' 이하의 타자가 떠안도록,
    # 미래 체력 비용은 대체 선수보다 잘 치는 만큼(surplus)에만 매긴다.
    # → 약한 타자는 체력을 써도 비용 0, 잘 치는 타자일수록 아껴 둔다(수비 대신 DH, 교대 기용).
    ranked_values = sorted(fresh_value.values())
    replacement = ranked_values[int(len(ranked_values) * REPLACEMENT_PCT)] if ranked_values else 0.0
    surplus = {b: max(0.0, v - replacement) for b, v in fresh_value.items()}

    def future_loss(b, extra_swings, base_extra=0.0):
        """extra_swings 만큼 체력을 쓰면 남은 이닝에 잃게 될 타격 가치 (늦은 이닝일수록 0에 가까움)."""
        c0 = swing_cnt[b] + base_extra
        drop = _bat_mult(c0, swing_tgt[b]) - _bat_mult(c0 + extra_swings, swing_tgt[b])
        return GAMMA_BATTER * surplus[b] * PA_PER_BATTER_INNING * remaining * drop

    field_cost = {}         # field_cost[b][그룹] : 이 타자를 그 수비 자리에 세울 때의 비용
    bat_swing_cost = {}     # bat_swing_cost[b][fld] : 타석 1번 설 때마다 드는 체력 비용
    for b, row in bats.items():
        m_after = _bat_mult(swing_cnt[b] + FIELDING_SWINGS, swing_tgt[b])
        fatigue_err = 1.0 + (1.0 - m_after)          # 탈진 시 최대 약 1.63배
        loss = future_loss(b, FIELDING_SWINGS)
        costs = {}
        for grp in ("내야수", "외야수"):
            mismatch = MISMATCH_MULT if row["position"] != grp else 1.0
            costs[grp] = ERR_COST * mismatch * fatigue_err + loss
        costs["포수"] = (CATCHER_MISMATCH_COST if row["position"] != "포수" else 0.0) + loss
        field_cost[b] = costs
        bat_swing_cost[b] = [future_loss(b, SWINGS_PER_PA),
                             future_loss(b, SWINGS_PER_PA, FIELDING_SWINGS)]

    # 우리 투수가 상대할 타선: 상대의 직전 타순이 있으면 그것, 없으면 상대가 고를 법한 상위 9명
    opp_prev = [int(h) for h in (context.get("opp_prev_offense") or []) if int(h) in opp_bats]
    if len(opp_prev) == 9:
        opp_lineup = opp_prev
    else:
        opp_lineup = sorted(opp_bats, key=lambda h: -_summary(pa_rate(h, None))[2])[:9]
    # 상대 타자 체력: 상대가 홈이면(=우리가 원정) 수비를 먼저 하고 타석에 선다
    opp_extra = 0.0 if is_home else FIELDING_SWINGS
    opp_bm = {h: _bat_mult(swing_cnt[h] + opp_extra + SWINGS_PER_PA / 2, swing_tgt[h]) for h in opp_lineup}
    lg_allowed = _summary(lg_pit)[2]

    pit_cost = {}
    for p, row in pits.items():
        cnt, tgt = _num(row.get("pitch_count"), 0.0), _pitch_target(row)
        pm_mid = _pit_mult_inning(cnt, tgt)
        stats = [_summary(pa_rate(h, p, _fatigue_factor(opp_bm[h], pm_mid))) for h in opp_lineup]
        # 상대 타순이 몇 번부터 시작할지 모르므로 9가지 시작점의 평균 실점으로 본다
        runs_allowed = sum(_inning_runs(stats[s:] + stats[:s]) for s in range(len(stats))) / max(len(stats), 1)

        fresh_allowed = _summary(pit_rate[p])[2]
        goodness = max(0.02, 4.3 * (1.25 * lg_allowed - fresh_allowed))   # 대체 투수 대비 이닝당 실점 절약분
        drop = _pit_mult(cnt, tgt) - _pit_mult(cnt + PITCHES_PER_INNING, tgt)
        pit_cost[p] = runs_allowed + GAMMA_PITCHER * goodness * remaining * drop

    # ------------------------------------------------------------------
    # 4) 목적함수 fit(해) — 클수록 좋은 해
    #    해 = (slots: 9명 [내야4, 외야3, 포수, DH], 투수, order: 그 9명의 '실제 타격 순서')
    # ------------------------------------------------------------------
    def offense_value(order, dh):
        """선두부터 타격해 3아웃이 될 때까지의 기대 득점 근사. 타순이 바뀌면 값도 바뀐다."""
        probs = [1.0, 0.0, 0.0]                  # 이 타자 차례가 왔을 때 0/1/2아웃일 확률
        total = 0.0
        prev_obp = None
        for t in range(12):                      # 한 바퀴 돌고 다시 앞 타자까지 (드물지만 반영)
            b = order[t % 9]
            f = 0 if b == dh else 1
            reach = probs[0] + probs[1] + probs[2]
            if reach < 1e-3:
                break
            obp = exp_obp[b][f]
            total += reach * (exp_rv[b][f] - bat_swing_cost[b][f])
            if prev_obp is not None:
                total += CONNECT_W * reach * prev_obp * exp_slg[b][f]
            out = 1.0 - obp
            probs = [probs[0] * obp, probs[1] * obp + probs[0] * out, probs[2] * obp + probs[1] * out]
            prev_obp = obp
        return total

    fit_cache = {}

    def fit(sol):
        if sol in fit_cache:
            return fit_cache[sol]
        slots, p, order = sol
        value = offense_value(order, slots[8]) - pit_cost[p]
        for i in range(8):
            value -= field_cost[slots[i]][SLOT_GROUPS[i]]
        fit_cache[sol] = value
        return value

    # ------------------------------------------------------------------
    # 5) 초기해 — 규칙 기반 그리디 (Tabu Search의 출발점)
    # ------------------------------------------------------------------
    def initial_solution():
        used, slots = set(), []
        for grp, n in (("내야수", 4), ("외야수", 3), ("포수", 1)):
            cands = sorted((b for b in bats if b not in used),
                           key=lambda b: (bats[b]["position"] == grp, exp_rv[b][1] - field_cost[b][grp]),
                           reverse=True)
            slots += cands[:n]
            used.update(cands[:n])
        dh = max((b for b in bats if b not in used), key=lambda b: exp_rv[b][0])
        slots.append(dh)
        p = min(pits, key=pit_cost.get)
        order = sorted(slots, key=lambda b: exp_obp[b][0 if b == dh else 1], reverse=True)
        return tuple(slots), p, tuple(order)

    # ------------------------------------------------------------------
    # 6) 이웃 생성 — 이동 1번 = (새 해, 이 이동의 타부 속성, 이동 후 금지할 역이동 속성들)
    #    ① 타순 두 자리 교환  ② 수비 두 자리 교환(DH 포함)  ③ 벤치 타자와 교체  ④ 투수 교체
    # ------------------------------------------------------------------
    bat_list = list(bats)
    pit_list = list(pits)

    def neighbor(sol):
        slots, p, order = sol
        move = rng.random()
        if move < 0.35:                                          # ① 타순 교환
            i, j = rng.sample(range(9), 2)
            new_order = list(order)
            new_order[i], new_order[j] = new_order[j], new_order[i]
            attr = ("ord", frozenset((order[i], order[j])))
            return (slots, p, tuple(new_order)), attr, [attr]
        if move < 0.55:                                          # ② 수비 자리 교환
            i, j = rng.sample(range(9), 2)
            new_slots = list(slots)
            new_slots[i], new_slots[j] = new_slots[j], new_slots[i]
            attr = ("slot", frozenset((slots[i], slots[j])))
            return (tuple(new_slots), p, order), attr, [attr]
        if move < 0.85:                                          # ③ 벤치 교체
            on_field = set(slots)
            bench = [b for b in bat_list if b not in on_field]
            if not bench:
                return None
            i = rng.randrange(9)
            out_b, in_b = slots[i], rng.choice(bench)
            new_slots = list(slots)
            new_slots[i] = in_b
            new_order = tuple(in_b if b == out_b else b for b in order)   # 빠진 선수의 타순을 이어받음
            return (tuple(new_slots), p, new_order), ("bat", in_b), [("bat", out_b)]
        others = [q for q in pit_list if q != p]                  # ④ 투수 교체
        if not others:
            return None
        q = rng.choice(others)
        return (slots, q, order), ("pit", q), [("pit", p)]

    # ------------------------------------------------------------------
    # 7) Tabu Search
    #    - 매 반복 이웃 TS_NEIGHBORS개 중 타부가 아닌 최선으로 이동 (나빠지는 이동도 허용)
    #    - 방금 한 이동을 되돌리는 속성은 TS_TENURE 반복 동안 타부
    #    - 열망 기준: 타부여도 지금까지의 최고해보다 좋으면 허용
    #    - 정체되면 최고해를 조금 흔들어 다시 출발 (다변화)
    # ------------------------------------------------------------------
    def tabu_search(init):
        cur = best = init
        best_f = fit(init)
        tabu = {}
        no_improve = 0
        for it in range(TS_MAX_ITERS):
            if time.perf_counter() - t_start > TS_TIME_LIMIT_SEC:
                break
            chosen = None
            for _ in range(TS_NEIGHBORS):
                nb = neighbor(cur)
                if nb is None:
                    continue
                sol, attr, reverse_attrs = nb
                f = fit(sol)
                if tabu.get(attr, -1) >= it and f <= best_f:
                    continue
                if chosen is None or f > chosen[0]:
                    chosen = (f, sol, reverse_attrs)
            if chosen is None:
                no_improve += 1
            else:
                cur_f, cur, reverse_attrs = chosen
                for a in reverse_attrs:
                    tabu[a] = it + TS_TENURE
                if cur_f > best_f + 1e-12:
                    best, best_f = cur, cur_f
                    no_improve = 0
                else:
                    no_improve += 1

            if no_improve >= TS_STAGNATION:
                cur = best
                for _ in range(TS_PERTURB_MOVES):
                    nb = neighbor(cur)
                    if nb is not None:
                        cur = nb[0]
                tabu.clear()
                no_improve = 0
        return best

    # ------------------------------------------------------------------
    # 8) 실행 + 결과 변환
    # ------------------------------------------------------------------
    def to_lineup(sol):
        slots, p, order = sol
        defense = [int(b) for b in slots] + [int(p)]
        offense = [0] * 9
        for i, b in enumerate(order):            # 엔진은 start_idx부터 읽으므로 그 자리에 선두타자를 둔다
            offense[(start_idx + i) % 9] = int(b)
        return defense, offense

    def is_valid(defense, offense):
        return (len(defense) == 10 and len(set(defense)) == 10
                and all(b in bats for b in defense[:9]) and defense[9] in pits
                and len(offense) == 9 and set(offense) == set(defense[:9]))

    init = initial_solution()
    best = tabu_search(init)
    defense, offense = to_lineup(best)
    if not is_valid(defense, offense):           # 탐색 중 규칙이 깨졌다면 초기해로 대체
        defense, offense = to_lineup(init)

    return {"defense": defense, "offense": offense}
