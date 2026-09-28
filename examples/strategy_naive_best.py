"""
strategy_naive_best.py
-----------------------
⚠️ 잘못된 전략의 표본 (일부러 나쁘게 만든 대조군).

"제일 잘하는 선수만 계속 쓰면 되는 것 아닌가?" 라는 가장 흔한 오해를 그대로 구현했다.
- 타순: 시즌 OPS 상위 9명 고정 (표본 크기 무시 → 1타석 1안타짜리 선수가 1번타자가 되기도 함)
- 수비: 시즌 OPS 상위 선수 고정
- 투수: 방어율 가장 낮은 투수를 **9이닝 내내** 고정

체력이 회복되지 않는 이 게임에서 이렇게 하면 5~6회부터 투수가 무너지고 야수도 전부 지친다.
메타휴리스틱 알고리즘이 이 대조군을 얼마나 이기는지가 곧 "전략이 통하는가"의 척도가 된다.
(수업에서 baseline으로 함께 돌려보길 권장)

이닝 선발 규칙: decide_lineup은 이닝마다 팀당 한 번 호출되며 {"defense": [10명], "offense": [9명]}
을 반환합니다. defense는 [내야x4, 외야x3, 포수, DH, 투수] 순서, offense는 defense의 앞 9명
(투수 제외)을 타순대로 재배열한 것입니다. 공수교대 때 교체는 없으며 다음 이닝 시작에 다시 선발합니다.
"""
import random

import pandas as pd


def decide_lineup(my_team: pd.DataFrame, opponent_team: pd.DataFrame,
                   matchups: pd.DataFrame, context: dict, rng: random.Random):
    batters = my_team[my_team["role"] == "타자"]
    pitchers = my_team[my_team["role"] == "투수"]

    # 체력도, 표본 크기도 보지 않고 그냥 시즌 성적 순으로만 뽑는다
    ranked = batters.assign(_s=batters["OPS"].fillna(0.0)).sort_values("_s", ascending=False)

    def top(pos, n):
        return ranked[ranked["position"] == pos]["pCode"].tolist()[:n]

    ifs, ofs, cs = top("내야수", 4), top("외야수", 3), top("포수", 1)
    used = set(ifs) | set(ofs) | set(cs)
    dh = next(p for p in ranked["pCode"].tolist() if p not in used)
    # 방어율 1위 투수를 무조건 (지쳤든 말든) 계속 올린다
    ace = pitchers.assign(_e=pitchers["ERA"].fillna(99.0)).sort_values("_e")["pCode"].tolist()[0]
    defense = ifs + ofs + cs + [dh, ace]

    # 공격 타순: 선발 9명을 그냥 시즌 OPS 순으로 세운다 (표본 크기 무시)
    rank_pos = {p: i for i, p in enumerate(ranked["pCode"].tolist())}
    offense = sorted(defense[:9], key=lambda p: rank_pos.get(p, 999))

    return {"defense": defense, "offense": offense}
