"""Rebuild the three validated games from the committed sanitized fixtures, compare their CSV
content hashes with the validated outputs, and merge them into one CSV (offline)."""
import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from src.main import build_plan, main
from src.ncaa.combine import COLUMN_DICTIONARY, combine_games
from src.ncaa.pipeline import COLUMNS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PLAN = ROOT / "config" / "ncaa_games_fixtures.json"
COMBINED_SHA256 = "402921c1ad1bf0a74590ca360a972dc36eaf0ddb630e4a44651b17e9ec6df03f"


def temp_plan(folder, **changes):
    """Copy the fixture plan with absolute inputs and outputs inside a temporary folder."""
    plan = json.loads(FIXTURE_PLAN.read_text(encoding="utf-8"))
    plan["affiliations"] = str(ROOT / plan["affiliations"])
    for game in plan["games"]:
        game["output"] = str(Path(folder) / f"ncaa-game-{game['game_id']}")
        for key in ("scoreboard", "individual_stats", "box_score"):
            game[key] = [str(ROOT / game[key][0]), game[key][1]]
        game["rosters"] = [[str(ROOT / path), url] for path, url in game["rosters"]]
        game.update(changes.get(game["game_id"], {}))
    path = Path(folder) / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def run_plan(path):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = build_plan(path)
    return status, json.loads(out.getvalue())["games"]


class FixtureRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plan = temp_plan(cls.tmp.name)
        cls.status, cls.games = run_plan(cls.plan)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rebuild_matches_validated_hashes(self):
        self.assertEqual([game["game_id"] for game in self.games], ["6643366", "6631754", "6617400"])
        for game in self.games:
            self.assertTrue(game["rebuild_match"], game["game_id"])
            self.assertEqual(game["source_checks"]["passed"], 57)
            self.assertEqual(game["provenance"], "local_validation_preview")
            # LF on every platform: the file bytes hash to the recorded content hash.
            data = Path(game["csv"]).read_bytes()
            self.assertNotIn(b"\r", data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), game["csv_sha256"])
        # Exit 2 comes only from the existing Ferrum division_reclassifying warnings.
        self.assertEqual(self.status, 2)

    def test_hash_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = temp_plan(folder, **{"6617400": {"expected_csv_sha256": "0" * 64}})
            status, games = run_plan(plan)
            self.assertEqual(status, 2)
            self.assertFalse(games[2]["rebuild_match"])
            self.assertEqual(games[2]["status"], 2)

    def test_combined_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            report = combine_games(self.plan, Path(folder) / "combined")
            self.assertEqual(report["csv_sha256"], COMBINED_SHA256)
            self.assertEqual((report["rows"], report["games"]), (77, 3))
            self.assertEqual(report["rows_by_division"], {"I": 14, "II": 39, "III": 24})
            self.assertEqual(report["provenance"], "local_validation_preview")
            csv_path = Path(report["csv"])
            self.assertTrue(csv_path.name.endswith("_LOCAL_PREVIEW.csv"))
            self.assertNotIn(b"\r", csv_path.read_bytes())
            with csv_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, COLUMNS)
            self.assertEqual([row["game_id"] for row in rows[:1] + rows[-1:]], ["6643366", "6617400"])
            with (Path(folder) / "combined" / "column_dictionary.csv").open(encoding="utf-8", newline="") as stream:
                dictionary = list(csv.DictReader(stream))
            self.assertEqual([entry["column"] for entry in dictionary], COLUMNS)

    def test_dictionary_covers_every_column(self):
        self.assertEqual([entry[0] for entry in COLUMN_DICTIONARY], COLUMNS)
        self.assertTrue(all(entry[3] for entry in COLUMN_DICTIONARY))

    def test_cli_expect_sha256(self):
        with tempfile.TemporaryDirectory() as folder:
            with contextlib.redirect_stdout(io.StringIO()):
                ok = main(["combine-ncaa-games", "--plan", str(self.plan), "--output", folder,
                           "--expect-sha256", COMBINED_SHA256])
                bad = main(["combine-ncaa-games", "--plan", str(self.plan), "--output", folder,
                            "--expect-sha256", "0" * 64])
            self.assertEqual((ok, bad), (0, 2))

    def test_refuses_exports_for_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "authorized_source"):
                combine_games(self.plan, Path(folder) / "data" / "exports")

    def test_refuses_edited_source_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = temp_plan(folder)
            run_plan(plan)
            target = next((Path(folder) / "ncaa-game-6617400").glob("*.csv"))
            # Edit one cell by hand without updating quality_report.json.
            target.write_bytes(target.read_bytes().replace(b",PASS", b",PASS ", 1))
            with self.assertRaisesRegex(ValueError, "does not match"):
                combine_games(plan, Path(folder) / "combined")

    def test_refuses_duplicate_game(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = json.loads(Path(self.plan).read_text(encoding="utf-8"))
            plan["games"].append(dict(plan["games"][0]))
            path = Path(folder) / "dup.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                combine_games(path, Path(folder) / "combined")


if __name__ == "__main__":
    unittest.main()
