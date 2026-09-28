"""
live_session.py
----------------
라이브(하프이닝 단위) 3연전 진행 관리자.

server.py가 이 클래스를 통해 "다음 이닝 진행" 요청을 처리한다. 경기 일정(대진/시드)은
match.build_series_schedule()을 공유하므로, 같은 시드라면 CLI 배치 채점과 라이브 중계의
결과가 완전히 동일하다.

경기가 끝날 때마다 리플레이용 JSON(output/gameN.json)도 자동 저장한다.
"""
from __future__ import annotations

import os
import random
from typing import List, Optional

from .broadcast_export import export_game_to_file, team_meta
from .data_pipeline import LeagueData
from .game import Game
from .match import Contestant, MatchGameRecord, ScheduledGame, award_points, build_series_schedule
from .models import build_team


class SeriesSession:
    def __init__(self, league: LeagueData, a: Contestant, b: Contestant, seed: Optional[int] = None,
                 timeout_sec: float = 10.0, output_dir: str = "output"):
        self.league = league
        self.a = a
        self.b = b
        self.seed = seed
        self.master_rng = random.Random(seed)
        self.schedule: List[ScheduledGame] = build_series_schedule(a, b, self.master_rng)
        self.timeout_sec = timeout_sec
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.game_index = -1
        self.current_game: Optional[Game] = None
        self.current_sched: Optional[ScheduledGame] = None
        self.records: List[MatchGameRecord] = []

    # ------------------------------------------------------------------
    @property
    def has_next_game(self) -> bool:
        if self.game_index < 0:
            return True
        if self.current_game is not None and not self.current_game.finished:
            return False
        return self.game_index + 1 < len(self.schedule)

    def start_next_game(self) -> dict:
        if self.current_game is not None and not self.current_game.finished:
            raise RuntimeError("현재 경기가 아직 끝나지 않았습니다.")
        if self.game_index + 1 >= len(self.schedule):
            raise RuntimeError("3연전이 모두 끝났습니다.")
        self.game_index += 1
        sched = self.schedule[self.game_index]
        self.current_sched = sched
        home_team = build_team(self.league, sched.home.team_name)
        away_team = build_team(self.league, sched.away.team_name)
        algo_path = {home_team.name: sched.home.algo_path, away_team.name: sched.away.algo_path}
        self.current_game = Game(self.league, home_team, away_team, algo_path, seed=sched.seed,
                                  timeout_sec=self.timeout_sec)
        return self.game_meta()

    def prepare_next_inning(self) -> dict:
        """1단계: 이닝 시작 시 양 팀 학생 알고리즘을 호출해 그 이닝의 공격/수비 명단을 모두 확정한다."""
        if self.current_game is None:
            raise RuntimeError("아직 경기가 시작되지 않았습니다.")
        out = self.current_game.prepare_next_inning()
        out["series"] = self.series_state()
        out["pending"] = self.current_game._pending is not None
        return out

    # 예전 이름 호환
    prepare_next_half = prepare_next_inning

    def play_prepared_half(self) -> dict:
        """2단계: 확정된 명단으로 그 하프이닝의 공격(타석)을 진행한다."""
        if self.current_game is None:
            raise RuntimeError("아직 경기가 시작되지 않았습니다.")
        out = self.current_game.play_prepared_half()
        if self.current_game.finished:
            self._record_finished_game()
        out["series"] = self.series_state()
        out["pending"] = self.current_game._pending is not None
        return out

    def play_next_half(self) -> dict:
        """두 단계를 한 번에 (배치/호환용)."""
        if self.current_game is None:
            raise RuntimeError("아직 경기가 시작되지 않았습니다.")
        out = self.current_game.play_next_half()
        if self.current_game.finished:
            self._record_finished_game()
        out["series"] = self.series_state()
        out["pending"] = self.current_game._pending is not None
        return out

    # ------------------------------------------------------------------
    def _record_finished_game(self):
        sched = self.current_sched
        if any(r.game_no == sched.game_no for r in self.records):
            return  # 이미 기록됨
        game = self.current_game
        json_path = os.path.join(self.output_dir, f"game{sched.game_no}.json")
        algo_meta = {
            game.home.name: {"student_name": sched.home.student_name, "file": sched.home.algo_path},
            game.away.name: {"student_name": sched.away.student_name, "file": sched.away.algo_path},
        }
        try:
            export_game_to_file(self.league, game, json_path, algo_meta)
        except Exception:
            json_path = None
        self.records.append(MatchGameRecord(
            game_no=sched.game_no, home_student=sched.home.student_name,
            away_student=sched.away.student_name, home_team=game.home.name,
            away_team=game.away.name, result=game.result, json_path=json_path,
            home_id=sched.home.participant_id, away_id=sched.away.participant_id))

    # ------------------------------------------------------------------
    def game_meta(self) -> dict:
        g = self.current_game
        s = self.current_sched
        return {
            "game_no": s.game_no,
            "label": s.label,
            "home": {**team_meta(self.league, g.home), "student": s.home.student_name, "student_id": s.home.participant_id},
            "away": {**team_meta(self.league, g.away), "student": s.away.student_name, "student_id": s.away.participant_id},
            "seed": s.seed,
            "timeout_sec": self.timeout_sec,
            "max_innings": g.max_innings,
            "mercy_margin": g.mercy_margin,
            "mercy_min_inning": g.mercy_min_inning,
            "next_inning": g.current_inning,
            "next_half": g.current_half,
            "finished": g.finished,
            # 라인업만 뽑아두고 아직 공격을 진행하지 않은 상태인지 (화면 버튼 결정용)
            "pending": g._pending is not None,
        }

    def series_state(self) -> dict:
        points, winner = award_points(self.records, "A", "B")
        return {
            "students": [self.a.student_name, self.b.student_name],
            "student_ids": ["A", "B"],
            "points": points,
            "series_winner_so_far": winner,
            "game_index": self.game_index,
            "total_games": len(self.schedule),
            "has_next_game": self.has_next_game,
            "records": [{"game_no": r.game_no, "home_id": r.home_id, "away_id": r.away_id, "home_student": r.home_student,
                          "away_student": r.away_student, "home_team": r.home_team,
                          "away_team": r.away_team, "result": r.result, "json_path": r.json_path}
                         for r in self.records],
            "schedule": [{"game_no": s.game_no, "home_id": s.home.participant_id,
                           "away_id": s.away.participant_id, "label": s.label,
                           "home_student": s.home.student_name, "home_team": s.home.team_name,
                           "away_student": s.away.student_name, "away_team": s.away.team_name}
                          for s in self.schedule],
        }

    def full_state(self) -> dict:
        """새로고침/재접속 시 화면을 통째로 복구하기 위한 전체 상태."""
        state = {"series": self.series_state(), "game": None, "events": []}
        if self.current_game is not None:
            state["game"] = self.game_meta()
            state["events"] = self.current_game.events
            state["timings"] = [{"inning": t.inning, "team": t.team,
                                  "status": t.status, "elapsed_sec": round(t.elapsed_sec, 3)}
                                 for t in self.current_game.timings]
        return state
