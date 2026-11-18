"""Per-host pacing persists across runs (fix for the 28.1 s gap on 2026-11-18)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.school.pacing import HostPacer

HOST = "athletics.bowdoin.edu"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now, self.slept = now, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


class HostPacerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def pacer(self):
        # A fresh object stands in for a new run: nothing is shared but the state file.
        return HostPacer(self.state, clock=self.clock, sleep=self.clock.sleep)

    def test_new_run_waits_for_remainder_of_crawl_delay(self):
        self.pacer().mark(HOST)
        self.clock.now += 2.0  # a second run starts 2 s later (the 2026-11-18 case)
        waited = self.pacer().wait(HOST, crawl_delay=30)
        self.assertEqual(waited, 28.0)
        self.assertEqual(self.clock.slept, [28.0])

    def test_saved_crawl_delay_paces_first_request_of_next_run(self):
        # Previous run read Crawl-delay 30 and recorded it with its last request.
        self.pacer().mark(HOST, crawl_delay=30)
        self.clock.now += 5
        # New run: robots.txt not read yet, so no crawl-delay is passed in.
        waited = self.pacer().wait(HOST)
        self.assertEqual(waited, 25.0)
        self.assertEqual(self.clock.slept, [25.0])

    def test_saved_crawl_delay_kept_until_robots_reread(self):
        self.pacer().mark(HOST, crawl_delay=30)
        self.pacer().mark(HOST)  # e.g. the robots.txt request, before the new value is known
        self.assertEqual(self.pacer().saved_crawl_delay(HOST), 30.0)
        self.pacer().mark(HOST, crawl_delay=12)  # robots.txt re-read with a new value
        self.assertEqual(self.pacer().saved_crawl_delay(HOST), 12.0)

    def test_seed_fills_missing_crawl_delay_without_moving_time(self):
        self.state.write_text(json.dumps({HOST: {"last_request_epoch": 2e9, "last_request_utc": "x"}}), encoding="utf-8")
        log = Path(self.tmp.name) / "log.jsonl"
        log.write_text(json.dumps({"host": HOST, "checked_at": "2026-11-18T06:05:37.919+00:00", "elapsed_seconds": 1.11,
                                   "attempts": [{}], "crawl_delay_seconds": 30}) + "\n", encoding="utf-8")
        state = self.pacer().seed_from_log(log)
        self.assertEqual((state[HOST]["last_request_epoch"], state[HOST]["crawl_delay_seconds"]), (2e9, 30.0))

    def test_no_wait_after_delay_passed_or_for_unknown_host(self):
        self.pacer().mark(HOST)
        self.clock.now += 31
        self.assertEqual(self.pacer().wait(HOST, crawl_delay=30), 0.0)
        self.assertEqual(self.pacer().wait("ferrumpanthers.com", crawl_delay=30), 0.0)

    def test_minimum_ten_seconds_when_crawl_delay_smaller(self):
        self.pacer().mark(HOST)
        self.assertEqual(self.pacer().wait(HOST, crawl_delay=5), 10.0)
        with self.assertRaises(ValueError):
            HostPacer(self.state, min_delay=5)

    def test_clock_moved_back_applies_full_delay(self):
        self.pacer().mark(HOST)
        self.clock.now -= 3600
        self.assertEqual(self.pacer().wait(HOST, crawl_delay=30), 30.0)

    def test_hosts_are_independent(self):
        pacer = self.pacer()
        pacer.mark(HOST)
        self.clock.now += 1
        pacer.mark("atlantic10.com")
        self.clock.now += 20
        self.assertEqual(self.pacer().wait(HOST, crawl_delay=30), 9.0)
        self.assertEqual(self.pacer().wait("atlantic10.com", crawl_delay=5), 0.0)

    def test_seed_from_log_uses_finish_time_and_skips_unsent(self):
        log = Path(self.tmp.name) / "log.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in (
            {"host": HOST, "checked_at": "2026-11-18T05:33:19.527+00:00", "elapsed_seconds": 1.135, "attempts": [{}],
             "crawl_delay_seconds": 30},
            {"host": HOST, "checked_at": "2026-11-18T05:32:20.508+00:00", "elapsed_seconds": 0.776, "attempts": [{}]},
            {"host": HOST, "checked_at": "2026-11-18T06:00:00+00:00", "attempts": []},
        )) + "\n", encoding="utf-8")
        state = self.pacer().seed_from_log(log)
        expected = datetime.fromisoformat("2026-11-18T05:33:19.527+00:00").timestamp() + 1.135
        self.assertAlmostEqual(state[HOST]["last_request_epoch"], expected, places=3)
        self.assertEqual(state[HOST]["crawl_delay_seconds"], 30.0)
        # A run started 10 s after that request still waits the remaining 20 s, before reading robots.txt.
        self.clock.now = expected + 10
        self.assertAlmostEqual(self.pacer().wait(HOST), 20.0, places=3)


if __name__ == "__main__":
    unittest.main()
