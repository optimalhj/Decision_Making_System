"""
student_algorithm_template.py
------------------------------
"너, 내 구단주가 돼라" 과제 제출 템플릿.

이 파일을 복사해서 본인 학번_이름.py 로 저장한 뒤, decide_lineup() 함수 안을
Tabu Search / PSO / GA 중 하나(또는 조합)로 구현하세요.

===============================================================
지켜야 하는 것
===============================================================
1. 함수 이름/인자 순서를 절대 바꾸지 마세요:
   decide_lineup(my_team, opponent_team, matchups, context, rng)
2. 난수는 반드시 인자로 받은 rng(random.Random 인스턴스)만 사용하세요.
   전역 random 모듈이나 random.seed()를 쓰면 여러분 알고리즘 내부의 재현성에는
   문제가 없지만, 시뮬레이션 엔진 결과에는 어차피 영향을 주지 못합니다 (완전히
   분리된 RNG). 대신 채점/디버깅 시 여러분 알고리즘의 동작을 재현하려면 이 rng를
   써야 합니다.
3. 반환값은 {"defense": [10명], "offense": [9명]} 형태의 dict입니다.
   pCode(정수) 리스트를 쓰면 되고, my_team["pCode"] 컬럼 값을 그대로 쓰면 됩니다.
   - defense: 10명, 순서 고정 [내야수x4, 외야수x3, 포수, DH, 투수] (마지막이 투수)
   - offense: 9명, 투수 제외, 타순 순서. defense의 앞 9명을 재배열한 것이어야 합니다.
4. 이 함수는 이닝마다 팀당 한 번 호출됩니다 (한 번에 공수 명단을 모두 정함).
   제한시간(기본 10초)을 넘기면 직전 이닝 명단으로 자동 대체합니다. 프로세스 시작·import
   비용을 포함하므로 실제 환경에서 반복 수를 조정하세요.
5. 적합도 함수 안에서 매번 DataFrame을 필터링하지 말고, 함수 시작 시 딕셔너리로
   한 번 캐싱해서 쓰세요 (아래 예시 참고).
6. my_team의 AVG/OPS/ERA는 원본 값 그대로라 표본이 적은 선수는 왜곡되어 보입니다
   (예: 1타수 1안타 → OPS 4.000). PA_eff/TBF_eff로 표본크기를 확인해서 리그 평균 쪽으로
   당겨 쓰세요 (아래 batter_score/pitcher_score 예시 참고).
7. matchups(맞대결 표)에는 상대 팀 투수 **전원**과 우리 타자들의 기록이 들어옵니다. 그래도
   **이번 이닝에 그중 누가 나올지는 알 수 없습니다.** 한 명을 확정으로 놓고 최적화하지 말고,
   등판 후보 전원에 대한 기댓값으로 평가하세요 (아래 "맞대결 표" 절과 0-2) 캐싱 예시 참고).

자세한 인자/반환값 스펙은 kbo_sim/student_api.py 모듈 docstring에 전부 설명되어 있습니다.

이닝 선발 규칙: 이닝마다 팀당 한 번 호출되어 그 이닝의 공격 타순과 수비 배치를 함께 정합니다.
공수교대 때 선수·투수 교체는 없으며, 다음 이닝 시작에 다시 선발합니다. context의
opp_pitcher_pcode/opp_catcher_pcode는 상대의 '직전 이닝' 수비 기준이며 1회엔 None입니다.

===============================================================
맞대결 표(matchups) — 전체가 들어오지만, 이번 이닝 상대 투수는 여전히 모른다
===============================================================
- matchups 에는 "우리 팀 전체 투수 x 상대 팀 전체 타자" 와 "상대 팀 전체 투수 x 우리 팀 전체 타자"
  조합이 **모두** 들어 있습니다. 상대 선발뿐 아니라 불펜 투수까지 조회 대상이고, **1회에도
  비어 있지 않습니다.** 단 통산 맞대결이 한 번도 없는 조합은 행 자체가 없어서, 실제로 행이
  붙는 상대 투수는 로스터의 평균 76%(대진에 따라 59~90%)입니다. 즉 등판 후보 중 일부는
  맞대결 정보가 아예 없으니, 그런 투수는 개인 시즌기록으로만 평가해야 합니다.
- 그렇다고 이번 이닝 상대 투수를 알 수 있는 건 아닙니다. 양 팀이 이닝 시작에 **동시에** 명단을
  정하므로 **이번 이닝에 상대가 누구를 올릴지는 끝까지 알 수 없습니다.**
  context["opp_pitcher_pcode"] 는 '상대의 직전 이닝 투수'라는 **힌트**일 뿐이고(1회엔 None),
  이번 이닝에도 그 투수가 나온다는 보장은 없습니다. 확정 정보로 오해하지 마세요.
- 그래서 투수 한 명에 맞춰 타순을 짜기보다, **등판 후보(상대 투수 전원)에 대한 기댓값**으로
  평가하는 쪽이 낫습니다. 예: 우리 타자별로 상대 투수 전원과의 기록을 모아 PA(타석수) 가중평균을
  내고, 표본이 작으면 리그 평균 쪽으로 당깁니다(shrinkage). 상대 투수들의 체력(health_pct)과
  투구수(pitch_count)는 opponent_team 에 전부 보이므로, 지친 투수의 가중치를 낮추는 식으로
  '등판 확률'을 추정해 가중치에 반영하는 것도 좋은 방향입니다.
- 맞대결 표본은 대부분 아주 작습니다(한 조합에 1~10타석). 한 줄만 보고 판단하면 안 됩니다.
"""
import random

