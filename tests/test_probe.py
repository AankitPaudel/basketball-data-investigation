"""Offline tests for the Stage 3 access probe. No test touches the network: a fake
session answers every request, and a fake clock makes the delays observable."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from src.common.probe import MAX_REQUESTS, Probe, load_config, plan_requests, runner_metadata
from src.main import main

ROOT = Path(__file__).resolve().parents[1]
DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
ALLOW_ALL = "User-agent: *\nDisallow:\n"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, status=200, body="<html><body><table></table></body></html>", content_type="text/html", **headers):
        self.status_code, self.text = status, body
        self.content = body.encode("utf-8")
        self.headers = {"Content-Type": content_type, **headers}


class Session:
    """Answers by URL; a callable or exception value is evaluated per request."""

    def __init__(self, routes, clock):
        self.routes, self.clock, self.calls = routes, clock, []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, timeout=None, headers=None, allow_redirects=None):
        self.calls.append({"url": url, "at": self.clock.now, "headers": headers, "allow_redirects": allow_redirects})
        self.cookies.set("session", "abc")
        answer = self.routes.get(url)
        if answer is None:
            raise AssertionError(f"Unexpected request: {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer() if callable(answer) else answer


def robots(body, status=200):
    return Response(status, body, "text/plain")


class ProbeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.clock = Clock()

    def config(self, hosts, urls, **overrides):
        data = {"schema_version": 1, "hosts": hosts, "rounds": 1, "min_delay_seconds": 10,
                "round_interval_seconds": 600, **overrides,
                "urls": [{"url": url, "purpose": f"test {index}",
                          **({"policy_only": True} if "//stats.ncaa.org/" in url else {})}
                         for index, url in enumerate(urls)]}
        path = self.directory / f"config-{len(list(self.directory.glob('config-*')))}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_config(path)

    def run_probe(self, config, routes, environ=None):
        session = Session(routes, self.clock)
        probe = Probe(config, environment="unit-test", region="unknown", output=self.directory / "logs",
                      session=session, clock=self.clock, sleep=self.clock.sleep, environ=environ or {})
        summary = probe.run()
        records = [json.loads(line) for line in probe.log_path.read_text(encoding="utf-8").splitlines()]
        return probe, session, summary, records

    def targets(self, probe):
        return {result["requested_url"]: result for result in probe.results}


class RobotsTests(ProbeTestCase):
    def test_disallowed_target_is_never_requested(self):
        config = self.config(["stats.ncaa.org"], ["https://stats.ncaa.org/contests/6643366/individual_stats"])
        probe, session, summary, _ = self.run_probe(config, {"https://stats.ncaa.org/robots.txt": robots(DISALLOW_ALL)})
        self.assertEqual([call["url"] for call in session.calls], ["https://stats.ncaa.org/robots.txt"])
        result = probe.results[0]
        self.assertEqual((result["outcome"], result["target_requested"]), ("robots_disallowed", False))
        self.assertEqual(summary["requests_sent"], 1)

    def test_stats_ncaa_pages_never_requested_even_if_robots_allows(self):
        config = self.config(["stats.ncaa.org"], ["https://stats.ncaa.org/contests/6643366/individual_stats"])
        probe, session, _, records = self.run_probe(config, {"https://stats.ncaa.org/robots.txt": robots(ALLOW_ALL)})
        self.assertEqual([call["url"] for call in session.calls], ["https://stats.ncaa.org/robots.txt"])
        result = probe.results[0]
        self.assertEqual((result["outcome"], result["robots_allows"], result["target_requested"]),
                         ("policy_only_not_requested", True, False))

    def test_stats_ncaa_urls_must_be_policy_only(self):
        path = self.directory / "unmarked.json"
        path.write_text(json.dumps({"schema_version": 1, "hosts": ["stats.ncaa.org"], "rounds": 1,
            "urls": [{"url": "https://stats.ncaa.org/contests/1/box_score", "purpose": "unmarked"}]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must be marked policy_only"):
            load_config(path)

    def test_redirect_into_stats_ncaa_is_blocked(self):
        config = self.config(["a.example.org", "stats.ncaa.org"], ["https://a.example.org/go"])
        probe, session, _, _ = self.run_probe(config, {
            "https://a.example.org/robots.txt": robots(ALLOW_ALL), "https://stats.ncaa.org/robots.txt": robots(ALLOW_ALL),
            "https://a.example.org/go": Response(302, "", Location="https://stats.ncaa.org/contests/1/box_score")})
        self.assertEqual(probe.results[0]["outcome"], "robots_disallowed_redirect")
        self.assertNotIn("https://stats.ncaa.org/contests/1/box_score", [c["url"] for c in session.calls])

    def test_path_rules_are_respected(self):
        config = self.config(["a.example.org"], ["https://a.example.org/public/page", "https://a.example.org/private/page"])
        probe, session, _, _ = self.run_probe(config, {
            "https://a.example.org/robots.txt": robots("User-agent: *\nDisallow: /private/\n"),
            "https://a.example.org/public/page": Response()})
        self.assertNotIn("https://a.example.org/private/page", [call["url"] for call in session.calls])
        results = self.targets(probe)
        self.assertEqual(results["https://a.example.org/public/page"]["outcome"], "html_available")
        self.assertEqual(results["https://a.example.org/private/page"]["outcome"], "robots_disallowed")

    def test_missing_robots_file_allows(self):
        config = self.config(["a.example.org"], ["https://a.example.org/page"])
        probe, _, _, _ = self.run_probe(config, {"https://a.example.org/robots.txt": robots("", 404),
                                                 "https://a.example.org/page": Response()})
        self.assertEqual(probe.robots["a.example.org"]["decision"], "no_robots_file_allowed")
        self.assertEqual(probe.results[0]["outcome"], "html_available")

    def test_unreadable_robots_fails_closed(self):
        cases = {"server error": robots("oops", 500), "html instead of text": Response(200, "<html>robots</html>"),
                 "network error": requests.ConnectionError("down")}
        for label, answer in cases.items():
            with self.subTest(label):
                config = self.config(["a.example.org"], ["https://a.example.org/page"])
                probe, session, _, _ = self.run_probe(config, {"https://a.example.org/robots.txt": answer})
                self.assertEqual(probe.robots["a.example.org"]["decision"], "unavailable_fail_closed")
                self.assertEqual(len(session.calls), 1)
                self.assertFalse(probe.results[0]["target_requested"])

    def test_forbidden_robots_stops_the_host(self):
        config = self.config(["a.example.org"], ["https://a.example.org/page"])
        probe, session, _, _ = self.run_probe(config, {"https://a.example.org/robots.txt": robots("no", 403)})
        self.assertEqual(probe.stopped, {"a.example.org": "forbidden"})
        self.assertEqual(probe.results[0]["outcome"], "skipped_host_stopped")
        self.assertEqual(len(session.calls), 1)


class StopRuleTests(ProbeTestCase):
    def two_host_config(self, rounds=2):
        return self.config(["a.example.org", "b.example.org"],
                           ["https://a.example.org/one", "https://a.example.org/two", "https://b.example.org/one"],
                           rounds=rounds)

    def base_routes(self):
        return {"https://a.example.org/robots.txt": robots(ALLOW_ALL),
                "https://b.example.org/robots.txt": robots(ALLOW_ALL),
                "https://b.example.org/one": Response()}

    def test_403_stops_host_without_retry_and_other_hosts_continue(self):
        probe, session, _, _ = self.run_probe(self.two_host_config(),
                                              {**self.base_routes(), "https://a.example.org/one": Response(403, "denied")})
        urls = [call["url"] for call in session.calls]
        self.assertEqual(urls.count("https://a.example.org/one"), 1)
        self.assertNotIn("https://a.example.org/two", urls)
        self.assertEqual(urls.count("https://b.example.org/one"), 2)
        outcomes = [r["outcome"] for r in probe.results if r["host"] == "a.example.org"]
        self.assertEqual(outcomes, ["forbidden", "skipped_host_stopped", "skipped_host_stopped", "skipped_host_stopped"])

    def test_429_and_retry_after_stop_the_host(self):
        for answer, outcome in ((Response(429, "slow down"), "rate_limited"),
                                (Response(503, "busy", **{"Retry-After": "120"}), "server_requested_backoff")):
            with self.subTest(outcome):
                self.clock = Clock()
                probe, session, _, _ = self.run_probe(self.two_host_config(),
                                                      {**self.base_routes(), "https://a.example.org/one": answer})
                self.assertEqual([c["url"] for c in session.calls].count("https://a.example.org/one"), 1)
                self.assertEqual(probe.stopped["a.example.org"], outcome)

    def test_server_error_is_one_attempt_without_retry(self):
        probe, session, _, _ = self.run_probe(self.two_host_config(rounds=1),
            {**self.base_routes(), "https://a.example.org/one": Response(503, "busy"), "https://a.example.org/two": Response()})
        self.assertEqual([c["url"] for c in session.calls].count("https://a.example.org/one"), 1)
        self.assertNotIn("a.example.org", probe.stopped)
        self.assertEqual(self.targets(probe)["https://a.example.org/two"]["outcome"], "html_available")

    def test_captcha_page_is_recorded_and_stops_the_host(self):
        challenge = Response(200, "<html><title>Just a moment...</title><body>Verify you are human</body></html>")
        probe, session, _, _ = self.run_probe(self.two_host_config(rounds=1),
                                              {**self.base_routes(), "https://a.example.org/one": challenge})
        self.assertEqual(self.targets(probe)["https://a.example.org/one"]["outcome"], "access_challenge")
        self.assertEqual(probe.stopped["a.example.org"], "access_challenge")
        self.assertNotIn("https://a.example.org/two", [c["url"] for c in session.calls])

    def test_challenge_behind_403_is_flagged(self):
        page = Response(403, "<html><title>Access Denied</title><body>Access Denied</body></html>")
        probe, _, _, _ = self.run_probe(self.two_host_config(rounds=1), {**self.base_routes(), "https://a.example.org/one": page})
        result = self.targets(probe)["https://a.example.org/one"]
        self.assertEqual((result["outcome"], result["challenge_detected"]), ("forbidden", True))

    def test_login_page_stops_the_host(self):
        login = Response(200, '<html><body><form><input type="password" name="p"></form></body></html>')
        probe, _, _, _ = self.run_probe(self.two_host_config(rounds=1), {**self.base_routes(), "https://a.example.org/one": login})
        self.assertEqual(probe.stopped["a.example.org"], "login_required")

    def test_redirects_stay_inside_allowlist_and_robots(self):
        config = self.config(["a.example.org"], ["https://a.example.org/out", "https://a.example.org/in"])
        probe, session, _, _ = self.run_probe(config, {
            "https://a.example.org/robots.txt": robots("User-agent: *\nDisallow: /private/\n"),
            "https://a.example.org/out": Response(302, "", Location="https://elsewhere.example.com/x"),
            "https://a.example.org/in": Response(302, "", Location="/private/x")})
        results = self.targets(probe)
        self.assertEqual(results["https://a.example.org/out"]["outcome"], "redirect_outside_allowed_hosts")
        self.assertEqual(results["https://a.example.org/in"]["outcome"], "robots_disallowed_redirect")
        self.assertNotIn("https://elsewhere.example.com/x", [c["url"] for c in session.calls])
        self.assertNotIn("https://a.example.org/private/x", [c["url"] for c in session.calls])


class DelayTests(ProbeTestCase):
    def test_same_host_spacing_and_round_interval(self):
        config = self.config(["a.example.org", "b.example.org"],
                             ["https://a.example.org/1", "https://a.example.org/2", "https://a.example.org/3",
                              "https://b.example.org/1"], rounds=2, min_delay_seconds=12, round_interval_seconds=900)
        routes = {"https://a.example.org/robots.txt": robots(ALLOW_ALL), "https://b.example.org/robots.txt": robots(ALLOW_ALL),
                  **{f"https://a.example.org/{n}": Response() for n in (1, 2, 3)}, "https://b.example.org/1": Response()}
        _, session, _, _ = self.run_probe(config, routes)
        for host in ("a.example.org", "b.example.org"):
            times = [call["at"] for call in session.calls if f"//{host}/" in call["url"]]
            gaps = [later - earlier for earlier, later in zip(times, times[1:])]
            self.assertTrue(gaps and all(gap >= 12 for gap in gaps), (host, gaps))
        self.assertIn(900, self.clock.sleeps)
        round_two = [call["at"] for call in session.calls if call["url"] == "https://b.example.org/1"]
        self.assertGreaterEqual(round_two[1] - round_two[0], 900)

    def test_interleaving_avoids_waits_across_hosts(self):
        config = self.config(["a.example.org", "b.example.org"], ["https://a.example.org/1", "https://b.example.org/1"])
        routes = {"https://a.example.org/robots.txt": robots(ALLOW_ALL), "https://b.example.org/robots.txt": robots(ALLOW_ALL),
                  "https://a.example.org/1": Response(), "https://b.example.org/1": Response()}
        _, session, _, _ = self.run_probe(config, routes)
        self.assertEqual([c["url"] for c in session.calls][2:], ["https://a.example.org/1", "https://b.example.org/1"])


class LoggingTests(ProbeTestCase):
    def test_jsonl_and_summary_fields(self):
        config = self.config(["a.example.org", "stats.ncaa.org"],
                             ["https://a.example.org/page", "https://stats.ncaa.org/contests/1/box_score"])
        environ = {"RUNNER_OS": "Linux", "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_TOKEN": "secret-token",
                   "ImageOS": "ubuntu24"}
        probe, session, summary, records = self.run_probe(config, {
            "https://a.example.org/robots.txt": robots(ALLOW_ALL),
            "https://stats.ncaa.org/robots.txt": robots(DISALLOW_ALL),
            "https://a.example.org/page": Response()}, environ)
        kinds = [record["kind"] for record in records]
        self.assertEqual((kinds[0], kinds[-1]), ("run_start", "run_end"))
        self.assertEqual(kinds.count("robots"), 2)
        start = records[0]
        self.assertEqual(start["region"], "unknown")
        self.assertEqual(start["runner"], {"RUNNER_OS": "Linux", "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24"})
        self.assertNotIn("secret-token", probe.log_path.read_text(encoding="utf-8"))
        target = next(r for r in records if r["kind"] == "target" and r.get("status_code") == 200)
        for field in ("run_id", "environment", "region", "round", "host", "purpose", "checked_at",
                      "status_code", "content_type", "response_bytes", "elapsed_seconds", "attempts"):
            self.assertIn(field, target)
        self.assertEqual(target["attempts"][0]["body_sha256"], __import__("hashlib").sha256(Response().content).hexdigest())
        blocked = next(r for r in records if r["kind"] == "target" and r["host"] == "stats.ncaa.org")
        self.assertEqual((blocked["outcome"], blocked["target_requested"]), ("robots_disallowed", False))
        with Path(summary["summary"]).open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["outcomes"] for row in rows], ["html_available", "robots_disallowed"])
        self.assertEqual(rows[1]["attempts"], "0")
        self.assertEqual(len(rows[0]["robots_sha256"]), 64)
        self.assertEqual(records[-1]["requests_sent"], 3)

    def test_identifies_honestly_and_keeps_no_cookies(self):
        config = self.config(["a.example.org"], ["https://a.example.org/1", "https://a.example.org/2"])
        _, session, _, _ = self.run_probe(config, {"https://a.example.org/robots.txt": robots(ALLOW_ALL),
                                                   "https://a.example.org/1": Response(), "https://a.example.org/2": Response()})
        self.assertTrue(all(call["headers"]["User-Agent"] == "BasketballDataResearch/0.1" for call in session.calls))
        self.assertTrue(all(call["allow_redirects"] is False for call in session.calls))
        self.assertEqual(len(session.cookies), 0)

    def test_runner_metadata_uses_only_allowlisted_variables(self):
        meta = runner_metadata(None, {"RUNNER_NAME": "GitHub Actions 2", "AWS_SECRET_ACCESS_KEY": "x", "PATH": "/bin"})
        self.assertEqual((meta["region"], meta["runner"]), ("unknown", {"RUNNER_NAME": "GitHub Actions 2"}))


class ConfigTests(ProbeTestCase):
    def test_invalid_configs_are_rejected(self):
        cases = {
            "plain http": (["a.example.org"], ["http://a.example.org/x"], {}),
            "host not allowlisted": (["a.example.org"], ["https://b.example.org/x"], {}),
            "wildcard host": (["*.example.org"], ["https://a.example.org/x"], {}),
            "short delay": (["a.example.org"], ["https://a.example.org/x"], {"min_delay_seconds": 5}),
            "short round interval": (["a.example.org"], ["https://a.example.org/x"], {"round_interval_seconds": 60}),
            "too many rounds": (["a.example.org"], ["https://a.example.org/x"], {"rounds": 4}),
            "secret query value": (["a.example.org"], ["https://a.example.org/x?token=abc"], {}),
            "credentials": (["a.example.org"], ["https://user:pw@a.example.org/x"], {}),
            "duplicate url": (["a.example.org"], ["https://a.example.org/x", "https://a.example.org/x"], {}),
            "too many requests": (["a.example.org"], [f"https://a.example.org/{n}" for n in range(30)], {"rounds": 3}),
        }
        for label, (hosts, urls, extra) in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.config(hosts, urls, **extra)

    def test_output_inside_data_is_rejected(self):
        config = self.config(["a.example.org"], ["https://a.example.org/x"])
        with self.assertRaisesRegex(ValueError, "must not be inside data/"):
            Probe(config, environment="unit-test", output=Path("data") / "probe", session=Session({}, self.clock))

    def test_committed_config(self):
        config = load_config(ROOT / "config" / "access_probe_urls.json")
        plan = plan_requests(config)
        self.assertEqual((len(config["hosts"]), len(config["urls"]), config["rounds"]), (7, 12, 3))
        self.assertEqual(plan["max_requests"], 37)
        self.assertLessEqual(plan["max_requests"], MAX_REQUESTS)
        self.assertEqual(len(plan["policy_only_never_requested"]), 2)
        self.assertFalse(any("stats.ncaa.org" in url for round_urls in plan["rounds"] for url in round_urls))
        self.assertEqual((config["min_delay_seconds"], config["round_interval_seconds"]), (10, 600))
        stats = [item["url"] for item in config["urls"] if "stats.ncaa.org" in item["url"]]
        self.assertEqual(len(stats), 2)


class WorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_read_only_and_gated(self):
        text = (ROOT / ".github" / "workflows" / "access-probe.yml").read_text(encoding="utf-8")
        triggers = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", triggers)
        for automatic in ("push:", "pull_request", "schedule:", "workflow_run:", "repository_dispatch:"):
            self.assertNotIn(automatic, triggers)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("PROBE_REGION: unknown", text)
        execute_lines = [line for line in text.splitlines() if "--execute" in line]
        self.assertEqual(len(execute_lines), 1)
        steps = text.split("- name: ")
        gated = [step for step in steps if "--execute" in step]
        self.assertEqual(len(gated), 1)
        self.assertIn("if: ${{ inputs.approve_network_requests == 'APPROVED' && github.run_attempt == '1' }}", gated[0])
        self.assertIn('default: "no"', text)


class CommandTests(unittest.TestCase):
    def test_dry_run_is_default_and_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch("requests.Session.get") as network, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            status = main(["probe", "--environment", "unit-test", "--output", directory])
            self.assertEqual(status, 0)
            network.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
        plan = json.loads(out.getvalue())
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["max_requests"], 37)

    def test_environment_label_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["probe"])

    def test_validated_csvs_are_not_touched(self):
        csvs = sorted((ROOT / "data" / "processed").glob("ncaa-game-*/*_LOCAL_PREVIEW.csv"))
        before = {path: path.read_bytes() for path in csvs}
        with tempfile.TemporaryDirectory() as directory, patch("requests.Session.get"), \
                contextlib.redirect_stdout(io.StringIO()):
            main(["probe", "--environment", "unit-test", "--output", directory])
        self.assertEqual({path: path.read_bytes() for path in csvs}, before)


if __name__ == "__main__":
    unittest.main()
