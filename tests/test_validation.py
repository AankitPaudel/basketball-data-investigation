import json
import tempfile
import unittest
from pathlib import Path

from src.common.export import export_records
from src.common.models import PlayerRecord
from src.common.validation import validate


class ValidationTests(unittest.TestCase):
    def record(self, **stats):
        return PlayerRecord(source="ncaa", source_url="https://stats.ncaa.org/synthetic-demo",
                            game_id="test", game_date="2026-01-01", team="Example", player="=Example",
                            stats=stats, provenance="synthetic")

    def test_arithmetic_and_impossible_statistics(self):
        row = self.record(pts=20, reb=8, ast=2, fgm=3, fga=2, fg3m=4, ftm=1, oreb=2, dreb=3)
        validate([row])
        for expected in ("made_exceeds_attempted:fgm", "three_pointers_exceed_field_goals",
                         "points_do_not_reconcile", "rebounds_do_not_reconcile"):
            self.assertIn(expected, row.validation)

    def test_duplicate_missing_date_and_negative(self):
        a, b = self.record(pts=-1), self.record(pts=1)
        a.game_date = None
        validate([a, b])
        self.assertIn("invalid_stat:pts", a.validation)
        self.assertIn("missing_game_date", a.validation)
        self.assertIn("duplicate_player", b.validation)

    def test_exports_preserve_null_unicode_and_escape_csv_formulas(self):
        row = self.record(pts=None, reb=0, ast=1)
        row.team = "Équipe"
        with tempfile.TemporaryDirectory() as directory:
            export_records([row], directory)
            payload = json.loads((Path(directory) / "boxscore.json").read_text(encoding="utf-8"))
            self.assertIsNone(payload["records"][0]["stats"]["pts"])
            self.assertEqual(payload["records"][0]["team"], "Équipe")
            self.assertIn("'=Example", (Path(directory) / "boxscore.csv").read_text(encoding="utf-8-sig"))
