import contextlib
import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main import main
from src.ncaa.pipeline import COLUMNS, process_bundle, run_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "ncaa_enrichment_synthetic.json"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_contract_matching_and_raw_values(self):
        records, quality = process_bundle(self.bundle)
        self.assertEqual(quality["row_count"], 3)
        self.assertEqual(quality["validation_counts"], {"PASS": 3})
        self.assertEqual(quality["player_match_counts"], {"matched_id": 3})
        self.assertEqual(set(records[0]["row"]), set(COLUMNS))
        self.assertEqual(records[0]["row"]["height"], 187.96)
        self.assertEqual(records[0]["raw"]["height"], "6-2")
        self.assertEqual(records[0]["row"]["opponent_team_id"], "SYNTHETIC-T2")
        self.assertIsNone(records[1]["row"]["points"])
        self.assertTrue(all(check["status"] == "pass" for check in quality["team_score_checks"]))

    def test_season_and_sport_scope_prevents_wrong_joins(self):
        self.bundle["rosters"][0]["season"] = "2025-26"
        self.bundle["conferences"][0]["sport"] = "WBB"
        records, quality = process_bundle(self.bundle)
        self.assertEqual(len(records), 3)  # left join preserves unmatched box rows
        self.assertEqual(quality["player_match_counts"]["unmatched"], 2)
        self.assertIsNone(records[0]["row"]["conference"])
        self.assertIsNone(records[0]["row"]["height"])

    def test_name_match_is_a_warning(self):
        self.bundle["games"][0]["players"][0]["player_id"] = None
        records, _ = process_bundle(self.bundle)
        self.assertEqual(records[0]["row"]["player_match_status"], "matched_name")
        self.assertEqual(records[0]["row"]["validation_status"], "WARNING")

    def test_ambiguity_preserves_row_without_biodata(self):
        self.bundle["rosters"][0]["players"].append(copy.deepcopy(self.bundle["rosters"][0]["players"][0]))
        records, quality = process_bundle(self.bundle)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["row"]["validation_status"], "FAIL")
        self.assertIsNone(records[0]["row"]["height"])
        self.assertEqual(quality["player_match_counts"]["ambiguous_id"], 1)

    def test_missing_conference_provenance_prevents_assertion(self):
        self.bundle["conferences"][0]["source_url"] = None
        records, _ = process_bundle(self.bundle)
        self.assertIsNone(records[0]["row"]["conference"])

    def test_exact_duplicates_removed_conflicts_rejected(self):
        players = self.bundle["games"][0]["players"]
        players.append(copy.deepcopy(players[0]))
        records, quality = process_bundle(self.bundle)
        self.assertEqual(len(records), 3)
        self.assertEqual(quality["exact_duplicate_rows_removed"], 1)
        players[-1]["stats"]["pts"] = 19
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            process_bundle(self.bundle)

    def test_post_join_identity_collision_rejected(self):
        players = self.bundle["games"][0]["players"]
        extra = copy.deepcopy(players[0])
        extra["player_id"] = None
        players.append(extra)
        with self.assertRaisesRegex(ValueError, "Duplicate identity after"):
            process_bundle(self.bundle)

    def test_team_score_exceptions_and_missing_points(self):
        game = self.bundle["games"][0]
        game["teams"][0]["final_score"] = 20
        records, quality = process_bundle(self.bundle)
        self.assertEqual(quality["team_score_checks"][0]["status"], "fail")
        self.assertIn("team_score_mismatch", records[0]["errors"])
        game["teams"][0].update(team_points_adjustment=2, team_points_adjustment_reason="Synthetic scorer correction")
        _, quality = process_bundle(self.bundle)
        self.assertEqual(quality["team_score_checks"][0]["status"], "pass")
        game["players"][0]["stats"]["pts"] = None
        _, quality = process_bundle(self.bundle)
        self.assertEqual(quality["team_score_checks"][0]["status"], "not_checked")

    def test_malformed_stats_and_empty_game_rejected(self):
        for mutate in (lambda bundle: bundle["games"][0]["players"][0]["stats"].update(pts="18"),
                       lambda bundle: bundle["games"][0].update(players=[])):
            bundle = copy.deepcopy(self.bundle)
            mutate(bundle)
            with self.assertRaises(ValueError):
                process_bundle(bundle)

    def test_real_inputs_require_permission_reference(self):
        self.bundle["provenance"] = "authorized_source"
        with self.assertRaisesRegex(ValueError, "permission_reference"):
            process_bundle(self.bundle)

    def test_nonfinal_game_and_missing_source_rejected(self):
        self.bundle["games"][0]["status"] = "in_progress"
        with self.assertRaisesRegex(ValueError, "final"):
            process_bundle(self.bundle)
        self.bundle["games"][0]["status"] = "final"
        self.bundle["games"][0]["boxscore_source_url"] = None
        with self.assertRaisesRegex(ValueError, "boxscore_source_url"):
            process_bundle(self.bundle)

    def test_synthetic_never_written_into_real_exports(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            run_pipeline(FIXTURE, "data/exports/should-not-exist")

    def test_reprocessing_is_byte_identical_and_preserves_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            manifest = directory / "input.json"
            manifest.write_text(json.dumps(self.bundle), encoding="utf-8")
            output = directory / "output"
            _, csv_path = run_pipeline(manifest, output)
            first = {path.name: path.read_bytes() for path in output.iterdir()}
            run_pipeline(manifest, output)
            self.assertEqual(first, {path.name: path.read_bytes() for path in output.iterdir()})
            with csv_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, COLUMNS)
                self.assertEqual(len(list(reader)), 3)
            self.bundle["games"][0]["players"] = []
            manifest.write_text(json.dumps(self.bundle), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_pipeline(manifest, output)
            self.assertEqual(first, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_cli_offline_and_configured_season(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch("requests.Session.get") as network:
            self.assertEqual(main(["process-ncaa", "--manifest", str(FIXTURE), "--output", directory]), 0)
            self.assertEqual(main(["process-ncaa", "--manifest", str(FIXTURE), "--season", "2025-26", "--output", directory]), 4)
            network.assert_not_called()

    def test_offline_html_adapter_reuses_existing_parser(self):
        game = self.bundle["games"][0]
        del game["players"]
        game["boxscore_file"] = "ncaa_boxscore_synthetic.html"
        game["teams"][0]["final_score"] = 30
        game["teams"][1]["final_score"] = 21
        records, quality = process_bundle(self.bundle, FIXTURE.parent)
        self.assertEqual(len(records), 4)
        self.assertEqual(quality["game_count"], 1)

    def test_csv_escaping_and_unicode(self):
        self.bundle["rosters"][0]["players"][0]["hometown"] = '=HYPERLINK("example")'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(self.bundle), encoding="utf-8")
            _, csv_path = run_pipeline(path, Path(directory) / "output")
            with csv_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows[0]["hometown"].startswith("'="))
            self.assertEqual(rows[2]["player_name"], "Renée Example")
