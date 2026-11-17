"""Bounded, transparent HTTP diagnostics; no authentication or challenge bypass."""
import json
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .models import utc_now

HOSTS = {
    "ncaa": {"stats.ncaa.org"},
}


def validate_url(url, source):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS[source]
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError(f"Use an HTTPS URL on an approved {source} host without credentials")
    return url


def safe_url(url):
    """Keep only recognized public routing parameters; remove credentials."""
    parsed = urlsplit(url)
    public = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        allowed = ((key in {"division", "division_filter", "season_division_id", "academic_year", "id"}
                    and value.isascii() and value.isdigit())
                   or (key == "sport_code" and value in {"MBB", "WBB"})
                   or (key in {"conference_id", "tournament_id"} and value.isascii() and value.isdigit())
                   or (key == "game_date" and re.fullmatch(r"\d{2}/\d{2}/\d{4}", value)))
        public.append((key if allowed else "REDACTED", value if allowed else "REDACTED"))
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path,
                       urlencode(public), ""))


def page_blocker(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    body = soup.get_text(" ", strip=True).lower()
    for marker in ("access denied", "just a moment", "verify you are human",
                   "checking your browser", "attention required", "captcha"):
        if marker in title or marker in body[:1500]:
            return "access_challenge"
    if soup.select_one('input[type="password"]'):
        return "login_required"
    return None


@dataclass
class FetchResult:
    report: dict
    html: str | None
    text: str | None = None


class HttpClient:
    def __init__(self, source, log_path="logs/requests.jsonl", session=None,
                 timeout=20, retries=1, sleep=time.sleep, environment="unspecified",
                 allowed_hosts=None):
        # An explicit allowlist (used by the access probe) replaces the per-source hosts.
        hosts = frozenset(allowed_hosts) if allowed_hosts else HOSTS.get(source)
        if not hosts or retries not in (0, 1):
            raise ValueError("Unknown source or retry limit (must be 0 or 1)")
        self.source = source
        self.hosts = hosts
        self.context = {}
        self.log_path = Path(log_path)
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.environment = environment
        self.url_policy = None

    def check_url(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.hosts
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError(f"Use an HTTPS URL on an approved {self.source} host without credentials")
        return url

    def fetch(self, url):
        self.check_url(url)
        report = {"source": self.source, "requested_url": safe_url(url),
                  "checked_at": utc_now(), "environment": self.environment, **self.context,
                  "attempts": [], "redirect_history": [], "outcome": "unknown"}
        overall_started = time.monotonic()
        current, html, document, redirects, transient_retries = url, None, None, 0, 0
        while True:
            if self.url_policy is not None and not self.url_policy(current):
                report["outcome"] = "robots_disallowed_redirect"
                report["blocked_url"] = safe_url(current)
                # The last response came from the previous URL, not the blocked target.
                current = report["attempts"][-1]["url"] if report["attempts"] else current
                break
            started = time.monotonic()
            try:
                response = self.session.get(current, timeout=self.timeout,
                    headers={"User-Agent": "BasketballDataResearch/0.1",
                             "Accept": "text/html"}, allow_redirects=False)
            except requests.RequestException as exc:
                # Exception strings can contain credentials, query values or proxy URLs.
                category = ("tls_error" if isinstance(exc, requests.exceptions.SSLError) else
                            "proxy_error" if isinstance(exc, requests.exceptions.ProxyError) else
                            "timeout" if isinstance(exc, requests.Timeout) else
                            "connection_error" if isinstance(exc, requests.ConnectionError) else
                            "request_error")
                report["attempts"].append({"url": safe_url(current), "error_category": category,
                    "error_type": type(exc).__name__,
                    "elapsed_seconds": round(time.monotonic() - started, 3)})
                report["outcome"] = "network_error"
                report["error_category"] = category
                break
            status = response.status_code
            content_type = response.headers.get("Content-Type", "")
            body = response.content
            if not isinstance(body, bytes):  # injected test transports may expose text only
                body = response.text.encode("utf-8")
            report["attempts"].append({"url": safe_url(current), "status_code": status,
                "content_type": content_type.split(";")[0],
                "response_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "retry_after_present": "Retry-After" in response.headers})
            report["status_code"] = status
            report["content_type"] = content_type.split(";")[0]
            report["response_bytes"] = len(body)
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or redirects >= 3:
                    report["outcome"] = "redirect_limit_or_missing_location"
                    break
                target = urljoin(current, location)
                report["redirect_history"].append({"from": safe_url(current),
                    "to": safe_url(target), "status_code": status})
                try:
                    self.check_url(target)
                except ValueError:
                    report["outcome"] = "redirect_outside_allowed_hosts"
                    break
                current = target
                redirects += 1
                continue
            if status in (401, 403, 429):
                report["outcome"] = {401: "authentication_required", 403: "forbidden",
                                     429: "rate_limited"}[status]
                # Record (never act on) a challenge page served with the denial.
                if "html" in content_type.lower() and isinstance(response.text, str):
                    report["challenge_detected"] = page_blocker(response.text) is not None
                break
            # Honor a server backoff by stopping, with or without retries; never shorten Retry-After.
            if status in (502, 503, 504) and "Retry-After" in response.headers:
                report["outcome"] = "server_requested_backoff"
                break
            if status in (502, 503, 504) and transient_retries < self.retries:
                transient_retries += 1
                self.sleep(1)
                continue
            if status != 200:
                report["outcome"] = "not_found" if status == 404 else "http_error"
                break
            document = response.text
            if "text/html" not in content_type.lower() and "application/xhtml+xml" not in content_type.lower():
                report["outcome"] = "non_html_response"
                break
            blocker = page_blocker(response.text)
            report["outcome"] = blocker or "html_available"
            if not blocker:
                html = response.text
                soup = BeautifulSoup(html, "html.parser")
                report["table_count"] = len(soup.find_all("table"))
                report["script_count"] = len(soup.find_all("script"))
                report["embedded_json_count"] = len(soup.select('script[type="application/json"], script[type="application/ld+json"]'))
                report["rendering_required"] = "not_determined"
            break
        report["final_url"] = safe_url(current)
        report["elapsed_seconds"] = round(time.monotonic() - overall_started, 3)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False) + "\n")
        return FetchResult(report, html, document)
