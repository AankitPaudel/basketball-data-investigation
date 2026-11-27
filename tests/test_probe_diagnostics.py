import contextlib
import io
import json
import socket
import ssl
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from requests.structures import CaseInsensitiveDict

from src.common.http_client import HttpClient, resolve_destination
from src.common.probe import NCAA_ROBOTS_URL, Probe, load_config, plan_requests, request_window
from src.common.probe_comparison import compare_runs
from src.main import main
from tests import test_probe as probe_fixtures

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ncaa_robots_probe.json"


def prepared_response(status=200, body="User-agent: *\nDisallow: /\n", headers=None):
    result = requests.Response()
    result.status_code = status
    result.headers = CaseInsensitiveDict(headers or {"Content-Type": "text/plain"})
    result._content = body.encode("utf-8")
    result._content_consumed = True
    result.request = requests.Request("GET", NCAA_ROBOTS_URL,
        headers={"User-Agent": "BasketballDataResearch/0.1", "Accept": "text/html",
                 "Authorization": "Bearer secret-auth", "Cookie": "session=secret-session",
                 "Proxy-Authorization": "Basic secret-proxy"}).prepare()
    sock = Mock()
    sock.getpeername.return_value = ("8.8.8.8", 443)
    result.raw = SimpleNamespace(version=11, connection=SimpleNamespace(sock=sock))
    return result


class DiagnosticHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / "http.jsonl"
        self.session = Mock()
        self.client = HttpClient("ncaa", log_path=self.log, session=self.session, retries=0,
                                 capture_diagnostics=True)
        resolver = patch("src.common.http_client.socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.8", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0))])
        self.resolver = resolver.start()
        self.addCleanup(resolver.stop)

    def test_classification_and_http_metadata(self):
        cases = [(200, "text/plain", "User-agent: *", "success"),
                 (403, "text/html", "<title>Access Denied</title>", "access_denial"),
                 (403, "text/html", "<title>Verify you are human</title>", "verification_challenge"),
                 (429, "text/plain", "Slow down", "rate_limited"),
                 (503, "text/plain", "Unavailable", "temporary_server_error")]
        for status, media, body, classification in cases:
            with self.subTest(status=status, classification=classification):
                self.session.get.reset_mock()
                self.session.get.return_value = prepared_response(status, body, {"Content-Type": media})
                report = self.client.fetch(NCAA_ROBOTS_URL).report
                self.assertEqual(report["classification"], classification)
                self.assertEqual(report["status_code"], status)
                attempt = report["attempts"][0]
                self.assertEqual(attempt["http_version"], "HTTP/1.1")
                self.assertEqual(attempt["peer_address"], "8.8.8.8")
                self.assertEqual(attempt["dns"]["ipv4"], ["8.8.8.8", "REDACTED_NON_PUBLIC"])
                self.assertEqual(attempt["dns"]["ipv6"], ["2001:4860:4860::8888"])
                self.assertEqual(attempt["destination_hostname"], "stats.ncaa.org")
                self.assertEqual(attempt["method"], "GET")
                self.session.get.assert_called_once()

    def test_header_and_reference_sanitization(self):
        self.session.get.return_value = prepared_response(403, "<p>Access Denied. Reference #9.6f64d440.1318965461.2f2b078</p>",
            {"Content-Type": "text/html; private=secret-param", "Server": "AkamaiGHost/secret-build",
             "Set-Cookie": "session=secret-set-cookie", "WWW-Authenticate": "Basic realm=secret-realm",
             "X-Api-Key": "secret-key", "Via": "1.1 secret-proxy-node", "Location": "/token/secret-route"})
        result = self.client.fetch(NCAA_ROBOTS_URL)
        attempt = result.report["attempts"][0]
        self.assertEqual(attempt["request_headers"], {"user-agent": "BasketballDataResearch/0.1", "accept": "text/html"})
        self.assertEqual(attempt["response_headers"], {"content-type": "text/html", "server": ["akamaighost"],
                                                      "location": "present", "via": "present"})
        self.assertEqual(attempt["reference_ids"], {"edge_error_reference": "9.6f64d440.1318965461.2f2b078"})
        self.assertNotIn("secret-", self.log.read_text(encoding="utf-8"))

    def test_network_error_categories(self):
        for error, category in ((requests.ConnectionError(socket.gaierror(11001, "private.example")), "dns_error"),
                                (requests.ConnectionError("unreachable"), "connection_error"),
                                (requests.exceptions.SSLError("certificate failure"), "tls_error")):
            with self.subTest(category=category):
                self.session.get.side_effect = error
                self.assertEqual(self.client.fetch(NCAA_ROBOTS_URL).report["classification"], category)
        self.resolver.side_effect = socket.gaierror(11001, "private.example")
        self.assertEqual(resolve_destination("stats.ncaa.org"), {"result": "dns_error", "error_code": 11001})
        self.assertNotIn("private.example", self.log.read_text(encoding="utf-8"))

    def test_captured_redirect_does_not_log_path_or_query_values(self):
        self.session.get.side_effect = [prepared_response(302, "", {"Location": "/login/secret-path?x=secret-query"}),
                                        prepared_response(403)]
        result = self.client.fetch(NCAA_ROBOTS_URL)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(result.report["redirect_history"][0]["to"], "https://stats.ncaa.org/REDACTED")
        self.assertNotIn("secret-", self.log.read_text(encoding="utf-8"))

    def test_received_403_remains_a_denial_when_body_read_fails(self):
        reply = prepared_response(403)
        reply._content = False
        reply._content_consumed = False
        reply.raw = Mock(version=11, connection=None)
        reply.raw.stream.side_effect = requests.ConnectionError("truncated response")
        self.session.get.return_value = reply
        report = self.client.fetch(NCAA_ROBOTS_URL).report
        self.assertEqual((report["status_code"], report["outcome"], report["classification"]),
                         (403, "forbidden", "access_denial"))
        self.assertEqual(report["error_category"], "connection_error")
        self.session.get.assert_called_once()