import pandas as pd

# ------------------------------------------------------------------------
# 표본이 적은 선수 함정 주의: 예를 들어 1타수 1안타면 원본 AVG=1.000, OPS=4.000으로 보입니다.
# 시뮬레이션 엔진은 내부적으로 표본크기(PA/TBF)에 비례해 리그 평균 쪽으로 당겨서(shrinkage)
# 실제 확률을 계산하므로 그런 선수가 실제로 4할 타자처럼 행동하지는 않습니다 — 하지만 my_team에
# 노출되는 AVG/OPS/ERA 컬럼은 원본 그대로(축소 적용 전)입니다. 아래 batter_score/pitcher_score는
# PA_eff/TBF_eff(엔진이 계산한 유효 표본수)를 이용해 여러분 스코어링에도 같은 보정을 적용하는
# 예시입니다. 이 보정이 없으면 "1타수 1안타" 선수를 4할 타자로 착각해 주전으로 기용하는 실수를
# 하게 됩니다. 자세한 설명은 프로그램_매뉴얼.md 참고.
# ------------------------------------------------------------------------
LEAGUE_AVG_OPS = 0.750
LEAGUE_AVG_ERA = 4.80
BATTER_SHRINK_PA = 30.0    # 이 값이 클수록 표본이 적은 선수를 더 강하게 리그평균으로 당김
PITCHER_SHRINK_TBF = 40.0
MATCHUP_SHRINK_PA = 40.0   # 맞대결 표본(PA 합)이 이보다 작으면 맞대결 성적을 그만큼 덜 믿는다
MATCHUP_WEIGHT = 0.30      # 개인 시즌성적 대비 맞대결 기댓값을 얼마나 섞을지 (0=무시, 1=맞대결만)
HINT_PITCHER_WEIGHT = 0.10  # '직전 이닝 투수' 힌트에 줄 최대 가중치. 확정이 아니므로 작게 유지


def _num(v, default):
    """v가 없거나(None) 결측(NaN)이면 default, 0.0처럼 유효한 실측값이면 그대로 반환한다.
    `row.get(col) or default` 식으로 쓰면 진짜 0인 값(OPS 0.000, ERA 0.00, health_pct 0 등)까지
    "없는 값" 취급해 default로 바꿔버리는 버그가 생긴다 (파이썬에서 0은 falsy이기 때문)."""
    return default if pd.isna(v) else v


