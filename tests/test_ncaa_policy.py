import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.ncaa.client import NCAAClient
from tests.test_http_client import response


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Mock()
        self.client = NCAAClient(session=self.session, log_path=Path(self.directory.name) / "requests.jsonl", environment="unit-test")

    def test_disallow_prevents_any_target_request(self):
        self.session.get.return_value = response(html="User-agent: *\nDisallow: /\n", **{"Content-Type": "text/plain"})
        result = self.client.fetch("https://stats.ncaa.org/contests/123/box_score")
        self.assertEqual(result.report["outcome"], "robots_disallowed")
        self.assertFalse(result.report["target_requested"])
        self.assertIsNone(result.report["status_code"])
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], "https://stats.ncaa.org/robots.txt")

    def test_unavailable_or_html_policy_fails_closed(self):
        for reply in (response(403), response(200, "<p>Unrecognized policy page</p>")):
            self.session.get.reset_mock()
            self.session.get.return_value = reply
            self.assertEqual(self.client.fetch("https://stats.ncaa.org/test").report["outcome"], "robots_unavailable")
            self.session.get.assert_called_once()

    def test_allowed_policy_then_one_target(self):
        self.session.get.side_effect = [response(html="User-agent: *\nDisallow:\n", **{"Content-Type": "text/plain"}), response()]
        result = self.client.fetch("https://stats.ncaa.org/test")
        self.assertEqual(result.report["outcome"], "html_available")
        self.assertEqual(self.session.get.call_count, 2)

    def test_redirect_cannot_enter_disallowed_path(self):
        self.session.get.side_effect = [
            response(html="User-agent: *\nDisallow: /private\n", **{"Content-Type": "text/plain"}),
            response(302, Location="/private")]
        result = self.client.fetch("https://stats.ncaa.org/public")
        self.assertEqual(result.report["outcome"], "robots_disallowed_redirect")
        self.assertEqual(result.report["blocked_url"], "https://stats.ncaa.org/private")
        self.assertEqual(result.report["final_url"], "https://stats.ncaa.org/public")
        self.assertEqual(self.session.get.call_count, 2)
