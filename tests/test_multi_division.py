"""Real D-II and D-III games: contest 6631754 (Ferrum 74 at Fairmont St. 94, 2026-11-15)
and contest 6617400 (Bowdoin 63 at UChicago 87, 2026-11-14).

Fixtures are sanitized copies of operator-saved stats.ncaa.org pages (see
ncaa_real_sources.json). The UChicago roster was first saved as the wrong page and
later replaced with the real /teams/628193/roster; the Bowdoin-only case is kept to
show that a missing roster leaves players unmatched instead of guessed.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.ncaa.affiliations import apply_affiliations, load_affiliations
from src.ncaa.pipeline import COLUMNS, process_bundle
from src.ncaa.real_game import build_bundle
from src.ncaa.roster_parser import parse_ncaa_roster
from src.ncaa.scoreboard_parser import parse_scoreboard
from tests.test_real_game import FERRUM_ROSTER, FIXTURES, STAMP, bundle as d1_bundle

ROOT = Path(__file__).resolve().parents[1]
MAPPING = ROOT / "config" / "ncaa_team_affiliations.json"
D2_BOARD = (FIXTURES / "ncaa_scoreboard_mbb_d2_2026-11-15.sanitized.html",
            "https://stats.ncaa.org/season_divisions/18984/livestream_scoreboards?game_date=11%2F15%2F2026&conference_id=0")
D3_BOARD = (FIXTURES / "ncaa_scoreboard_mbb_d3_2026-11-14.sanitized.html",
            "https://stats.ncaa.org/season_divisions/18985/livestream_scoreboards?game_date=11%2F14%2F2026&conference_id=0")


def page(path, url):
    return Path(path).read_bytes(), url


def contest_pages(contest_id):
    return dict(individual_stats=page(FIXTURES / f"ncaa_individual_stats_{contest_id}.sanitized.html",
                                      f"https://stats.ncaa.org/contests/{contest_id}/individual_stats"),
                box_score=page(FIXTURES / f"ncaa_box_score_{contest_id}.sanitized.html",
                               f"https://stats.ncaa.org/contests/{contest_id}/box_score"))


def roster(team_id):
    return page(FIXTURES / f"ncaa_roster_{team_id}.sanitized.html", f"https://stats.ncaa.org/teams/{team_id}/roster")


def processed(game_bundle):
    apply_affiliations(game_bundle, load_affiliations(MAPPING))
    result, quality = process_bundle(game_bundle)
    return [entry["row"] for entry in result], quality


class ScoreboardTests(unittest.TestCase):
    def test_division_scoreboards(self):
        for (path, url), division, division_id, date, count in ((D2_BOARD, "II", "18984", "2026-11-15", 20),
                                                              (D3_BOARD, "III", "18985", "2026-11-14", 150)):
            meta = parse_scoreboard(path.read_bytes(), source_url=url)
            self.assertEqual((meta["provenance"]["division"], meta["provenance"]["season_division_id"],
                              meta["provenance"]["ncaa_season_id"], meta["provenance"]["scoreboard_date"]),
                             (division, division_id, "17240", date))
            self.assertEqual((meta["counts"]["contests_listed"], meta["counts"]["completed"]), (count, count))
        d3 = parse_scoreboard(D3_BOARD[0].read_bytes(), source_url=D3_BOARD[1])
        self.assertEqual(d3["counts"]["teams_without_team_page"], 13)

    def test_contest_must_be_on_the_given_scoreboard(self):
        with self.assertRaisesRegex(ValueError, "not a listed"):
            build_bundle(scoreboard=page(*D2_BOARD), **contest_pages("6617400"), rosters=[roster("628171")],
                         extracted_at=STAMP)


class DivisionTwoGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle(scoreboard=page(*D2_BOARD), **contest_pages("6631754"),
                                  rosters=[roster("627590"), page(*FERRUM_ROSTER)], extracted_at=STAMP)
        cls.rows, cls.quality = processed(copy.deepcopy(cls.bundle))

    def test_rows_matches_and_points(self):
        self.assertEqual(len(self.rows), 23)
        self.assertEqual(sum(row["team_name"] == "Ferrum" for row in self.rows), 13)
        self.assertEqual(sum(row["team_name"] == "Fairmont St." for row in self.rows), 10)
        self.assertEqual(self.quality["player_match_counts"], {"matched_id": 23})
        self.assertEqual([(c["team_name"], c["source_score"], c["player_points_sum"], c["status"])
                          for c in self.quality["team_score_checks"]],
                         [("Ferrum", 74, 74, "pass"), ("Fairmont St.", 94, 94, "pass")])
        self.assertEqual(len(self.quality["source_checks"]), 57)
        self.assertEqual([c for c in self.quality["source_checks"] if c["status"] != "pass"], [])
        self.assertTrue(all(list(row) == COLUMNS for row in self.rows))

    def test_sport_versus_institution_division(self):
        applied = {entry["team_name"]: entry for entry in load_affiliations(MAPPING)["entries"].values()}
        self.assertEqual((applied["Ferrum"]["division"], applied["Ferrum"]["institution_division"]), ("II", "III"))
        self.assertEqual((applied["Fairmont St."]["division"], applied["Fairmont St."]["institution_division"]), ("II", "II"))
        for row in self.rows:
            if row["team_name"] == "Fairmont St.":
                self.assertEqual((row["division"], row["conference"], row["validation_status"]),
                                 ("II", "Mountain East Conference", "PASS"))
            else:
                self.assertEqual((row["division"], row["conference"]), ("II", "Conference Carolinas"))
        self.assertEqual(self.quality["issue_counts"]["division_reclassifying"], 13)

    def test_reused_ferrum_roster_gives_consistent_biodata(self):
        d1_rows, _ = processed(d1_bundle())
        first = {row["player_id"]: row for row in d1_rows if row["team_name"] == "Ferrum"}
        shared = [row for row in self.rows if row["team_name"] == "Ferrum" and row["player_id"] in first]
        self.assertTrue(shared)
        for row in shared:
            for field in ("player_name", "height", "class_year", "hometown", "roster_source_url"):
                self.assertEqual(row[field], first[row["player_id"]][field])


class DivisionThreeGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle(scoreboard=page(*D3_BOARD), **contest_pages("6617400"),
                                  rosters=[roster("628171"), roster("628193")], extracted_at=STAMP)
        cls.rows, cls.quality = processed(copy.deepcopy(cls.bundle))

    def test_rows_and_points(self):
        self.assertEqual(len(self.rows), 24)
        self.assertEqual(sum(row["team_name"] == "Bowdoin" for row in self.rows), 11)
        self.assertEqual(sum(row["team_name"] == "UChicago" for row in self.rows), 13)
        self.assertEqual([(c["team_name"], c["source_score"], c["player_points_sum"], c["status"])
                          for c in self.quality["team_score_checks"]],
                         [("Bowdoin", 63, 63, "pass"), ("UChicago", 87, 87, "pass")])
        self.assertEqual([c for c in self.quality["source_checks"] if c["status"] != "pass"], [])
        self.assertEqual(len(self.quality["source_checks"]), 57)

    def test_all_players_match_by_id_and_pass(self):
        self.assertEqual(self.quality["player_match_counts"], {"matched_id": 24})
        self.assertEqual(self.quality["validation_counts"], {"PASS": 24})
        self.assertEqual(self.quality["issue_counts"], {})
        rows = {row["player_name"]: row for row in self.rows}
        smith = rows["Luke Smith"]
        self.assertEqual((smith["player_id"], smith["jersey_number"], smith["height"], smith["class_year"],
                          smith["hometown"], smith["roster_source_url"]),
                         ("11483954", "2", 205.74, "Senior", "Richmond, VA", "https://stats.ncaa.org/teams/628193/roster"))
        self.assertEqual(sum(row["points"] for row in self.rows if row["team_name"] == "UChicago"), 87)

    def test_uchicago_roster_fixture(self):
        html, url = roster("628193")
        parsed = parse_ncaa_roster(html, source_url=url)
        self.assertEqual((parsed["team_id"], parsed["team_name"], parsed["season"], len(parsed["players"])),
                         ("628193", "UChicago", "2026-27", 15))
        self.assertTrue(all(player["player_id"] for player in parsed["players"]))

    def test_missing_roster_leaves_players_unmatched(self):
        partial = build_bundle(scoreboard=page(*D3_BOARD), **contest_pages("6617400"),
                               rosters=[roster("628171")], extracted_at=STAMP)
        rows, quality = processed(partial)
        self.assertEqual(quality["player_match_counts"], {"matched_id": 11, "unmatched": 13})
        for row in rows:
            if row["team_name"] == "UChicago":
                self.assertEqual(row["player_match_status"], "unmatched")
                self.assertIsNone(row["roster_source_url"])
                self.assertIsNone(row["height"])
                self.assertTrue(row["player_id"].isdigit())

    def test_division_three_affiliations_use_canonical_names(self):
        expected = {"Bowdoin": "New England Small College Athletic Conference", "UChicago": "University Athletic Association"}
        for row in self.rows:
            self.assertEqual((row["division"], row["conference"]), ("III", expected[row["team_name"]]))
            self.assertTrue(row["conference_source_url"].startswith("https://web3.ncaa.org/directory/orgDetail?id="))


class AliasRuleTests(unittest.TestCase):
    def edited(self, edit):
        data = json.loads(MAPPING.read_text(encoding="utf-8"))
        edit({entry["team_id"]: entry for entry in data["affiliations"]})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "mapping.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_affiliations(path)

    def test_undeclared_abbreviation_is_a_conflict(self):
        with self.assertRaisesRegex(ValueError, "conflicting conference"):
            self.edited(lambda entries: entries["628171"].pop("conference_aliases"))

    def test_directory_must_use_canonical_name(self):
        def alias_in_directory(entries):
            entries["628193"]["evidence"][0]["observed"]["conference"] = "UAA"
        with self.assertRaisesRegex(ValueError, "canonical|must equal"):
            self.edited(alias_in_directory)


class PlanFileTests(unittest.TestCase):
    def test_plan_lists_three_real_games_consistently(self):
        plan = json.loads((ROOT / "config" / "ncaa_games.json").read_text(encoding="utf-8"))
        games = {game["game_id"]: game for game in plan["games"]}
        self.assertEqual(set(games), {"6643366", "6631754", "6617400"})
        for game_id, game in games.items():
            self.assertTrue(game["individual_stats"][1].endswith(f"/contests/{game_id}/individual_stats"))
            self.assertTrue(game["box_score"][1].endswith(f"/contests/{game_id}/box_score"))
            self.assertEqual(game["output"], f"data/processed/ncaa-game-{game_id}")
            self.assertTrue(all("/teams/" in url and url.endswith("/roster") for _, url in game["rosters"]))


if __name__ == "__main__":
    unittest.main()