def decide_lineup(my_team: pd.DataFrame, opponent_team: pd.DataFrame,
                   matchups: pd.DataFrame, context: dict, rng: random.Random):
    # ------------------------------------------------------------------
    # 0) 자주 쓰는 형태로 미리 캐싱 (매 적합도 평가마다 DataFrame 필터링 금지!)
    # ------------------------------------------------------------------

    batters = my_team[my_team["role"] == "타자"]

    pitchers = my_team[my_team["role"] == "투수"]

    batter_stat = {row["pCode"]: row for _, row in batters.iterrows()}       # pCode -> Series
    pitcher_stat = {row["pCode"]: row for _, row in pitchers.iterrows()}

    ifs = batters[batters["position"] == "내야수"]["pCode"].tolist()
    ofs = batters[batters["position"] == "외야수"]["pCode"].tolist()
    cs = batters[batters["position"] == "포수"]["pCode"].tolist()
    all_batter_codes = batters["pCode"].tolist()
    all_pitcher_codes = pitchers["pCode"].tolist()

    # ------------------------------------------------------------------
    # 0-2) 맞대결 표 캐싱 — 표에는 상대 투수 '전원'이 들어 있다 (1회에도 비어 있지 않음)
    # ------------------------------------------------------------------
    # 하지만 이번 이닝에 상대가 누구를 올릴지는 모른다. context["opp_pitcher_pcode"]는
    # '직전 이닝 투수'라는 힌트일 뿐(1회엔 None)이므로, 특정 투수 한 명을 확정으로 놓지 말고
    # 후보 전원에 대한 기댓값으로 보는 것이 핵심이다.
    # iterrows()는 행마다 Series를 새로 만들어 느리다 → to_dict("records")로 한 번에 dict 리스트로
    # 바꿔 쓴다 (표가 상대 투수 전원만큼 커져서 차이가 더 커졌다). 이 캐싱은 함수 시작에 딱 한 번만!
    mu_records = matchups.to_dict("records") if matchups is not None and len(matchups) else []

    my_batter_set = set(all_batter_codes)
    my_pitcher_set = set(all_pitcher_codes)
    matchup_lookup = {}    # (투수 pCode, 타자 pCode) -> 기록 dict. 특정 조합 한 줄을 찍어볼 때
    vs_opp_pitchers = {}   # 우리 타자 pCode -> [상대 투수 전원과의 맞대결 기록, ...]  (타순용)
    vs_opp_batters = {}    # 우리 투수 pCode -> [상대 타자 전원과의 맞대결 기록, ...]  (선발투수용)
    for row in mu_records:
        p, h = row["pitcherPCode"], row["hitterPCode"]
        matchup_lookup[(p, h)] = row
        if h in my_batter_set:         # 상대 투수 x 우리 타자 (우리 공격)
            vs_opp_pitchers.setdefault(h, []).append(row)
        elif p in my_pitcher_set:      # 우리 투수 x 상대 타자 (우리 수비)
            vs_opp_batters.setdefault(p, []).append(row)

    # pCode는 이 표에서 실수(float)로 들어오지만, 정수 pCode로 조회해도 파이썬이 같은 키로 찾아준다.
    hint_pitcher = context.get("opp_pitcher_pcode")   # 힌트일 뿐! 이번 이닝 등판 확정이 아니다

    def matchup_expected_ops(rows, hint_pcode=None):
        """맞대결 기록 목록을 '기대 OPS' 하나로 요약한다 (PA 가중평균 + 리그평균 쪽으로 축소).

        누가 등판할지 모르므로 후보 전원을 PA(타석수)로 가중해 평균 낸다. hint_pcode(직전 이닝
        투수)가 주어지면 그 투수만 가중치를 조금 올린다 — 확정이 아니므로 '조금'만.
        더 정교하게 하려면 opponent_team의 health_pct/pitch_count로 등판 확률을 추정해
        가중치에 곱하면 된다 (많이 던져 지친 투수는 이번 이닝에 나올 가능성이 낮다)."""
        num = den = pa_sum = 0.0
        for r in rows:
            pa = _num(r.get("PA"), 0.0)
            if pa <= 0:
                continue
            w = pa
            if hint_pcode is not None and r["pitcherPCode"] == hint_pcode:
                w *= 2.0                                    # 직전 이닝 투수 = 조금 더 나올 법한 후보
            num += w * _num(r.get("OPS"), LEAGUE_AVG_OPS)
            den += w
            pa_sum += pa
        ops = num / den if den > 0 else LEAGUE_AVG_OPS
        shrink = pa_sum / (pa_sum + MATCHUP_SHRINK_PA)      # 표본이 적을수록 0에 가까워짐
        return shrink * ops + (1 - shrink) * LEAGUE_AVG_OPS

    # 선수별로 한 번씩만 집계해 둔다 (적합도 평가 때마다 다시 계산하면 제한시간만 잡아먹는다)
    batter_mu_ops = {h: matchup_expected_ops(rows, hint_pitcher)
                     for h, rows in vs_opp_pitchers.items()}   # 우리 타자의 기대 OPS (높을수록 좋음)
    pitcher_mu_ops = {p: matchup_expected_ops(rows)
                      for p, rows in vs_opp_batters.items()}   # 우리 투수의 피 OPS (낮을수록 좋음)

    # ------------------------------------------------------------------
    # 1) 간단한 적합도(fitness) 함수 예시 - OPS와 체력을 이용한 아주 단순한 점수.
    #    실제 과제에서는 이 부분을 여러분의 메타휴리스틱 탐색으로 대체하세요.
    # ------------------------------------------------------------------
    def batter_score(pcode):
        row = batter_stat[pcode]
        raw_ops = _num(row.get("OPS"), LEAGUE_AVG_OPS)
        pa = _num(row.get("PA_eff", row.get("PA")), 0.0)
        w = pa / (pa + BATTER_SHRINK_PA)               # 표본이 적을수록 w가 0에 가까워짐
        ops = w * raw_ops + (1 - w) * LEAGUE_AVG_OPS    # 리그 평균 쪽으로 축소(shrinkage)
        # 맞대결 보정: '상대 투수 후보 전원'을 상대로 한 기대 OPS를 MATCHUP_WEIGHT 만큼 섞는다.
        # 맞대결 기록이 없는 타자에게는 리그평균이 들어가므로 손해도 이득도 보지 않는다.
        mu_ops = batter_mu_ops.get(pcode, LEAGUE_AVG_OPS)
        ops = (1 - MATCHUP_WEIGHT) * ops + MATCHUP_WEIGHT * mu_ops
        health = _num(row.get("health_pct"), 100.0) / 100.0
        return ops * (0.5 + 0.5 * health)  # 체력이 떨어지면 점수 하락

    def pitcher_score(pcode):
        row = pitcher_stat[pcode]
        raw_era = _num(row.get("ERA"), LEAGUE_AVG_ERA)
        tbf = _num(row.get("TBF_eff", row.get("TBF")), 0.0)
        w = tbf / (tbf + PITCHER_SHRINK_TBF)
        era = w * raw_era + (1 - w) * LEAGUE_AVG_ERA
        health = _num(row.get("health_pct"), 100.0) / 100.0
        # 맞대결 보정: 이 투수가 '상대 타선 전체'에게 허용해온 기대 OPS (낮을수록 좋다).
        allowed = pitcher_mu_ops.get(pcode, LEAGUE_AVG_OPS)
        mu_mult = LEAGUE_AVG_OPS / max(allowed, 0.30)       # 잘 막아왔으면 1보다 커진다
        mu_mult = min(max(mu_mult, 0.80), 1.25)             # 표본이 작으니 영향은 제한해 둔다
        return (1.0 / (era + 1.0)) * (0.5 + 0.5 * health) * mu_mult

    # ------------------------------------------------------------------
    # 2) TODO: 여기를 Tabu Search / PSO / GA로 교체하세요.
    #    아래는 "점수 높은 순으로 그리디하게 채우는" 매우 단순한 자리표시자(placeholder)입니다.
    # ------------------------------------------------------------------
    # (a) 수비: 포지션별 상위 점수 선수 + 나머지 중 1명 DH + 최고점 투수
    chosen_if = sorted(ifs, key=batter_score, reverse=True)[:4]
    chosen_of = sorted(ofs, key=batter_score, reverse=True)[:3]
    chosen_c = sorted(cs, key=batter_score, reverse=True)[:1]
    used = set(chosen_if) | set(chosen_of) | set(chosen_c)
    remaining = [p for p in all_batter_codes if p not in used]
    dh = sorted(remaining, key=batter_score, reverse=True)[0] if remaining else all_batter_codes[0]
    pitcher = sorted(all_pitcher_codes, key=pitcher_score, reverse=True)[0]
    defense = chosen_if + chosen_of + chosen_c + [dh, pitcher]

    # (b) 공격: 선발 9명(투수 제외)을 점수 순으로 세운다 (예시일 뿐 - 타순 최적화 로직으로 대체)
    #     batter_score 에 이미 '상대 투수 후보 전원'에 대한 기댓값이 들어 있다. 여기서는 추가로
    #     직전 이닝 투수가 이번에도 나올 경우를 아주 조금만 더 얹는다 (어디까지나 힌트이므로).
    #     matchup_lookup 으로 (그 투수, 우리 타자) 조합 한 줄만 바로 꺼내 쓰는 예시다.
    def order_score(pcode):
        s = batter_score(pcode)
        row = matchup_lookup.get((hint_pitcher, pcode)) if hint_pitcher is not None else None
        if row is None:
            return s
        pa = _num(row.get("PA"), 0.0)
        w = HINT_PITCHER_WEIGHT * pa / (pa + MATCHUP_SHRINK_PA)
        return s * ((1 - w) + w * _num(row.get("OPS"), LEAGUE_AVG_OPS) / LEAGUE_AVG_OPS)

    offense = sorted(defense[:9], key=order_score, reverse=True)

    return {"defense": defense, "offense": offense}