class ScopedProbeTests(probe_fixtures.ProbeTestCase):
    def test_scoped_mode_dry_run_has_no_http_or_dns(self):
        with patch("requests.Session.get") as get, patch("socket.getaddrinfo") as resolve, \
                patch.dict("os.environ", {"HTTPS_PROXY": "https://name:secret-proxy@example.org"}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["probe", "--config", str(CONFIG), "--capture-diagnostics", "--environment", "unit-test",
                         "--network-asn", "64496", "--network-source", "existing-network-record"])
        self.assertEqual(code, 0)
        get.assert_not_called()
        resolve.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual((plan["robots"], plan["max_requests"]), ([NCAA_ROBOTS_URL], 1))
        self.assertTrue(plan["proxy_environment_configured"])
        self.assertEqual(plan["network_provider"]["source"], "existing-network-record")
        self.assertNotIn("secret-proxy", output.getvalue())

    def test_workflow_defaults_to_scoped_config(self):
        workflow = (ROOT / ".github" / "workflows" / "access-probe.yml").read_text(encoding="utf-8")
        self.assertIn("PROBE_CONFIG: config/ncaa_robots_probe.json", workflow)
        self.assertNotIn("config/access_probe_urls.json", workflow)
        self.assertIn("probe --execute --config", workflow)
        self.assertIn('--window-start-utc "$PROBE_WINDOW_START"', workflow)
        self.assertIn("path: probe-logs/", workflow)

    def test_expired_window_writes_zero_request_result_without_dns_or_http(self):
        start = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with patch("requests.Session.get") as get, patch("socket.getaddrinfo") as resolve, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["probe", "--config", str(CONFIG), "--capture-diagnostics", "--environment", "unit-test",
                         "--window-start-utc", start, "--output", str(self.directory), "--execute"])
        self.assertEqual(code, 3)
        get.assert_not_called()
        resolve.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["requests_sent"], 0)
        self.assertEqual(result["robots"]["stats.ncaa.org"]["outcome"], "window_closed")
        record = json.loads(Path(result["log"]).read_text(encoding="utf-8").splitlines()[1])
        self.assertEqual(record["window_closed_stage"], "before_dns")
        self.assertEqual(record["attempts"], [])

    def test_window_expiring_during_dns_prevents_http(self):
        start, end = request_window(load_config(CONFIG), "2026-11-26T16:00:00Z")
        client = HttpClient("ncaa", log_path=self.directory / "http.jsonl", session=Mock(), capture_diagnostics=True)
        with patch("src.common.http_client.utc_now", side_effect=[start.isoformat(), start.isoformat(), end.isoformat()]), \
                patch("src.common.http_client.resolve_destination", return_value={"result": "resolved"}) as resolve:
            report = client.fetch(NCAA_ROBOTS_URL, request_window=(start, end)).report
        self.assertEqual(report["classification"], "not_requested")
        self.assertEqual(report["window_closed_stage"], "before_http")
        self.assertEqual(report["attempts"], [])
        resolve.assert_called_once_with("stats.ncaa.org")
        client.session.get.assert_not_called()

    def test_scoped_probe_records_transport_evidence_once(self):
        session = requests.Session()
        self.addCleanup(session.close)
        session.trust_env = False
        reply = prepared_response(403, "<p>Access Denied</p><p>session=body-secret</p>", {
            "Content-Type": "text/html", "Server": "cloudflare", "CF-Ray": "230b030023ae2822-SJC",
            "Set-Cookie": "session=response-secret"})
        reply.url = NCAA_ROBOTS_URL
        adapter = session.get_adapter(NCAA_ROBOTS_URL)
        resolver = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        start_value = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window = request_window(load_config(CONFIG), start_value)

        def transport(request, **_options):
            reply.request = request
            return reply

        with patch.object(adapter, "send", side_effect=transport) as send, \
                patch("socket.getaddrinfo", return_value=resolver):
            probe = Probe(load_config(CONFIG), environment="unit-test", output=self.directory, session=session,
                          capture_diagnostics=True, environ={"HTTPS_PROXY": "https://proxy-secret@example.org"},
                          window=window)
            summary = probe.run()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0].method, "GET")
        self.assertEqual(send.call_args.args[0].url, NCAA_ROBOTS_URL)
        self.assertEqual(send.call_args.kwargs["timeout"], 20)
        events = Path(summary["log"]).read_text(encoding="utf-8")
        start, event, end = map(json.loads, events.splitlines())
        self.assertEqual(start["openssl"], ssl.OPENSSL_VERSION)
        self.assertEqual(start["ipv6_socket_support"], socket.has_ipv6)
        self.assertEqual(start["request_config"]["configured_headers"], {"User-Agent": "BasketballDataResearch/0.1", "Accept": "text/html"})
        self.assertFalse(start["request_config"]["follow_redirects"])
        self.assertTrue(start["proxy_environment_configured"])
        for field in ("environment", "platform", "python", "requests", "urllib3"):
            self.assertTrue(start[field], field)
        attempt = event["attempts"][0]
        request_started = datetime.fromisoformat(attempt["request_started_at"])
        self.assertGreaterEqual(request_started, datetime.fromisoformat(event["checked_at"]))
        self.assertLess(request_started, datetime.fromisoformat(start["request_window_utc"]["end_exclusive"]))
        self.assertEqual(request_started.utcoffset(), timedelta(0))
        self.assertGreaterEqual(attempt["elapsed_seconds"], 0)
        self.assertEqual(attempt["response_bytes"], len(reply.content))
        self.assertEqual(attempt["body_sha256"], sha256(reply.content).hexdigest())
        self.assertEqual(attempt["response_signals"], ["access_denied_text"])
        self.assertEqual(attempt["reference_ids"], {"cf_ray": "230b030023ae2822-SJC"})
        self.assertEqual(attempt["request_headers"]["user-agent"], "BasketballDataResearch/0.1")
        self.assertEqual(attempt["response_headers"]["server"], ["cloudflare"])
        self.assertEqual((attempt["http_version"], attempt["peer_address"]), ("HTTP/1.1", "8.8.8.8"))
        self.assertEqual(attempt["dns"]["ipv4"], ["8.8.8.8"])
        self.assertEqual(end["stopped_hosts"], {"stats.ncaa.org": "forbidden"})
        self.assertNotIn("-secret", events)

    def test_scoped_redirect_retains_redacted_location_without_following(self):
        locations = [("/contests/private-path?token=private-token", "stats.ncaa.org"),
                     ("https://private-name.example/private-path", "redacted-host")]
        for location, recorded_host in locations:
            with self.subTest(location=location), \
                    patch("src.common.http_client.resolve_destination", return_value={"result": "no_addresses"}):
                session = Mock()
                session.get.return_value = prepared_response(302, "", {"Location": location})
                probe = Probe(load_config(CONFIG), environment="unit-test", output=self.directory, session=session,
                              capture_diagnostics=True)
                probe.run()
                session.get.assert_called_once()
                contents = probe.log_path.read_text(encoding="utf-8")
                self.assertNotIn("private-", contents)
                event = json.loads(contents.splitlines()[1])
                self.assertEqual(event["redirect_history"][0], {"from": NCAA_ROBOTS_URL,
                    "to": f"https://{recorded_host}/REDACTED", "status_code": 302, "followed": False})

    def test_scoped_mode_does_not_follow_redirects(self):
        config = load_config(CONFIG)
        self.assertEqual(plan_requests(config)["max_requests"], 1)
        probe, session, _, records = self.run_probe(config, {
            NCAA_ROBOTS_URL: probe_fixtures.Response(302, "", Location="/contests/1/box_score")})
        self.assertEqual([call["url"] for call in session.calls], [NCAA_ROBOTS_URL])
        self.assertEqual(records[1]["outcome"], "redirect_not_followed")
        self.assertEqual(probe.robots["stats.ncaa.org"]["decision"], "unavailable_fail_closed")

    def test_denied_redirect_destination_stops_for_entire_run(self):
        a, b, c = (f"https://{name}.example.org" for name in "abc")
        config = self.config([f"{name}.example.org" for name in "abc"], [a + "/one", b + "/two", c + "/three"])
        reply = probe_fixtures.Response
        routes = {base + "/robots.txt": probe_fixtures.robots(probe_fixtures.ALLOW_ALL) for base in (a, b, c)}
        probe, session, _, _ = self.run_probe(config, {**routes,
            a + "/one": reply(302, "", Location=b + "/deny"), b + "/deny": reply(403, "Access denied"),
            c + "/three": reply(302, "", Location=b + "/later")})
        self.assertEqual(probe.stopped["b.example.org"], "forbidden")
        self.assertFalse({b + "/two", b + "/later"}.intersection(call["url"] for call in session.calls))

    def test_header_challenge_on_robots_fails_closed(self):
        for status, headers, outcome in ((200, {"Cf-Mitigated": "challenge"}, "access_challenge"),
                                         (403, {}, "forbidden"), (429, {"Retry-After": "120"}, "rate_limited")):
            with self.subTest(status=status), \
                    patch("src.common.http_client.resolve_destination", return_value={"result": "no_addresses"}):
                session = Mock()
                session.get.return_value = prepared_response(status, probe_fixtures.ALLOW_ALL,
                    {"Content-Type": "text/plain", **headers})
                probe = Probe(load_config(CONFIG), environment="unit-test", output=self.directory, session=session,
                              capture_diagnostics=True)
                summary = probe.run()
                session.get.assert_called_once()
                self.assertEqual(summary["stopped_hosts"], {"stats.ncaa.org": outcome})
                self.assertEqual(summary["robots"]["stats.ncaa.org"]["decision"], "unavailable_fail_closed")


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.left = self.directory / "local.jsonl"
        self.right = self.directory / "cloud.jsonl"
        self.write(self.left, "local", 200)
        self.write(self.right, "cloud", 403, legacy=True)

    def write(self, path, environment, status, legacy=False, details=None):
        entry = {"environment": environment, "source": "probe", "kind": "robots", "run_id": environment,
                 "requested_url": NCAA_ROBOTS_URL, "status_code": status,
                 "attempts": [{"url": NCAA_ROBOTS_URL, "status_code": status, **(details or {})}],
                 "outcome": "non_html_response" if status == 200 else "forbidden"}
        if legacy:
            entry["challenge_detected"] = True
        path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    def test_legacy_metadata_is_missing_and_flag_does_not_prove_challenge(self):
        report = compare_runs(self.left, self.right)
        right = report["right"]["focus_observations"][0]
        self.assertTrue(right["legacy_challenge_flag"])
        self.assertEqual(right["classification"], "access_denial")
        self.assertIn("response_signals", right["attempts"][0]["missing_fields"])
        self.assertIn("dns", right["attempts"][0]["missing_fields"])
        self.assertIn("platform", report["right"]["missing_environment_fields"])
        self.assertEqual(report["right"]["source"]["run_id"], "cloud")
        self.assertEqual(right["line"], 1)
        self.assertEqual(report["exact_root_cause"], "undetermined")

    def test_provider_indicator_and_dns_differences_do_not_attribute_rule(self):
        dns = {"result": "resolved", "ipv4": ["8.8.8.8"], "ipv6": []}
        self.write(self.left, "local", 200, details={"dns": dns, "response_headers": {"server": ["nginx"]}})
        self.write(self.right, "cloud", 403, details={"dns": {**dns, "ipv4": ["1.1.1.1"]},
            "response_headers": {"Server": "AkamaiGHost", "Set-Cookie": "secret-value"}})
        report = compare_runs(self.left, self.right)
        self.assertIn("dns", report["focus_differences"])
        self.assertIn("response_headers", report["focus_differences"])
        self.assertEqual(report["exact_root_cause"], "undetermined")
        self.assertNotIn("secret-value", json.dumps(report))

    def test_comparison_uses_actual_http_attempt_times(self):
        self.write(self.left, "local", 200, details={"request_started_at": "2026-11-26T16:00:01.123+00:00"})
        self.write(self.right, "cloud", 403, details={"request_started_at": "2026-11-26T16:00:12.456+00:00"})
        report = compare_runs(self.left, self.right)
        self.assertEqual(report["request_start_gap_seconds"], 11.333)

    def test_window_closed_is_not_an_observed_denial(self):
        record = json.loads(self.right.read_text(encoding="utf-8"))
        record.update(status_code=None, outcome="window_closed", classification="not_requested", attempts=[],
                      window_closed_stage="before_http")
        self.right.write_text(json.dumps(record) + "\n", encoding="utf-8")
        report = compare_runs(self.left, self.right)
        self.assertEqual(report["observation"], "Insufficient comparable records for the selected URL.")
        self.assertEqual(report["right"]["focus_observations"][0]["window_closed_stage"], "before_http")

    def test_offline_cli_writes_comparison(self):
        output = self.directory / "comparison.json"
        with patch("requests.Session.get") as get, patch("socket.getaddrinfo") as resolve, \
                contextlib.redirect_stdout(io.StringIO()):
            code = main(["compare-probes", str(self.left), str(self.right), "--output", str(output)])
        self.assertEqual(code, 0)
        get.assert_not_called()
        resolve.assert_not_called()
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("status_code", report["focus_differences"])
        self.assertEqual(report["right"]["source"]["file"], self.right.name)


if __name__ == "__main__":
    unittest.main()
