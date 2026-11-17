"""Regression tests on real stats.ncaa.org pages for contest 6643366 (Ferrum 57, George Washington 94).

Fixtures are sanitized copies (scripts/tokens removed; see ncaa_real_sources.json).
Edits made inside individual tests are labeled; they are not observed NCAA markup.
"""
import contextlib
import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from src.common.sanitize import sanitize_html
from src.common.tables import ParseError
from src.main import main
from src.ncaa.boxscore_parser import parse_individual_stats, parse_team_stats
from src.ncaa.pipeline import COLUMNS, process_bundle, run_pipeline
from src.ncaa.real_game import build_bundle
from src.ncaa.roster_parser import parse_ncaa_roster

FIXTURES = Path(__file__).parent / "fixtures"
SCOREBOARD = (FIXTURES / "ncaa_scoreboard_mbb_d1_2026-11-16.sanitized.html",
              "https://stats.ncaa.org/season_divisions/18983/livestream_scoreboards?game_date=11%2F16%2F2026&conference_id=0")
INDIVIDUAL = (FIXTURES / "ncaa_individual_stats_6643366.sanitized.html",
              "https://stats.ncaa.org/contests/6643366/individual_stats")
BOX = (FIXTURES / "ncaa_box_score_6643366.sanitized.html", "https://stats.ncaa.org/contests/6643366/box_score")
GW_ROSTER = (FIXTURES / "ncaa_roster_627529.sanitized.html", "https://stats.ncaa.org/teams/627529/roster")
FERRUM_ROSTER = (FIXTURES / "ncaa_roster_627884.sanitized.html", "https://stats.ncaa.org/teams/627884/roster")
STAMP = "2026-11-17T08:00:00+00:00"


def page(pair):
    return pair[0].read_bytes(), pair[1]


def bundle(**overrides):
    arguments = dict(scoreboard=page(SCOREBOARD), individual_stats=page(INDIVIDUAL), box_score=page(BOX),
                     rosters=[page(GW_ROSTER), page(FERRUM_ROSTER)], extracted_at=STAMP)
    return build_bundle(**{**arguments, **overrides})


class IndividualStatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDIVIDUAL[0].read_text(encoding="utf-8")
        cls.game = parse_individual_stats(cls.html, source_url=INDIVIDUAL[1])
        cls.teams = {team["team_name"]: team for team in cls.game["teams"]}

    def test_game_header(self):
        self.assertEqual(self.game["contest_id"], "6643366")
        self.assertEqual((self.game["game_date"], self.game["start_time_local"]), ("2026-11-16", "12:00 PM"))
        self.assertEqual(self.game["venue"], "Charles E. Smith Center (Washington, DC)")
        self.assertEqual(self.game["attendance"], 1737)
        self.assertEqual([(t["team_name"], t["team_id"], t["final_score"]) for t in self.game["teams"]],
                         [("Ferrum", "627884", 57), ("George Washington", "627529", 94)])

    def test_player_rows_exclude_team_and_totals(self):
        self.assertEqual(len(self.teams["Ferrum"]["players"]), 16)
        self.assertEqual(len(self.teams["George Washington"]["players"]), 14)
        for team in self.game["teams"]:
            names = {player["player_name"] for player in team["players"]}
            self.assertNotIn("TEAM", names)
            self.assertNotIn(team["team_name"], names)
            self.assertTrue(all(player["player_id"].isdigit() for player in team["players"]))
            self.assertEqual(team["totals"]["minutes"], "200:00")
            self.assertEqual(team["team_row"]["stats"]["reb"], 1)
        self.assertEqual(self.game["issues"], [])

    def test_player_points_sum_to_final_scores(self):
        for name, score in (("Ferrum", 57), ("George Washington", 94)):
            team = self.teams[name]
            self.assertEqual(sum(player["stats"]["pts"] for player in team["players"]), score)
            self.assertEqual(team["totals"]["stats"]["pts"], score)

    def test_known_player_lines(self):
        players = {p["player_name"]: p for team in self.game["teams"] for p in team["players"]}
        witherspoon = players["Omari Witherspoon"]
        self.assertEqual((witherspoon["player_id"], witherspoon["jersey_number"], witherspoon["position"],
                          witherspoon["minutes"]), ("12145040", "0", "G", "13:27"))
        self.assertEqual({key: witherspoon["stats"][key] for key in ("pts", "fgm", "fga", "fg3m", "fg3a", "reb", "ast")},
                         {"pts": 12, "fgm": 5, "fga": 6, "fg3m": 2, "fg3a": 3, "reb": 4, "ast": 2})
        mcduffie = players["Myles McDuffie"]
        self.assertEqual((mcduffie["stats"]["ftm"], mcduffie["stats"]["fta"], mcduffie["stats"]["pts"]), (6, 6, 10))
        self.assertEqual(players["Promis N'Landu"]["player_id"], "12145035")
        self.assertIsNone(players["Dezmon Ainsworth"]["position"])
        self.assertEqual(players["Jalen Rougier-Roane"]["extra"]["dq"], 1)

    def test_header_drift_is_rejected(self):
        # Test-only edit: rename a column so the fixed header no longer matches.
        drifted = self.html.replace(">3FGA<", ">3PA<", 1)
        with self.assertRaisesRegex(ParseError, "Unexpected individual-stats header"):
            parse_individual_stats(drifted, source_url=INDIVIDUAL[1])

    def test_source_url_must_match_contest_page(self):
        with self.assertRaises(ParseError):
            parse_individual_stats(self.html, source_url="https://stats.ncaa.org/contests/6643367/individual_stats")
        with self.assertRaises(ParseError):
            parse_individual_stats(self.html, source_url="https://stats.ncaa.org/contests/6643366/box_score")

    def test_team_stats_panel(self):
        panel = parse_team_stats(BOX[0].read_bytes(), source_url=BOX[1])
        self.assertEqual(panel["stats"]["Rebounds"], {"Ferrum": 22, "George Washington": 50})
        self.assertEqual(panel["stats"]["Field Goal %"], {"Ferrum": 32.69, "George Washington": 54.55})


class RosterTests(unittest.TestCase):
    def test_george_washington_roster(self):
        roster = parse_ncaa_roster(GW_ROSTER[0].read_bytes(), source_url=GW_ROSTER[1])
        self.assertEqual((roster["team_id"], roster["team_name"], roster["season"], roster["sport"], roster["ncaa_season_id"]),
                         ("627529", "George Washington", "2026-27", "MBB", "17240"))
        self.assertEqual(len(roster["players"]), 16)
        phillips = next(p for p in roster["players"] if p["player_id"] == "12145258")
        self.assertEqual(phillips, {"player_id": "12145258", "player_name": "Andrew Phillips", "jersey_number": "21",
            "class_year": "Sr.", "position": "F", "height": "6-5", "hometown": "Chester Springs, PA",
            "high_school": "Malvern Prep", "games_played": 5, "games_started": 0})

    def test_ferrum_roster_keeps_missing_values_missing(self):
        roster = parse_ncaa_roster(FERRUM_ROSTER[0].read_bytes(), source_url=FERRUM_ROSTER[1])
        self.assertEqual((roster["team_id"], roster["team_name"], len(roster["players"])), ("627884", "Ferrum", 16))
        winstead = next(p for p in roster["players"] if p["player_name"] == "MJ Winstead")
        self.assertEqual((winstead["height"], winstead["hometown"], winstead["position"]), ("-", None, None))

    def test_roster_url_must_match_team(self):
        with self.assertRaises(ParseError):
            parse_ncaa_roster(GW_ROSTER[0].read_bytes(), source_url=FERRUM_ROSTER[1])


class RealGamePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = bundle()
        cls.result, cls.quality = process_bundle(copy.deepcopy(cls.bundle))
        cls.rows = [entry["row"] for entry in cls.result]

    def test_preview_label_without_permission_reference(self):
        self.assertEqual(self.bundle["provenance"], "local_validation_preview")
        self.assertIsNone(self.bundle["permission_reference"])
        self.assertEqual(self.quality["source_verification"], "real_html_not_permission_attested")
        self.assertEqual(self.bundle["ncaa_season_id"], "17240")

    def test_rows_and_roster_matches(self):
        self.assertEqual(len(self.rows), 30)
        self.assertEqual(sum(row["team_name"] == "Ferrum" for row in self.rows), 16)
        self.assertEqual(sum(row["team_name"] == "George Washington" for row in self.rows), 14)
        self.assertEqual(self.quality["player_match_counts"], {"matched_id": 30})
        self.assertTrue(all(list(row) == COLUMNS for row in self.rows))
        self.assertEqual(len(COLUMNS), 39)

    def test_points_and_source_checks_pass(self):
        self.assertEqual([(c["team_name"], c["source_score"], c["player_points_sum"], c["status"])
                          for c in self.quality["team_score_checks"]],
                         [("Ferrum", 57, 57, "pass"), ("George Washington", 94, 94, "pass")])
        checks = self.quality["source_checks"]
        self.assertEqual(len(checks), 57)
        self.assertEqual([c for c in checks if c["status"] != "pass"], [])
        names = {c["check"] for c in checks}
        for expected in ("player_points_vs_scoreboard_score", "players_plus_team_row_vs_totals:reb",
                         "totals_vs_box_score_team_stats:Field Goal %", "player_minutes_vs_totals_row"):
            self.assertIn(expected, names)

    def test_biodata_joined_and_unknowns_left_null(self):
        rows = {row["player_name"]: row for row in self.rows}
        phillips = rows["Andrew Phillips"]
        self.assertEqual((phillips["height"], phillips["class_year"], phillips["hometown"], phillips["roster_source_url"]),
                         (195.58, "Senior", "Chester Springs, PA", GW_ROSTER[1]))
        self.assertEqual((rows["MJ Winstead"]["height"], rows["MJ Winstead"]["hometown"]), (None, None))
        self.assertTrue(all(row["division"] is None and row["conference"] is None for row in self.rows))
        self.assertTrue(all(row["boxscore_source_url"] == INDIVIDUAL[1] for row in self.rows))
        self.assertEqual(self.quality["issue_counts"]["missing_conference"], 30)

    def test_stat_mismatch_is_detected(self):
        # Test-only edit: alter one player's points after parsing.
        altered = copy.deepcopy(self.bundle)
        altered["games"][0]["players"][0]["stats"]["pts"] += 1
        _, quality = process_bundle(altered)
        self.assertEqual(quality["team_score_checks"][0]["status"], "fail")

    def test_permission_reference_is_never_defaulted(self):
        authorized = bundle(permission_reference="ticket-123")
        self.assertEqual(authorized["provenance"], "authorized_source")
        preview = copy.deepcopy(self.bundle)
        preview["verification_reference"] = "manual-check"
        with self.assertRaisesRegex(ValueError, "preview cannot carry"):
            process_bundle(preview)

    def test_preview_cannot_be_written_to_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(self.bundle), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "data/exports is reserved"):
                run_pipeline(manifest, Path("data/exports/preview-test"))

    def test_scoreboard_must_list_the_contest(self):
        with self.assertRaisesRegex(ValueError, "not a listed"):
            bundle(scoreboard=(SCOREBOARD[0].read_bytes().replace(b"contest_6643366", b"contest_6643399"), SCOREBOARD[1]))

    def test_roster_for_other_team_rejected(self):
        roster = (GW_ROSTER[0].read_bytes().replace(b'value="627529"', b'value="627530"'),
                  "https://stats.ncaa.org/teams/627530/roster")
        with self.assertRaisesRegex(ValueError, "did not play"):
            bundle(rosters=[roster])


