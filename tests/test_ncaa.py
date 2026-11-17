import unittest
from pathlib import Path

from src.common.tables import ParseError
from src.common.validation import validate
from src.ncaa.boxscore_parser import parse_boxscore
from src.ncaa.roster_parser import parse_roster

FIXTURES = Path(__file__).parent / "fixtures"
META = dict(source_url="https://stats.ncaa.org/synthetic-demo", game_id="SYNTHETIC-001",
            game_date="2026-01-01", provenance="synthetic")


class NCAAParserTests(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / "ncaa_boxscore_synthetic.html").read_text(encoding="utf-8")

    def test_players_shooting_ids_totals_and_dnp(self):
        rows = validate(parse_boxscore(self.html, **META))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].stats["fg3m"], 2)
        self.assertEqual(rows[0].player_id, "1001")
        self.assertEqual(rows[0].jersey, "01")
        self.assertEqual(rows[2].participation, "did_not_play")
        self.assertIsNone(rows[2].stats["pts"])
        self.assertEqual(rows[3].team, "Example Foxes")
        self.assertFalse(any(row.validation for row in rows))

    def test_roster(self):
        rows = parse_roster((FIXTURES / "ncaa_roster_synthetic.html").read_text(),
                            source_url=META["source_url"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].position, "G")
        self.assertIsNone(rows[0].game_id)

    def test_fail_closed_on_bad_layout_and_challenge(self):
        for html in ("<title>Access Denied</title>", "<p>No data</p>",
                     self.html.replace("<td>18</td>", ""),
                     self.html.replace("<td>18</td>", '<td colspan="2">18</td>')):
            with self.subTest(html=html[:60]), self.assertRaises(ParseError):
                parse_boxscore(html, **META)

    def test_missing_is_not_zero_and_bad_number_is_flagged(self):
        rows = validate(parse_boxscore(self.html.replace("<td>18</td>", "<td>oops</td>"), **META))
        self.assertIsNone(rows[0].stats["pts"])
        self.assertIn("invalid_number:pts", rows[0].validation)

    def test_refuse_one_team_for_two_tables(self):
        with self.assertRaises(ParseError):
            parse_boxscore(self.html, team="Wrong", **META)
