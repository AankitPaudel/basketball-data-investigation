"""Season-specific, source-backed affiliations applied to the real game 6643366.

Conflict cases edit copies of config/ncaa_team_affiliations.json inside each test;
those edited values are hypothetical, not observed sources.
"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from src.main import main
from src.ncaa.affiliations import apply_affiliations, load_affiliations
from src.ncaa.pipeline import process_bundle
from tests.test_real_game import BOX, FERRUM_ROSTER, GW_ROSTER, INDIVIDUAL, SCOREBOARD, STAMP, bundle

MAPPING = Path(__file__).resolve().parents[1] / "config" / "ncaa_team_affiliations.json"
GW, FERRUM = "627529", "627884"


def mapping_file(directory, edit=None):
    data = json.loads(MAPPING.read_text(encoding="utf-8"))
    if edit:
        edit(data["affiliations"])
    path = Path(directory) / "affiliations.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def entry(entries, team_id):
    return next(item for item in entries if item["team_id"] == team_id)


class AffiliationMappingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def load(self, edit=None):
        return load_affiliations(mapping_file(self.directory.name, edit))

    def test_committed_mapping_values_and_evidence(self):
        entries = load_affiliations(MAPPING)["entries"]
        gw, ferrum = entries[("2026-27", "MBB", GW)], entries[("2026-27", "MBB", FERRUM)]
        self.assertEqual((gw["division"], gw["conference"], gw["reclassifying"]), ("I", "Atlantic 10 Conference", False))
        self.assertEqual((ferrum["division"], ferrum["conference"], ferrum["reclassifying"]), ("II", "Conference Carolinas", True))
        self.assertEqual(gw["evidence"][0]["url"], "https://web3.ncaa.org/directory/orgDetail?id=249")
        self.assertEqual(ferrum["evidence"][0]["url"], "https://web3.ncaa.org/directory/orgDetail?id=225")
        for item in (*gw["evidence"], *ferrum["evidence"]):
            self.assertEqual(len(item["sha256"]), 64)

    def test_real_game_gets_division_and_conference(self):
        game = bundle()
        applied = apply_affiliations(game, load_affiliations(MAPPING))
        self.assertEqual({item["team_id"] for item in applied}, {GW, FERRUM})
        result, quality = process_bundle(game)
        rows = [item["row"] for item in result]
        for row in rows:
            expected = ("I", "Atlantic 10 Conference") if row["team_id"] == GW else ("II", "Conference Carolinas")
            self.assertEqual((row["division"], row["conference"]), expected)
            self.assertTrue(row["conference_source_url"].startswith("https://web3.ncaa.org/directory/orgDetail?id="))
        self.assertEqual(sum(row["validation_status"] == "PASS" for row in rows if row["team_id"] == GW), 14)
        self.assertEqual(quality["issue_counts"]["division_reclassifying"], 16)
        self.assertNotIn("missing_conference", quality["issue_counts"])
        self.assertNotIn("missing_division", quality["issue_counts"])
        self.assertEqual([c for c in quality["source_checks"] if c["status"] != "pass"], [])
        self.assertEqual(len(quality["source_checks"]), 57)

    def test_other_season_entry_is_not_applied(self):
        def to_previous_season(entries):
            for item in entries:
                item["season"] = "2025-26"
                for evidence in item["evidence"]:
                    evidence["academic_year"] = "2025-2026"
        game = bundle()
        self.assertEqual(apply_affiliations(game, self.load(to_previous_season)), [])
        _, quality = process_bundle(game)
        self.assertEqual(quality["issue_counts"]["missing_division"], 30)
        self.assertEqual(quality["issue_counts"]["missing_conference"], 30)

    def test_evidence_from_another_academic_year_is_rejected(self):
        def stale(entries):
            entry(entries, FERRUM)["evidence"][0]["academic_year"] = "2024-2025"
        with self.assertRaisesRegex(ValueError, "is not season 2026-27"):
            self.load(stale)

    def test_conflicting_entries_are_rejected(self):
        def duplicate(entries):
            other = copy.deepcopy(entry(entries, FERRUM))
            # A self-consistent but contradictory entry (hypothetical old D-III membership).
            other.update(division="III", conference="Old Dominion Athletic Conference", institution_division="III",
                         institution_status="Active", reclassifying=False, reclass_division=None)
            for evidence in other["evidence"]:
                evidence["observed"] = {"division": "III", "conference": "Old Dominion Athletic Conference",
                                        "institution_division": "III"}
            entries.append(other)
        with self.assertRaisesRegex(ValueError, "Conflicting affiliation entries"):
            self.load(duplicate)

    def test_agreeing_duplicate_entries_merge_evidence(self):
        entries = self.load(lambda items: items.append(copy.deepcopy(entry(items, GW))))["entries"]
        self.assertEqual(len(entries[("2026-27", "MBB", GW)]["evidence"]), 4)

    def test_disagreeing_evidence_is_rejected(self):
        def disagree(entries):
            entry(entries, FERRUM)["evidence"][1]["observed"]["division"] = "III"
        with self.assertRaisesRegex(ValueError, "conflicting division"):
            self.load(disagree)

    def test_ncaa_directory_evidence_required(self):
        def drop_directory(entries):
            entry(entries, GW)["evidence"] = entry(entries, GW)["evidence"][1:]
        with self.assertRaisesRegex(ValueError, "NCAA Directory evidence item is required"):
            self.load(drop_directory)

        def wrong_host(entries):
            entry(entries, GW)["evidence"][0]["url"] = "https://stats.ncaa.org/rankings/ranking_summary?division=1"
        with self.assertRaisesRegex(ValueError, "web3.ncaa.org orgDetail"):
            self.load(wrong_host)

    def test_institution_and_sport_division_kept_separate(self):
        entries = load_affiliations(MAPPING)["entries"]
        ferrum = entries[("2026-27", "MBB", FERRUM)]
        self.assertEqual((ferrum["institution_division"], ferrum["reclass_division"], ferrum["division"]), ("III", "II", "II"))
        gw = entries[("2026-27", "MBB", GW)]
        self.assertEqual((gw["institution_division"], gw["division"], gw["reclass_division"]), ("I", "I", None))

    def test_reclassifying_entry_needs_reclass_division(self):
        def drop(entries):
            entry(entries, FERRUM)["reclass_division"] = None
        with self.assertRaisesRegex(ValueError, "needs reclass_division"):
            self.load(drop)

    def test_sport_division_outside_membership_is_rejected(self):
        def wrong(entries):
            item = entry(entries, FERRUM)
            item["division"] = "I"
            for evidence in item["evidence"]:
                evidence["observed"]["division"] = "I"
        with self.assertRaisesRegex(ValueError, "institution or reclass division"):
            self.load(wrong)

    def test_multidivision_sport_requires_a_note(self):
        # Hypothetical: a Division III institution sponsoring this sport in Division I.
        def multi(entries):
            item = entry(entries, GW)
            item["institution_division"] = "III"
            item["evidence"][0]["observed"]["institution_division"] = "III"
        with self.assertRaisesRegex(ValueError, "without a sport_division_note"):
            self.load(multi)

        def multi_with_note(entries):
            multi(entries)
            entry(entries, GW)["sport_division_note"] = "Sport sponsored in Division I (hypothetical test value)"
        self.assertEqual(self.load(multi_with_note)["entries"][("2026-27", "MBB", GW)]["division"], "I")

    def test_directory_must_observe_institution_division(self):
        def missing(entries):
            del entry(entries, FERRUM)["evidence"][0]["observed"]["institution_division"]
        with self.assertRaisesRegex(ValueError, "must observe"):
            self.load(missing)

    def test_bundle_division_conflict_is_rejected(self):
        game = bundle()
        game["games"][0]["teams"][0]["division"] = "III"
        with self.assertRaisesRegex(ValueError, "Conflicting division for team 627884"):
            apply_affiliations(game, load_affiliations(MAPPING))

    def test_bundle_conference_conflict_is_rejected(self):
        game = bundle()
        game["conferences"] = [{"season": "2026-27", "sport": "MBB", "team_id": GW,
                                "conference": "Colonial Athletic Association",
                                "source_url": "https://web3.ncaa.org/directory/orgDetail?id=249"}]
        with self.assertRaisesRegex(ValueError, "Conflicting conference for team 627529"):
            apply_affiliations(game, load_affiliations(MAPPING))

    def test_team_name_mismatch_is_rejected(self):
        def rename(entries):
            entry(entries, GW)["team_name"] = "George Mason"
        with self.assertRaisesRegex(ValueError, "names 'George Mason'"):
            apply_affiliations(bundle(), self.load(rename))

    def test_conference_source_host_is_restricted(self):
        game = bundle()
        game["conferences"] = [{"season": "2026-27", "sport": "MBB", "team_id": GW,
                                "conference": "Atlantic 10 Conference", "source_url": "https://example.com/a10"}]
        with self.assertRaisesRegex(ValueError, "Conference source must be"):
            process_bundle(game)

    def test_cli_applies_mapping_and_stays_preview(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            status = main(["build-ncaa-game", "--scoreboard", str(SCOREBOARD[0]), SCOREBOARD[1],
                "--individual-stats", str(INDIVIDUAL[0]), INDIVIDUAL[1], "--box-score", str(BOX[0]), BOX[1],
                "--roster", str(GW_ROSTER[0]), GW_ROSTER[1], "--roster", str(FERRUM_ROSTER[0]), FERRUM_ROSTER[1],
                "--affiliations", str(MAPPING), "--extracted-at", STAMP, "--output", directory])
            summary = json.loads(out.getvalue())
            quality = json.loads((Path(directory) / "quality_report.json").read_text(encoding="utf-8"))
        self.assertEqual(status, 2)  # Ferrum rows keep reclassifying/missing-roster-field warnings.
        self.assertEqual(summary["provenance"], "local_validation_preview")
        self.assertEqual(summary["validation_counts"], {"PASS": 14, "WARNING": 16})
        self.assertEqual(len(quality["affiliations"]["applied"]), 2)
        self.assertIn("evidence", quality["affiliations"]["applied"][0])


if __name__ == "__main__":
    unittest.main()
