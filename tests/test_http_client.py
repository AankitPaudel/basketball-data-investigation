import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from src.common.http_client import HttpClient


def response(status=200, html="<table></table>", **headers):
    return Mock(status_code=status, text=html, headers={"Content-Type": "text/html", **headers})


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = Mock()
        self.log = Path(self.temp.name) / "requests.jsonl"
        self.client = HttpClient("ncaa", log_path=self.log, session=self.session, sleep=Mock())

    def test_stops_on_restriction_without_retry(self):
        for status, outcome in ((401, "authentication_required"), (403, "forbidden"), (429, "rate_limited")):
            self.session.reset_mock()
            self.session.get.return_value = response(status)
            result = self.client.fetch("https://stats.ncaa.org/test")
            self.assertEqual(result.report["outcome"], outcome)
            self.assertIsNone(result.html)
            self.session.get.assert_called_once()

    def test_bounded_retry_and_backoff(self):
        self.session.get.side_effect = [response(503), response()]
        self.assertEqual(self.client.fetch("https://stats.ncaa.org/test").report["outcome"], "html_available")
        self.assertEqual(self.session.get.call_count, 2)
        self.session.get.side_effect = None
        self.session.get.reset_mock()
        self.session.get.return_value = response(503, **{"Retry-After": "120"})
        self.assertEqual(self.client.fetch("https://stats.ncaa.org/test").report["outcome"], "server_requested_backoff")
        self.session.get.assert_called_once()

    def test_external_redirect_not_followed(self):
        self.session.get.return_value = response(302, Location="https://example.com/private?token=secret")
        result = self.client.fetch("https://stats.ncaa.org/test")
        self.assertEqual(result.report["outcome"], "redirect_outside_allowed_hosts")
        self.session.get.assert_called_once()
        self.assertNotIn("secret", self.log.read_text())

    def test_query_and_exception_redaction(self):
        self.session.get.side_effect = requests.Timeout("credential-secret https://private-proxy")
        result = self.client.fetch("https://stats.ncaa.org/test?token=credential-secret")
        self.assertEqual(result.report["outcome"], "network_error")
        log = self.log.read_text()
        self.assertNotIn("credential-secret", log)
        self.assertNotIn("private-proxy", log)
        self.assertEqual(json.loads(log)["attempts"][0]["error_type"], "Timeout")

    def test_soft_block_and_login(self):
        for html in ("<title>Access Denied</title>", '<input type="password">'):
            self.session.get.return_value = response(html=html)
            self.assertIsNone(self.client.fetch("https://stats.ncaa.org/test").html)

    def test_url_allowlist(self):
        for url in ("http://stats.ncaa.org/", "https://stats.ncaa.org.evil.com/",
                    "https://user:password@stats.ncaa.org/", "https://stats.ncaa.org:444/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.client.fetch(url)
        self.session.get.assert_not_called()

    def test_diagnostic_fields_and_safe_redirects(self):
        self.session.get.side_effect = [response(302, Location="/next?sport_code=MBB&token=secret"), response(html="<table></table>")]
        result = self.client.fetch("https://stats.ncaa.org/test?division=1")
        self.assertEqual(result.report["response_bytes"], len(b"<table></table>"))
        self.assertEqual(len(result.report["redirect_history"]), 1)
        self.assertIn("sport_code=MBB", result.report["final_url"])
        self.assertIn("environment", result.report)
        self.assertNotIn("secret", self.log.read_text())
        self.assertEqual(result.report["rendering_required"], "not_determined")

    def test_404_is_distinct_from_access_denial(self):
        self.session.get.return_value = response(404)
        self.assertEqual(self.client.fetch("https://stats.ncaa.org/test").report["outcome"], "not_found")
