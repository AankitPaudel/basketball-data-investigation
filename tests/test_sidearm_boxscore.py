"""Sidearm school-site box score parser and the school-vs-NCAA comparison (synthetic fixture)."""
import unittest
from pathlib import Path

from src.common.tables import ParseError
from src.school.compare import jersey_relation, minutes_note
from src.school.sidearm_boxscore import MATCH_STATUS, name_match_key, normalize_name, parse_sidearm_boxscore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sidearm_boxscore_synthetic.html"
URL = "https://example.edu/sports/mens-basketball/stats/2026-27/example-tech/boxscore/1"


class NameTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(normalize_name("Maly, Benedek"), ("Benedek Maly", None))
        self.assertEqual(normalize_name("Lapsiwala,Arhan"), ("Arhan Lapsiwala", None))
        self.assertEqual(normalize_name("Baylor, Jr., Corey"), ("Corey Baylor Jr.", None))
        self.assertEqual(normalize_name("Smith, III, Pat"), ("Pat Smith III", None))
        self.assertEqual(normalize_name("Prince"), ("Prince", "unrecognized_name_format"))
        self.assertEqual(normalize_name("A, B, C"), ("A, B, C", "unrecognized_name_format"))

    def test_match_key_shared_by_both_sources(self):
        self.assertEqual(name_match_key("Corey Baylor Jr."), name_match_key("Corey Baylor, Jr."))
        self.assertNotEqual(name_match_key("Benedek Maly"), name_match_key("Bendek Maly"))  # no fuzzy matching

    def test_jersey_relation(self):
        self.assertEqual(jersey_relation("5", "05"), "leading_zero")
        self.assertEqual(jersey_relation("0", "00"), "zero_vs_double_zero")
        self.assertEqual(jersey_relation("12", "12"), "same")
        self.assertIsNone(jersey_relation("12", "21"))

    def test_minutes_note(self):
        self.assertEqual(minutes_note("17:32", 18)["school_equals"], "round")
        self.assertEqual(minutes_note("17:50", 17)["school_equals"], "floor")
        self.assertEqual(minutes_note("22:29", 23)["school_equals"], "ceil")


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE.read_text(encoding="utf-8")
        cls.result = parse_sidearm_boxscore(cls.html, URL)

    def test_teams_players_and_team_rows(self):
        teams = self.result["teams"]
        self.assertEqual([(t["team_name"], t["caption_score"], len(t["players"])) for t in teams],
                         [("Example College", 19, 2), ("Example Tech", 18, 2)])
        self.assertEqual(teams[0]["team_row"]["stats"]["oreb"], 1)
        self.assertNotIn("TEAM", [p["player_name_raw"] for t in teams for p in t["players"]])

    def test_player_fields(self):
        river, stone = self.result["teams"][0]["players"]
        lake = self.result["teams"][1]["players"][0]
        self.assertEqual((river["jersey_number"], river["player_name"], river["school_player_id"]), ("05", "Sam River", "101"))
        self.assertEqual(stone["player_name"], "Alex Stone Jr.")
        self.assertEqual((lake["jersey_number"], lake["player_name"], lake["school_player_id"]), ("00", "Jordan Lake", None))
        self.assertEqual(river["stats"], {"pts": 13, "reb": 4, "ast": 3, "oreb": 1, "dreb": 3, "stl": 1, "blk": 0,
            "tov": 1, "pf": 2, "fgm": 5, "fga": 9, "fg3m": 2, "fg3a": 4, "ftm": 1, "fta": 2})
        for team in self.result["teams"]:
            for player in team["players"]:
                self.assertIsNone(player["player_id"])
                self.assertEqual(player["player_match_status"], MATCH_STATUS)
                self.assertEqual(player["minutes_precision"], "whole_minutes")
                self.assertEqual(player["parser_issues"], [])

    def test_source_checks_and_details(self):
        self.assertEqual([c for c in self.result["source_checks"] if c["status"] == "fail"], [])
        self.assertEqual((self.result["game_date"], self.result["attendance"]), ("2027-01-10", 1234))
        self.assertFalse(self.result["ncaa_player_id_available"])

    def test_totals_mismatch_is_reported(self):
        html = self.html.replace("<td>8-15</td>", "<td>9-15</td>", 1)
        failed = [c["check"] for c in parse_sidearm_boxscore(html, URL)["source_checks"] if c["status"] == "fail"]
        self.assertEqual(failed, ["players_plus_team_vs_totals:fgm"])

    def test_rebound_pair_inconsistency_flagged(self):
        html = self.html.replace("<td>1-3</td><td>4</td>", "<td>1-3</td><td>5</td>", 1)
        river = parse_sidearm_boxscore(html, URL)["teams"][0]["players"][0]
        self.assertIn("rebounds_orb_drb_differ_from_reb", river["parser_issues"])

    def test_layout_drift_stops(self):
        with self.assertRaisesRegex(ParseError, "header"):
            parse_sidearm_boxscore(self.html.replace("<th scope=\"col\">ORB-DRB</th>", "<th scope=\"col\">OR</th>"), URL)
        with self.assertRaisesRegex(ParseError, "n-m"):
            parse_sidearm_boxscore(self.html.replace("<td>5-9</td>", "<td>5/9</td>", 1), URL)
        with self.assertRaisesRegex(ParseError, "two player tables"):
            parse_sidearm_boxscore("<html><body><p>Nothing</p></body></html>", URL)


if __name__ == "__main__":
    unittest.main()
