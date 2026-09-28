"""
broadcast_export.py
--------------------
Game 실행 후 쌓인 이벤트 로그를 문자중계 뷰어(viewer/broadcast_viewer.html)가 읽을 수 있는
JSON 스키마로 변환한다. 뷰어는 이 JSON을 파일로 불러와("파일 선택") 재생한다.

⚠️ 도루 선호/실책 배수 등 traits.py에서 만든 "합성 성향치"는 여기서 절대 노출하지 않는다
(실제 KBO 기록으로 오인될 수 있으므로). 노출되는 건 시뮬레이션 결과(안타/삼진/도루 성공여부 등)와
실제 원본 기록 기반 정적 프로필(이름/포지션/등번호/시즌 스탯)뿐이다.
"""
from __future__ import annotations

import json
import math
from typing import Dict

from .data_pipeline import LeagueData
from .game import Game
from .models import Team


def _json_safe(obj):
    """dict/list를 재귀적으로 순회하며 numpy 스칼라, NaN/Inf 등 표준 JSON(및 JS JSON.parse)이
    받아들이지 못하는 값을 안전한 값으로 변환한다. (실제로 batters CSV의 backNo 등 일부
    컬럼에 결측치가 있어 NaN이 섞여 들어옴 - Python json 모듈은 NaN을 허용하지만 그 결과물은
    표준 JSON이 아니어서 브라우저의 JSON.parse가 파싱을 거부한다.)"""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if hasattr(obj, "item"):  # numpy scalar (int64/float64/bool_ 등)
        return _json_safe(obj.item())
    return obj

# 구단 상징색. 각 구단이 널리 쓰는 대표색(한화=주황, 롯데=남색 등)을 화면 표시용으로 근사한
# 값이며 공식 브랜드 컬러 코드 그대로는 아니다. 엠블럼도 실제 구단 로고를 재현한 것이 아니라
# 이 색과 구단 약칭으로 만든 오리지널 디자인이다 (실제 로고 이미지를 쓰려면
# viewer/logos/<팀코드>.png|svg 를 넣으면 자동으로 그쪽이 우선 사용됨 - 강사 가이드 참고).
TEAM_COLORS = {
    "삼성": ("#074CA1", "#8FB8E0"), "KT": ("#1A1A1A", "#C8102E"), "LG": ("#C30452", "#111111"),
    "KIA": ("#EA0029", "#06141F"), "두산": ("#131230", "#C8102E"), "NC": ("#315288", "#B39C5A"),
    "롯데": ("#041E42", "#D00F31"), "SSG": ("#CE0E2D", "#FFB81C"), "한화": ("#FF6600", "#1A1A1A"),
    "키움": ("#570514", "#B08D57"),
}


def team_meta(league: LeagueData, team: Team) -> dict:
    primary, secondary = TEAM_COLORS.get(team.name, ("#333333", "#888888"))
    return {
        "name": team.name,
        "full_name": team.full_name,
        "code": team.code,
        "color": primary,
        "color2": secondary,
        "roster": {
            "batters": [
                {"pCode": p, **{k: league.batter(p).get(k) for k in
                                 ("name", "displayId", "backNo", "position", "throwBat")}}
                for p in team.batter_pcodes
            ],
            "pitchers": [
                {"pCode": p, **{k: league.pitcher(p).get(k) for k in
                                 ("name", "displayId", "backNo", "position", "throwBat")}}
                for p in team.pitcher_pcodes
            ],
        },
    }


def export_game(league: LeagueData, game: Game, algo_meta: Dict[str, dict] | None = None) -> dict:
    """algo_meta: {team_name: {"student_name": "...", "file": "..."}} 등 표시용 부가정보(선택)"""
    return {
        "schema": "kbo-owner-game-broadcast/1",
        "home": team_meta(league, game.home),
        "away": team_meta(league, game.away),
        "algo_meta": algo_meta or {},
        "result": game.result,
        "timings": [
            {"inning": t.inning, "team": t.team,
             "status": t.status, "elapsed_sec": round(t.elapsed_sec, 3)}
            for t in game.timings
        ],
        "events": game.events,
    }


def export_game_to_file(league: LeagueData, game: Game, filepath: str, algo_meta=None):
    data = _json_safe(export_game(league, game, algo_meta))
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
    return filepath
