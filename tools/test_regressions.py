"""Deterministic regression checks: python tools/test_regressions.py."""
import itertools
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbo_sim import server
from kbo_sim.atbat import _advance_on_hit
from kbo_sim.data_pipeline import load_league_data
from kbo_sim.game import Game
from kbo_sim.live_session import SeriesSession
from kbo_sim.match import Contestant, Match, MatchGameRecord, award_points, build_series_schedule
from kbo_sim.models import build_team
from kbo_sim.pitch_sequence import generate_pitch_sequence
from kbo_sim.student_api import default_defense_lineup, default_offense_lineup


class PitchSequenceTests(unittest.TestCase):
    def test_counts_and_terminal_pitch_match_result(self):
        for event, seed in itertools.product(("BB", "SO", "HBP", "1B", "2B", "3B", "HR", "OUT"), range(1000)):
            with self.subTest(event=event, seed=seed):
                pitches = generate_pitch_sequence(event, random.Random(seed))
                balls = strikes = 0
                for index, pitch in enumerate(pitches):
                    kind = pitch["kind"]
                    if kind == "ball":
                        balls += 1
                    elif kind in ("called_strike", "swinging_strike"):
                        strikes += 1
                    elif kind == "foul":
                        strikes = min(2, strikes + 1)
                    self.assertEqual((pitch["balls"], pitch["strikes"]), (balls, strikes))
                    self.assertEqual(pitch["seq"], index + 1)
                    if index < len(pitches) - 1:
                        self.assertLess(balls, 4)
                        self.assertLess(strikes, 3)
                        self.assertNotIn(kind, ("hbp", "inplay"))
                if event == "BB":
                    self.assertEqual((balls, pitches[-1]["kind"]), (4, "ball"))
                elif event == "SO":
                    self.assertLess(balls, 4)
                    self.assertEqual(strikes, 3)
                else:
                    self.assertLess(balls, 4)
                    self.assertLess(strikes, 3)
                    self.assertEqual(pitches[-1]["kind"], "hbp" if event == "HBP" else "inplay")


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = load_league_data()
        cls.home = build_team(cls.league, "KT")
        cls.away = build_team(cls.league, "삼성")

    def play_scoring_half(self, hit, inning=9, half="bottom", away_score=0):
        game = Game(self.league, self.home, self.away, {}, seed=42)
        game.current_inning, game.current_half = inning, half
        game.score[self.away.name] = away_score
        batting, fielding = ((self.home, self.away) if half == "bottom"
                              else (self.away, self.home))
        outcomes = iter(["BB", "BB", "BB", hit, "SO", "SO", "SO"])
        def probabilities(*args):
            return {next(outcomes): 1.0}
        with patch("kbo_sim.atbat.resolve_pa_probabilities", probabilities), \
             patch("kbo_sim.atbat.maybe_attempt_steal", return_value=None), \
             patch("kbo_sim.atbat.maybe_attempt_bunt", return_value=False):
            game._play_at_bats(inning, half, batting, fielding,
                               default_defense_lineup(fielding), default_offense_lineup(batting), 0)
        game._advance_schedule()
        return game

    def test_walkoff_hits_score_only_winning_run(self):
        for hit in ("1B", "2B", "3B"):
            with self.subTest(hit=hit):
                game = self.play_scoring_half(hit)
                pa = [e for e in game.events if e["type"] == "pa_result"]
                self.assertEqual(len(pa), 4)
                self.assertEqual(game.result["home_score"], 1)
                self.assertEqual(pa[-1]["runs"], 1)
                self.assertEqual(pa[-1]["rbi"], 1)
                self.assertEqual(pa[-1]["event"], "1B")
                self.assertEqual(sum(p is not None for p in pa[-1]["bases"].values()), 3)
                self.assertEqual(game.events[-2]["runs"], 1)
                self.assertTrue(any(e["type"] == "walkoff" for e in game.events))

    def test_walkoff_from_behind_includes_tying_run(self):
        game = self.play_scoring_half("3B", away_score=1)
        pa = [e for e in game.events if e["type"] == "pa_result"][-1]
        self.assertEqual((game.result["home_score"], game.result["away_score"]), (2, 1))
        self.assertEqual((pa["runs"], pa["rbi"], pa["event"]), (2, 2, "2B"))
        self.assertEqual(sum(p is not None for p in pa["bases"].values()), 2)

    def test_walkoff_homer_keeps_all_runs(self):
        game = self.play_scoring_half("HR")
        pa = [e for e in game.events if e["type"] == "pa_result"][-1]
        self.assertEqual(game.result["home_score"], 4)
        self.assertEqual((pa["runs"], pa["rbi"], pa["event"]), (4, 4, "HR"))
        self.assertFalse(any(pa["bases"].values()))

    def test_fourth_ball_advances_runners_and_scores_forced_run(self):
        game = self.play_scoring_half("BB", inning=8)
        pa = [e for e in game.events if e["type"] == "pa_result"]
        self.assertEqual([sum(p is not None for p in e["bases"].values()) for e in pa[:4]], [1, 2, 3, 3])
        for result in pa[:4]:
            self.assertEqual(result["bases"][1], result["batter"])
        self.assertEqual((pa[3]["runs"], pa[3]["score"][self.home.name]), (1, 1))
        for index, event in enumerate(game.events):
            if event["type"] == "pitch" and event["balls"] == 4:
                self.assertEqual(game.events[index + 1]["type"], "pa_result")
                self.assertEqual(game.events[index + 1]["event"], "BB")

    def test_non_walkoff_hits_keep_all_runs(self):
        for inning, half in ((8, "bottom"), (9, "top")):
            with self.subTest(inning=inning, half=half):
                game = self.play_scoring_half("3B", inning=inning, half=half)
                pa = [e for e in game.events if e["type"] == "pa_result"]
                self.assertEqual((pa[3]["runs"], pa[3]["rbi"], pa[3]["event"]), (3, 3, "3B"))
                self.assertEqual(len(pa), 7)
                self.assertFalse(any(e["type"] == "walkoff" for e in game.events))

    def test_hit_runner_conservation_for_all_base_states(self):
        for occupied, gain, limit, seed in itertools.product(
                itertools.product((False, True), repeat=3), (1, 2, 3), (None, 1, 2, 3), range(20)):
            bases = {b: (b * 100 if occupied[b - 1] else None) for b in (1, 2, 3)}
            runs, rbi = _advance_on_hit(bases, gain, 999, random.Random(seed), lambda _: 0.5, limit)
            remaining = [p for p in bases.values() if p is not None]
            self.assertEqual(sum(occupied) + 1, runs + len(remaining))
            self.assertEqual(len(remaining), len(set(remaining)))
            self.assertEqual(runs, rbi)
            if limit is not None:
                self.assertLessEqual(runs, limit)

    def test_uploads_preserve_identical_and_sanitized_filenames(self):
        handler = object.__new__(server.Handler)
        responses = []
        handler._json = lambda value, code=200: responses.append(value)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(server, "UPLOAD_DIR", tmp), \
             patch.object(server, "APP", SimpleNamespace(league=self.league, timeout_sec=10)), \
             patch.object(server, "full_check", side_effect=lambda *args: {"ok": True}):
            paths = []
            for name, content in (("solution.py", "# A"), ("solution.py", "# B"),
                                  ("a?.py", "# C"), ("a*.py", "# D")):
                handler._api_upload({"filename": name, "content": content})
                paths.append(Path(responses[-1]["result"]["path"]))
            self.assertEqual(len(set(paths)), 4)
            self.assertEqual([p.read_text(encoding="utf-8") for p in paths], ["# A", "# B", "# C", "# D"])
            self.assertEqual(paths[0].name, "solution.py")

    def test_ids_survive_team_and_home_swaps(self):
        a, b = Contestant("동명", "a.py", "KT"), Contestant("동명", "b.py", "삼성")
        for seed in range(30):
            schedule = build_series_schedule(a, b, random.Random(seed))
            records = []
            for sched in schedule:
                for c in (sched.home, sched.away):
                    self.assertEqual(c.algo_path, {"A": "a.py", "B": "b.py"}[c.participant_id])
                winner = next(c.team_name for c in (sched.home, sched.away) if c.participant_id == "A")
                records.append(MatchGameRecord(sched.game_no, "동명", "동명", sched.home.team_name,
                    sched.away.team_name, {"winner": winner},
                    home_id=sched.home.participant_id, away_id=sched.away.participant_id))
            self.assertEqual(award_points(records, "A", "B"), ({"A": 3.0, "B": 0.0}, "A"))
            records[0].result["winner"] = None
            self.assertEqual(award_points(records, "A", "B"), ({"A": 2.5, "B": 0.5}, "A"))
            for record in records:
                record.result["winner"] = None
            self.assertEqual(award_points(records, "A", "B"), ({"A": 1.5, "B": 1.5}, None))

    def test_live_and_batch_keep_separate_same_name_scores(self):
        a, b = Contestant("동명", "a.py", "KT"), Contestant("동명", "b.py", "삼성")
        def finish_for_a(game):
            winning_team = next(t for t, path in game.algo_path.items() if path == "a.py")
            game.score[winning_team] = 1
            game._finalize(9)
            return game.result
        with tempfile.TemporaryDirectory() as tmp:
            session = SeriesSession(self.league, a, b, seed=9, output_dir=tmp)
            for _ in range(3):
                meta = session.start_next_game()
                self.assertEqual({meta["home"]["student_id"], meta["away"]["student_id"]}, {"A", "B"})
                finish_for_a(session.current_game)
                session._record_finished_game()
                session._record_finished_game()  # Retry must not award twice.
            live = session.full_state()["series"]
            with patch.object(Game, "run", finish_for_a):
                batch = Match(self.league, a, b, seed=9, export_json=False).run_series()
            self.assertEqual(live["points"], {"A": 3.0, "B": 0.0})
            self.assertEqual(batch.score, live["points"])
            self.assertEqual(batch.winner, live["series_winner_so_far"])
            self.assertEqual(batch.students, {"A": "동명", "B": "동명"})
            self.assertEqual(live["student_ids"], ["A", "B"])
            self.assertEqual(len(live["records"]), 3)
            self.assertEqual(live["records"][1]["home_id"], "A")
            json.dumps(live, allow_nan=False)

    def test_real_game_batch_and_step_results_match(self):
        def fixed_decision(game, team, opponent, inning, start_index, opp_pitcher, opp_catcher):
            defense = default_defense_lineup(team)
            return list(defense[:9]), defense
        # Only lineup selection is fixed; all at-bats, fatigue and game transitions are real.
        with patch.object(Game, "_decide", fixed_decision):
            for seed in (42, 4242, 9000):
                batch = Game(self.league, self.home, self.away, {}, seed=seed)
                step = Game(self.league, self.home, self.away, {}, seed=seed)
                expected = batch.run()
                while not step.finished:
                    step.prepare_next_inning()
                    step.play_prepared_half()
                self.assertEqual(expected, step.result)
                self.assertEqual(batch.events, step.events)

    def test_locked_roster_survives_half_switch_and_repeated_prepare(self):
        from kbo_sim.student_api import DecisionOutcome
        calls = []
        def decision(path, module, *, my_team, context, **kwargs):
            team = self.home if path == "home" else self.away
            self.assertEqual(len(my_team), len(team.batter_pcodes) + len(team.pitcher_pcodes))
            defense = default_defense_lineup(team)
            offense = list(reversed(defense[:9]))
            calls.append(path)
            return DecisionOutcome("ok", 0.01, {"offense": offense, "defense": defense}, None)
        game = Game(self.league, self.home, self.away, {"KT": "home", "삼성": "away"}, seed=88)
        with patch("kbo_sim.game.run_student_decision", decision):
            prepared = game.prepare_next_inning()
            self.assertEqual(len(calls), 2)
            original = json.loads(json.dumps(game._pending))
            game.prepare_next_inning()
            top = game.play_prepared_half()
            game.prepare_next_inning()
            bottom = game.play_prepared_half()
            self.assertEqual(len(calls), 2)
        for result in (top, bottom):
            event = next(e for e in result["events"] if e["type"] == "half_start")
            bat = original["lineups"][event["batting_team"]]
            defense = original["lineups"][event["fielding_team"]]
            self.assertEqual([p["pCode"] for p in event["batting_order"]], bat["offense"])
            self.assertEqual(set(bat["offense"]), set(bat["defense"][:9]))
            self.assertEqual([p["pCode"] for p in event["batting_roster"]], bat["defense"])
            self.assertEqual([p["pCode"] for p in event["defense"]], defense["defense"])
        self.assertEqual(len(prepared["events"]), 3)

    def test_offense_must_be_a_permutation_of_defense_nine(self):
        from kbo_sim.student_api import DecisionOutcome
        game = Game(self.league, self.home, self.away, {}, seed=88)
        game.algo_path[self.home.name] = "test"
        good_def = default_defense_lineup(self.home)
        outsider = next(p for p in self.home.batter_pcodes if p not in good_def)
        good_off = list(reversed(good_def[:9]))
        # offense가 defense의 앞 9명이 아니면 규칙 위반 -> 폴백
        bad = {"defense": good_def, "offense": [outsider] + list(good_def[:8])}
        for status, lineups in (("ok", bad), ("timeout", None), ("error", None)):
            game.prev_lineup[self.home.name] = {"offense": good_off, "defense": good_def}
            with patch("kbo_sim.game.run_student_decision",
                       return_value=DecisionOutcome(status, 0.01, lineups, "fixture")):
                offense, defense = game._decide(self.home, self.away, 2, 0, None, None)
            self.assertEqual(offense, good_off)
            self.assertNotIn(outsider, offense)
            self.assertEqual(set(offense), set(defense[:9]))
        # 폴백할 직전 명단도 없으면 기본 명단으로
        game.prev_lineup[self.home.name] = {"offense": None, "defense": None}
        with patch("kbo_sim.game.run_student_decision",
                   return_value=DecisionOutcome("timeout", 0.01, None, "fixture")):
            offense, defense = game._decide(self.home, self.away, 2, 0, None, None)
        self.assertEqual(defense, default_defense_lineup(self.home))
        self.assertEqual(offense, list(defense[:9]))

    def test_new_inning_can_change_roster_and_defense_failure_reuses_it(self):
        from kbo_sim.student_api import DecisionOutcome
        game = Game(self.league, self.home, self.away, {"KT": "home", "삼성": "away"}, seed=6)
        def decision(path, module, *, context, **kwargs):
            team = self.home if path == "home" else self.away
            lineup = list(default_defense_lineup(team))
            if context["inning"] == 2:
                lineup[8] = next(p for p in team.batter_pcodes if p not in lineup)
                lineup[9] = team.pitcher_pcodes[1]
            if context["inning"] == 3:
                return DecisionOutcome("timeout", 0.01, None, "fixture")
            return DecisionOutcome("ok", 0.01, {"defense": lineup, "offense": list(lineup[:9])}, None)
        snapshots = []
        with patch("kbo_sim.game.run_student_decision", decision):
            for _ in range(3):
                game.prepare_next_inning()
                snapshots.append({name: list(info["defense"]) for name, info in game._pending["lineups"].items()})
                game.play_prepared_half()
                game.play_prepared_half()
        for name in (self.home.name, self.away.name):
            self.assertNotEqual(snapshots[0][name], snapshots[1][name])
            self.assertEqual(snapshots[1][name], snapshots[2][name])

    def test_upload_check_rejects_offense_not_matching_defense(self):
        from kbo_sim.student_check import full_check
        from kbo_sim.student_api import DecisionOutcome
        selected = default_defense_lineup(self.home)
        outsider = next(p for p in self.home.batter_pcodes if p not in selected)
        calls = []
        def decision(path, module, *, my_team, context, **kwargs):
            calls.append(1)
            return DecisionOutcome("ok", 0.01,
                {"defense": selected, "offense": [outsider] + list(selected[:8])}, None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.py"
            path.write_text("def decide_lineup(my_team, opponent_team, matchups, context, rng):\n    return {}\n", encoding="utf-8")
            with patch("kbo_sim.student_check.run_student_decision", decision):
                report = full_check(str(path), self.league, "KT", "삼성")
        self.assertEqual(len(calls), 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["smoke"]["cases"][0]["status"], "invalid")

    def test_bundled_examples_keep_roster_for_every_team(self):
        from kbo_sim.student_api import DecisionOutcome, load_student_module
        from kbo_sim.student_check import full_check
        def direct(path, module, *, timeout_sec, **kwargs):
            result = load_student_module(path, module).decide_lineup(**kwargs)
            return DecisionOutcome("ok", 0.0, result, None)
        examples = Path(__file__).resolve().parents[1] / "examples"
        teams = ["삼성", "KT", "LG", "KIA", "두산", "NC", "롯데", "SSG", "한화", "키움"]
        with patch("kbo_sim.student_check.run_student_decision", direct):
            for path in sorted(examples.glob("*.py")):
                for i, team in enumerate(teams):
                    with self.subTest(example=path.name, team=team):
                        report = full_check(str(path), self.league, team, teams[(i + 1) % len(teams)])
                        self.assertTrue(report["ok"], report)
                        self.assertEqual(len(report["smoke"]["cases"]), 1)

    def test_legacy_distinct_name_records(self):
        r = MatchGameRecord(1, "Alice", "Bob", "KT", "삼성", {"winner": "KT"})
        self.assertEqual(award_points([r], "Alice", "Bob"), ({"Alice": 1.0, "Bob": 0.0}, "Alice"))
        with self.assertRaises(ValueError):
            award_points([], "same", "same")


if __name__ == "__main__":
    unittest.main(verbosity=2)
