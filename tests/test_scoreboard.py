"""Tests against a real saved stats.ncaa.org scoreboard (2026-27 MBB D-I, 11/16/2026).

The fixture is the unmodified saved page. Every game on it is Final, so postponed and
incomplete handling is exercised on derived copies whose edits are made (and labeled)
inside the tests; those edited markups are NOT observed NCAA layouts.
"""
import contextlib
import copy
import csv
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common.http_client import safe_url
from src.common.tables import ParseError
from src.main import main
from src.ncaa.discovery import bundle_games, bundle_skeleton, cross_check, write_discovery
from src.ncaa.pipeline import process_bundle
from src.ncaa.scoreboard_parser import parse_scoreboard

FIXTURES = Path(__file__).parent / "fixtures"
REAL = FIXTURES / "ncaa_scoreboard_mbb_d1_2026-11-16.sanitized.html"
URL = ("https://stats.ncaa.org/season_divisions/18983/livestream_scoreboards"
       "?game_date=11%2F16%2F2026&conference_id=0")
UNLINKED = {"Miami-Hamilton", "Fisk", "Virginia-Lynchburg", "Fla. Memorial", "Midway",
            "Calumet Col.", "Mayville St.", "Dallas Christian"}


def derived(html, contest_id, replacement_status=None, clear_scores=False, active=False):
    """Edit one contest's card in a copy of the real page (test-only, not observed markup)."""
    start = html.index(f'<tr id="contest_{contest_id}">')
    # The card table closes after the contest's box-score link (tolerant of formatting).
    end = html.index("</table>", html.index(f"/contests/{contest_id}/box_score", start))
    card = html[start:end]
    if replacement_status is not None:
        card = card.replace("Final", replacement_status)
    if active:
        # Move the status element out of its HTML comment.
        card = re.sub(r"<!-- consider moving this up to the top\s*(<td rowspan=\"2\".*?</td>)\s*-->",
                      r"\1", card, count=1, flags=re.S)
    if clear_scores:
        card = re.sub(r'(<div[^>]*id="score_\d+"[^>]*>)\s*\d+\s*(</div>)', r"\1\2", card)
    return html[:start] + card + html[end:]


class RealScoreboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = REAL.read_bytes()
        # Saved with CRLF; derived copies edit newline-normalized text.
        cls.html = cls.raw.decode("utf-8-sig").replace("\r\n", "\n")
        cls.result = parse_scoreboard(cls.raw, source_url=URL)
        cls.games = {game["contest_id"]: game for game in cls.result["games"]}

    def test_page_identifiers_from_real_html(self):
        meta = self.result["provenance"]
        self.assertEqual(meta["ncaa_season_id"], "17240")
        self.assertEqual(meta["season_division_id"], "18983")
        self.assertEqual(meta["season"], "2026-27")
        self.assertEqual((meta["sport"], meta["division"]), ("MBB", "I"))
        self.assertEqual(meta["scoreboard_date"], "2026-11-16")
        self.assertEqual(meta["page_title"], "2026-27 Men's Basketball D-I Scoreboard")

    def test_all_33_listed_games_are_completed(self):
        counts = self.result["counts"]
        self.assertEqual(counts["contests_listed"], 33)
        self.assertEqual((counts["completed"], counts["incomplete"], counts["excluded"]), (33, 0, 0))
        self.assertEqual(counts["status"], {"final": 33})
        for game in self.result["games"]:
            self.assertEqual(game["game_date"], "2026-11-16")
            self.assertEqual(len(game["teams"]), 2)
            self.assertEqual(game["linescore_check"], "pass")
            self.assertEqual(game["issues"], [])

    def test_example_game_6643366(self):
        game = self.games["6643366"]
        self.assertEqual(game["boxscore_url"], "https://stats.ncaa.org/contests/6643366/box_score")
        self.assertEqual((game["start_time_local"], game["attendance"]), ("12:00 PM", 1737))
        first, second = game["teams"]
        self.assertEqual((first["team_name"], first["team_id"], first["final_score"]), ("Ferrum", "627884", 57))
        self.assertEqual((second["team_name"], second["team_id"], second["final_score"]),
                         ("George Washington", "627529", 94))
        self.assertEqual(second["team_url"], "https://stats.ncaa.org/teams/627529")
        self.assertEqual(second["record_shown"], "5-0")
        self.assertEqual((first["period_scores"], second["period_scores"]), ([26, 31], [42, 52]))
        self.assertTrue(second["marked_winner"])

    def test_urls_follow_observed_structure(self):
        self.assertEqual(len(self.games), 33)
        for contest_id, game in self.games.items():
            self.assertEqual(game["boxscore_url"], f"https://stats.ncaa.org/contests/{contest_id}/box_score")
            for team in game["teams"]:
                if team["team_id"]:
                    self.assertEqual(team["team_url"], f"https://stats.ncaa.org/teams/{team['team_id']}")

    def test_teams_without_ncaa_team_pages(self):
        unlinked = [team for game in self.result["games"] for team in game["teams"] if not team["has_team_page"]]
        self.assertEqual({team["team_name"] for team in unlinked}, UNLINKED)
        self.assertTrue(all(team["team_id"] is None and team["team_url"] is None for team in unlinked))
        fisk, north_alabama = self.games["6656341"]["teams"]
        self.assertEqual((fisk["team_name"], fisk["final_score"], fisk["record_shown"]), ("Fisk", 49, None))
        self.assertEqual((north_alabama["team_id"], north_alabama["final_score"]), ("627271", 87))
        self.assertTrue(self.games["6656341"]["completed"])

    def test_parenthesized_name_keeps_qualifier(self):
        names = {team["team_name"] for game in self.result["games"] for team in game["teams"]}
        self.assertIn("St. Thomas (MN)", names)
        self.assertIn("Saint Mary's (CA)", names)

    def test_provenance_preserved(self):
        meta = self.result["provenance"]
        self.assertEqual(meta["source_url"], URL)
        self.assertEqual(meta["html_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(meta["provenance"], "operator_saved_html")
        self.assertFalse(meta["player_statistics_verified"])
        for game in self.result["games"]:
            self.assertFalse(game["player_statistics_verified"])
            # In this capture the Final status is only present inside an HTML comment.
            self.assertEqual((game["status_text"], game["status_source"]), ("Final", "html_comment"))

    def test_text_and_bytes_give_same_games(self):
        self.assertEqual(parse_scoreboard(self.html, source_url=URL)["games"], self.result["games"])

    def test_derived_postponed_game_is_excluded(self):
        html = derived(self.html, "6643366", replacement_status="Postponed", clear_scores=True)
        result = parse_scoreboard(html, source_url=URL)
        self.assertNotIn("6643366", {game["contest_id"] for game in result["games"]})
        self.assertEqual(result["excluded"][0]["contest_id"], "6643366")
        self.assertEqual(result["excluded"][0]["status"], "postponed")
        self.assertEqual(result["counts"]["games"], 32)
        self.assertNotIn("6643366", {game["game_id"] for game in bundle_games(result)})

    def test_derived_canceled_game_is_excluded(self):
        result = parse_scoreboard(derived(self.html, "6639787", replacement_status="Canceled"), source_url=URL)
        self.assertEqual([entry["status"] for entry in result["excluded"]], ["canceled"])

    def test_derived_incomplete_game_kept_but_not_completed(self):
        html = derived(self.html, "6661678", replacement_status="2nd Half", active=True, clear_scores=True)
        result = parse_scoreboard(html, source_url=URL)
        game = next(game for game in result["games"] if game["contest_id"] == "6661678")
        self.assertEqual((game["status"], game["status_source"], game["completed"]), ("not_final", "active_markup", False))
        self.assertEqual([team["final_score"] for team in game["teams"]], [None, None])
        self.assertEqual((result["counts"]["completed"], result["counts"]["incomplete"]), (32, 1))
        self.assertNotIn("6661678", {entry["game_id"] for entry in bundle_games(result)})

    def test_active_status_overrides_commented_final(self):
        # Hypothetical: active markup says in progress while an old comment still says Final.
        html = re.sub(r'<div class="livestream_status_6661678 livestream_status livestream_game_over">\s*Final',
                      '<div class="livestream_status_6661678 livestream_status">Halftime',
                      derived(self.html, "6661678", active=True), count=1)
        html = re.sub(r'(<tr id="contest_6661678">\s*<!--)', r'\1 <div class="livestream_status_6661678">Final</div>',
                      html, count=1)
        game = next(game for game in parse_scoreboard(html, source_url=URL)["games"] if game["contest_id"] == "6661678")
        self.assertEqual((game["status"], game["status_text"], game["completed"]), ("not_final", "Halftime", False))

    def test_final_without_score_is_not_completed(self):
        game = parse_scoreboard(derived(self.html, "6635360", clear_scores=True), source_url=URL)["games"][5]
        self.assertEqual(game["contest_id"], "6635360")
        self.assertFalse(game["completed"])
        self.assertIn("final_without_scores", game["issues"])

    def test_linescore_mismatch_blocks_completion(self):
        html = re.sub(r'(<div[^>]*id="score_8669336"[^>]*>\s*)94', r"\g<1>95", self.html, count=1)
        game = {g["contest_id"]: g for g in parse_scoreboard(html, source_url=URL)["games"]}["6643366"]
        self.assertEqual(game["linescore_check"], "fail")
        self.assertFalse(game["completed"])

    def test_rejects_wrong_pages_and_sources(self):
        synthetic_boxscore = (FIXTURES / "ncaa_boxscore_synthetic.html").read_text(encoding="utf-8")
        with self.assertRaises(ParseError):
            parse_scoreboard(synthetic_boxscore, source_url=URL)
        with self.assertRaises(ParseError):
            parse_scoreboard(self.raw, source_url="https://stats.ncaa.org/season_divisions/18984/livestream_scoreboards")
        with self.assertRaises(ValueError):
            parse_scoreboard(self.raw, source_url="https://example.com/scoreboard")

    def test_safe_url_keeps_validated_scoreboard_parameters(self):
        self.assertEqual(safe_url(URL), URL)
        self.assertIn("REDACTED", safe_url("https://stats.ncaa.org/x?game_date=tomorrow&conference_id=0"))


class DiscoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = parse_scoreboard(REAL.read_bytes(), source_url=URL, parsed_at="2026-11-17T06:00:00+00:00")

    def test_bundle_games_follow_input_contract_without_players(self):
        games = bundle_games(self.result)
        self.assertEqual(len(games), 33)
        game = next(game for game in games if game["game_id"] == "6643366")
        self.assertEqual(game["status"], "final")
        self.assertEqual(game["boxscore_source_url"], "https://stats.ncaa.org/contests/6643366/box_score")
        self.assertEqual(game["teams"][1], {"team_name": "George Washington", "team_id": "627529",
                                            "division": None, "final_score": 94})
        self.assertEqual(game["player_statistics"], "pending_boxscore")
        self.assertNotIn("players", game)

    def test_pipeline_refuses_discovered_games_without_box_scores(self):
        bundle = {**bundle_skeleton(self.result), "provenance": "synthetic"}
        self.assertEqual(bundle["ncaa_season_id"], "17240")
        with self.assertRaisesRegex(ValueError, "add its box-score players"):
            process_bundle(bundle)

    def test_cross_check_reports_matches_and_differences(self):
        games = copy.deepcopy(bundle_games(self.result))[:2]
        games[1]["teams"][1]["final_score"] = 93
        games.append({"game_id": "SYNTHETIC-G1", "teams": []})
        checks = cross_check({"games": games}, self.result)
        self.assertEqual([check["status"] for check in checks], ["match", "mismatch", "not_on_scoreboard"])
        self.assertEqual(checks[1]["differences"], ["final_score:627529"])

    def test_unlinked_team_matches_by_name(self):
        game = copy.deepcopy(next(game for game in bundle_games(self.result) if game["game_id"] == "6656341"))
        self.assertEqual(cross_check({"games": [game]}, self.result)[0]["status"], "match")

    def test_write_discovery_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_discovery(self.result, directory)
            with paths["csv"].open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 33)
            row = next(row for row in rows if row["contest_id"] == "6656341")
            self.assertEqual((row["team1_name"], row["team1_id"], row["team1_score"]), ("Fisk", "", "49"))
            self.assertEqual(row["scoreboard_source_url"], URL)
            self.assertEqual(row["player_statistics_verified"], "False")
            saved = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(saved["provenance"]["html_sha256"], self.result["provenance"]["html_sha256"])
            bundle = json.loads(paths["bundle"].read_text(encoding="utf-8"))
            self.assertEqual((bundle["season"], bundle["sport"], len(bundle["games"])), ("2026-27", "MBB", 33))
            self.assertIsNone(bundle["permission_reference"])


class DiscoveryCLITests(unittest.TestCase):
    def test_discover_command_is_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch("requests.Session.get") as network:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["discover-ncaa-games", "--file", str(REAL), "--source-url", URL, "--output", directory])
            network.assert_not_called()
            self.assertEqual(status, 0)
            summary = json.loads(out.getvalue())
            self.assertEqual((summary["completed"], summary["teams_without_team_page"]), (33, 8))
            self.assertFalse(summary["player_statistics_verified"])
            self.assertEqual(json.loads(Path(directory, "scoreboard_games.json").read_text(encoding="utf-8"))
                             ["provenance"]["source_file"], REAL.name)

    def test_process_with_scoreboard_reports_synthetic_games_as_absent(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            status = main(["process-ncaa", "--manifest", str(FIXTURES / "ncaa_enrichment_synthetic.json"),
                           "--output", directory, "--scoreboard", str(REAL), "--scoreboard-url", URL])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(out.getvalue())["scoreboard_mismatches"], 1)
            quality = json.loads(Path(directory, "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["scoreboard_cross_check"]["games"],
                             [{"game_id": "SYNTHETIC-G1", "status": "not_on_scoreboard", "differences": []}])

    def test_scoreboard_requires_its_url(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["process-ncaa", "--manifest", str(FIXTURES / "ncaa_enrichment_synthetic.json"),
                                   "--scoreboard", str(REAL)]), 4)


if __name__ == "__main__":
    unittest.main()
