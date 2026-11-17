import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main import main
from tests.test_ncaa import FIXTURES


class CLITests(unittest.TestCase):
    def test_demo_is_offline_and_labeled(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch("requests.Session.get") as network:
            self.assertEqual(main(["demo", "--output", directory]), 0)
            network.assert_not_called()
            files = list(Path(directory).rglob("*.json"))
            self.assertEqual(len(files), 2)
            for path in files:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(all(row["provenance"] == "synthetic" for row in payload["records"]))
                self.assertTrue(all(row["source"] == "ncaa" for row in payload["records"]))

    def test_missing_date_exports_flagged_rows_and_returns_two(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            status = main(["parse", "ncaa", "https://stats.ncaa.org/synthetic-demo", "--game-id", "test",
                "--file", str(FIXTURES / "ncaa_boxscore_synthetic.html"), "--output", directory])
            self.assertEqual(status, 2)
            self.assertTrue((Path(directory) / "boxscore.json").exists())