class RealGameCLITests(unittest.TestCase):
    def test_build_command_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch("requests.Session.get") as network:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["build-ncaa-game", "--scoreboard", str(SCOREBOARD[0]), SCOREBOARD[1],
                    "--individual-stats", str(INDIVIDUAL[0]), INDIVIDUAL[1], "--box-score", str(BOX[0]), BOX[1],
                    "--roster", str(GW_ROSTER[0]), GW_ROSTER[1], "--roster", str(FERRUM_ROSTER[0]), FERRUM_ROSTER[1],
                    "--extracted-at", STAMP, "--output", directory])
            network.assert_not_called()
            summary = json.loads(out.getvalue())
            # Exit 2: rows carry missing-division/conference warnings; source checks all pass.
            self.assertEqual(status, 2)
            self.assertEqual((summary["rows"], summary["source_checks"]["failed"]), (30, []))
            path = Path(directory) / "ncaa_player_game_enriched_2026_27_mbb_LOCAL_PREVIEW.csv"
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, COLUMNS)
            self.assertEqual(len(rows), 30)
            self.assertTrue((Path(directory) / "manifest.json").exists())

    def test_plan_processes_games_and_isolates_failures(self):
        with tempfile.TemporaryDirectory() as directory, patch("requests.Session.get") as network:
            good = {"game_id": "6643366", "label": "fixture game", "output": str(Path(directory) / "g1"),
                    "scoreboard": [str(SCOREBOARD[0]), SCOREBOARD[1]], "individual_stats": [str(INDIVIDUAL[0]), INDIVIDUAL[1]],
                    "box_score": [str(BOX[0]), BOX[1]], "extracted_at": STAMP,
                    "rosters": [[str(GW_ROSTER[0]), GW_ROSTER[1]], [str(FERRUM_ROSTER[0]), FERRUM_ROSTER[1]]]}
            missing = {**good, "game_id": "9999999", "label": "pages not saved yet", "output": str(Path(directory) / "g2"),
                       "individual_stats": [str(Path(directory) / "absent.html"), "https://stats.ncaa.org/contests/9999999/individual_stats"]}
            plan = Path(directory) / "plan.json"
            plan.write_text(json.dumps({"schema_version": 1, "affiliations": str(Path(__file__).resolve().parents[1]
                                        / "config" / "ncaa_team_affiliations.json"), "games": [good, missing]}), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["build-ncaa-games", "--plan", str(plan)])
            network.assert_not_called()
            games = json.loads(out.getvalue())["games"]
            self.assertEqual(status, 4)
            self.assertEqual([game["status"] for game in games], [2, 4])
            self.assertEqual((games[0]["rows"], games[0]["source_checks"]["failed"]), (30, []))
            self.assertEqual(games[1]["type"], "FileNotFoundError")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["build-ncaa-games", "--plan", str(plan), "--only", "6643366"]), 2)

    def test_sanitized_fixtures_have_no_tokens_and_are_stable(self):
        for path in (INDIVIDUAL[0], BOX[0], GW_ROSTER[0], FERRUM_ROSTER[0]):
            content = path.read_text(encoding="utf-8")
            for marker in ("csrf-token", "authenticity_token", "BOOMR"):
                self.assertNotIn(marker, content)
            # Commented-out <script> text may remain; no executable script elements do.
            self.assertEqual(BeautifulSoup(content, "html.parser").find_all("script"), [])
        # Sanitizing must not change parsed values (whitespace may differ).
        html = INDIVIDUAL[0].read_text(encoding="utf-8")
        once = parse_individual_stats(html, source_url=INDIVIDUAL[1], parsed_at=STAMP)
        twice = parse_individual_stats(sanitize_html(html), source_url=INDIVIDUAL[1], parsed_at=STAMP)
        self.assertEqual(once["teams"], twice["teams"])
        sources = json.loads((FIXTURES / "ncaa_real_sources.json").read_text(encoding="utf-8"))
        self.assertEqual(len(sources["files"]), 14)
        self.assertTrue(all((FIXTURES / entry["fixture"]).exists() for entry in sources["files"]))


if __name__ == "__main__":
    unittest.main()
