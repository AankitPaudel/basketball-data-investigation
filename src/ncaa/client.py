import json
from urllib.robotparser import RobotFileParser

from ..common.http_client import FetchResult, HttpClient, safe_url, validate_url
from ..common.models import utc_now


class NCAAClient(HttpClient):
    def __init__(self, **kwargs):
        super().__init__("ncaa", **kwargs)

    def fetch(self, url):
        """Check robots before a target request; no override/bypass flag."""
        validate_url(url, "ncaa")
        self.url_policy = None
        policy_url = "https://stats.ncaa.org/robots.txt"
        if url == policy_url:
            return super().fetch(url)
        policy = super().fetch(policy_url)
        permitted = False
        outcome = "robots_unavailable"
        if policy.report.get("status_code") == 404:
            permitted = True
        elif (policy.report.get("status_code") == 200 and policy.text is not None
              and policy.report.get("content_type", "").lower() == "text/plain"):
            rules = RobotFileParser()
            rules.parse(policy.text.splitlines())
            permitted = rules.can_fetch("BasketballDataResearch", url)
            self.url_policy = lambda candidate: rules.can_fetch("BasketballDataResearch", candidate)
            outcome = "robots_disallowed"
        if permitted:
            return super().fetch(url)
        report = {"source": "ncaa", "requested_url": safe_url(url), "final_url": None,
                  "environment": self.environment, "checked_at": utc_now(),
                  "outcome": outcome, "status_code": None, "attempts": [],
                  "redirect_history": [], "policy_url": policy_url,
                  "policy_status_code": policy.report.get("status_code"),
                  "target_requested": False}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(report) + "\n")
        return FetchResult(report, None)
