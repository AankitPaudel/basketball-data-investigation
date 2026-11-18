"""fetch-school-boxscore: argument parsing and robots/pacing behavior, fully offline (fake transport)."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from src.main import build_parser, main
from src.school.fetcher import FetchRefused, SchoolFetcher, find_boxscore, in_visit_window, star_group, wildcard_rule
from src.school.pacing import HostPacer

HOST = "athletics.example.edu"
SCHEDULE = f"https://{HOST}/sports/mens-basketball/schedule/2026-27"
BOX = f"https://{HOST}/sports/mens-basketball/stats/2026-27/example-tech/boxscore/7"
ROBOTS = "User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /services/\nDisallow: /*print=true*\nAllow: /\nCrawl-delay: 30\n"
SCHEDULE_HTML = f"""<html><body><ul>
<li class="sidearm-schedule-game"><div class="sidearm-schedule-game-opponent-date">Jan 3 (Sat) 2:00 PM</div>
 <div class="sidearm-schedule-game-opponent-name">Other College</div><a href="/sports/mens-basketball/stats/2026-27/other/boxscore/6">Box Score</a></li>
<li class="sidearm-schedule-game"><div class="sidearm-schedule-game-opponent-date">Jan 10 (Sat) 2:00 PM</div>
 <div class="sidearm-schedule-game-opponent-name">Example Tech</div><a href="{BOX[len('https://' + HOST):]}">Box Score</a>
 <a href="{BOX[len('https://' + HOST):]}">Box Score</a><a href="https://elsewhere.example/boxscore/9.xml">XML</a></li>
</ul></body></html>"""
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sidearm_boxscore_synthetic.html"


class Response:
    def __init__(self, status, body, kind):
        self.status_code, self.text, self.content = status, body, body.encode("utf-8")
        self.headers = {"Content-Type": kind}


class Session:
    def __init__(self, pages):
        self.pages, self.requested, self.cookies = pages, [], {}

    def get(self, url, **kwargs):
        self.requested.append(url)
        assert kwargs["headers"]["User-Agent"] == "BasketballDataResearch/0.1"
        return Response(*self.pages[url])


class Clock:
    def __init__(self):
        self.now, self.slept = 2_000_000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


ARGS = ["fetch-school-boxscore", "--host", HOST, "--schedule-url", SCHEDULE, "--opponent", "Example Tech",
        "--date", "2027-01-10", "--ncaa-game-dir", "data/processed/ncaa-game-6617400",
        "--output", "data/processed/school-comparison/x", "--environment", "local-windows"]


class ArgumentTests(unittest.TestCase):
    def test_parses_and_defaults(self):
        args = build_parser().parse_args(ARGS)
        self.assertEqual((args.host, args.opponent, args.date), (HOST, "Example Tech", date(2027, 1, 10)))
        self.assertEqual((args.region, args.execute, args.log, args.raw_dir), ("unknown", False, None, None))
        self.assertEqual(args.pacing_state, Path("logs/host-pacing-state.json"))

    def test_rejects_bad_or_missing_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (ARGS[:-2], [*ARGS[:7], "--date", "14/11/2026", *ARGS[9:]]):
                with self.assertRaises(SystemExit):
                    build_parser().parse_args(argv)

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(argv)
        return status, json.loads(out.getvalue())

    def test_refuses_off_host_and_ncaa_urls_before_any_request(self):
        status, result = self.run_main([*ARGS[:3], "--schedule-url", "https://other.example/s", *ARGS[5:]])
        self.assertEqual(status, 4)
        self.assertIn("on --host", result["error"])
        ncaa = ["--host", "stats.ncaa.org", "--schedule-url", "https://stats.ncaa.org/x"]
        status, result = self.run_main([ARGS[0], *ncaa, *ARGS[5:]])
        self.assertEqual((status, "manual-only" in result["error"]), (4, True))

    def test_dry_run_without_execute(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder) / "game"
            game.mkdir()
            (game / "manifest.json").write_text("{}", encoding="utf-8")
            status, result = self.run_main([*ARGS[:9], "--ncaa-game-dir", str(game), *ARGS[11:]])
        self.assertEqual(status, 0)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["requests_in_order"][:2], [f"https://{HOST}/robots.txt", SCHEDULE])


class RobotsTests(unittest.TestCase):
    def test_star_group_wildcards_and_visit_time(self):
        rules, visit = star_group(ROBOTS + "Visit-time: 0100-0645\n")
        self.assertEqual(visit, "0100-0645")
        self.assertFalse(wildcard_rule(rules, BOX + "?print=true")[0])
        self.assertFalse(wildcard_rule(rules, f"https://{HOST}/services/x")[0])
        self.assertTrue(wildcard_rule(rules, BOX)[0])
        self.assertTrue(in_visit_window("0100-0645", datetime(2026, 11, 18, 5, 30, tzinfo=timezone.utc)))
        self.assertFalse(in_visit_window("0100-0645", datetime(2026, 11, 17, 9, 10, tzinfo=timezone.utc)))

    def test_find_boxscore_by_opponent_and_date(self):
        self.assertEqual(find_boxscore(SCHEDULE_HTML, SCHEDULE, HOST, "Example Tech", date(2027, 1, 10)), BOX)
        with self.assertRaisesRegex(FetchRefused, "found 0"):
            find_boxscore(SCHEDULE_HTML, SCHEDULE, HOST, "Example Tech", date(2027, 1, 3))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.clock = Clock()
        self.pacer = HostPacer(self.folder / "state.json", clock=self.clock, sleep=self.clock.sleep)

    def tearDown(self):
        self.tmp.cleanup()

    def fetcher(self, pages):
        session = Session(pages)
        fetcher = SchoolFetcher(HOST, self.folder / "log.jsonl", "test", pacer=self.pacer, session=session,
                                clock=lambda: datetime(2026, 11, 18, 5, 30, tzinfo=timezone.utc))
        return fetcher, session

    def pages(self, robots=ROBOTS, schedule=SCHEDULE_HTML):
        return {f"https://{HOST}/robots.txt": (200, robots, "text/plain"),
                SCHEDULE: (200, schedule, "text/html"),
                BOX: (200, FIXTURE.read_text(encoding="utf-8"), "text/html")}

    def test_full_run_paces_with_crawl_delay(self):
        fetcher, session = self.fetcher(self.pages())
        url, html, saved = fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertEqual((url, session.requested), (BOX, [f"https://{HOST}/robots.txt", SCHEDULE, BOX]))
        self.assertEqual(self.clock.slept, [30.0, 30.0])  # Crawl-delay 30, not the 10 s minimum
        self.assertTrue(saved.exists())
        log = [json.loads(line) for line in (self.folder / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in log], ["robots", "schedule", "boxscore"])
        self.assertTrue(all(r["robots_allows"] for r in log[1:]))

    def test_next_run_paces_its_robots_request_with_saved_crawl_delay(self):
        fetcher, _ = self.fetcher(self.pages())
        fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertEqual(self.pacer.saved_crawl_delay(HOST), 30.0)
        self.clock.now += 5  # a new run starts 5 s after the last request
        self.clock.slept.clear()
        fetcher, session = self.fetcher(self.pages())
        fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertEqual(self.clock.slept, [25.0, 30.0, 30.0])  # robots.txt waits the remaining 25 s
        robots = [json.loads(line) for line in (self.folder / "log.jsonl").read_text(encoding="utf-8").splitlines()][3]
        self.assertEqual((robots["kind"], robots["waited_seconds"], robots["min_delay_seconds"]), ("robots", 25.0, 30.0))

    def test_disallowed_page_is_never_requested(self):
        robots = ROBOTS.replace("Allow: /\n", "Disallow: /sports/mens-basketball/stats/\nAllow: /\n")
        fetcher, session = self.fetcher(self.pages(robots=robots))
        with self.assertRaisesRegex(FetchRefused, "disallows"):
            fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertNotIn(BOX, session.requested)
        last = json.loads((self.folder / "log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((last["outcome"], last["target_requested"]), ("robots_disallowed", False))

    def test_stops_on_403_and_unreadable_robots(self):
        pages = self.pages()
        pages[SCHEDULE] = (403, "<html><title>Access denied</title></html>", "text/html")
        fetcher, session = self.fetcher(pages)
        with self.assertRaisesRegex(FetchRefused, "forbidden"):
            fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertNotIn(BOX, session.requested)
        pages[f"https://{HOST}/robots.txt"] = (500, "error", "text/plain")
        fetcher, session = self.fetcher(pages)
        with self.assertRaisesRegex(FetchRefused, "not readable"):
            fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertEqual(session.requested, [f"https://{HOST}/robots.txt"])

    def test_outside_visit_time_is_refused(self):
        fetcher, session = self.fetcher(self.pages(robots=ROBOTS + "Visit-time: 0100-0200\n"))
        with self.assertRaisesRegex(FetchRefused, "Visit-time"):
            fetcher.fetch_game(SCHEDULE, "Example Tech", "2027-01-10", self.folder / "raw")
        self.assertEqual(session.requested, [f"https://{HOST}/robots.txt"])


if __name__ == "__main__":
    unittest.main()
