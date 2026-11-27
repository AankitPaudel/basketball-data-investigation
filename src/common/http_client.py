"""Bounded, transparent HTTP diagnostics; no authentication or challenge bypass."""
import json
import hashlib
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.structures import CaseInsensitiveDict

from .models import utc_now

HOSTS = {
    "ncaa": {"stats.ncaa.org"},
}
REQUEST_HEADERS = {"User-Agent": "BasketballDataResearch/0.1", "Accept": "text/html"}
PAGE_MARKERS = {"access denied": "access_denied_text", "just a moment": "interstitial_text",
                "verify you are human": "human_verification_text", "checking your browser": "browser_check_text",
                "attention required": "interstitial_text", "captcha": "captcha_text"}
VERIFICATION_SIGNALS = {"human_verification_text", "browser_check_text", "captcha_text", "cf_mitigated_challenge"}
DENIAL_OUTCOMES = {401: "authentication_required", 403: "forbidden", 429: "rate_limited"}
REFERENCE_PATTERN = re.compile(r"\breference\s*#([0-9a-f]{1,4}\.[0-9a-f]{1,12}\.[0-9a-f]{1,12}\.[0-9a-f]{1,12})\b", re.I)


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


def safe_redirect_url(url: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else ""
    host = parsed.hostname if parsed.hostname in allowed_hosts else "redacted-host"
    return urlunsplit((scheme, host, "/REDACTED" if parsed.path else "", "", ""))


def page_evidence(html: str) -> tuple[list[str], dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    body = soup.get_text(" ", strip=True)[:1500].lower()
    signals = {signal for marker, signal in PAGE_MARKERS.items() if marker in title or marker in body}
    if soup.select_one('input[type="password"]'):
        signals.add("password_input")
    match = REFERENCE_PATTERN.search(body)
    return sorted(signals), {"edge_error_reference": match.group(1)} if match else {}


def blocker_from_signals(signals: list[str]) -> str | None:
    if any(signal != "password_input" for signal in signals):
        return "access_challenge"
    if "password_input" in signals:
        return "login_required"
    return None


def page_blocker(html):
    return blocker_from_signals(page_evidence(html)[0])


def classify_response(status: int | None, signals: list[str]) -> str:
    if status == 429:
        return "rate_limited"
    if VERIFICATION_SIGNALS.intersection(signals):
        return "verification_challenge"
    if status == 403 or "access_denied_text" in signals:
        return "access_denial"
    if status == 401 or "password_input" in signals:
        return "authentication_required"
    if "interstitial_text" in signals:
        return "unknown_interstitial"
    if status is not None and 500 <= status < 600:
        return "temporary_server_error"
    if status is not None and 200 <= status < 300:
        return "success"
    return "unknown_response"


def network_error_category(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy_error"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    # Requests wraps urllib3 exceptions in a MaxRetryError, whose reason holds DNS failures.
    pending, seen = [exc], set()
    while pending:
        error = pending.pop()
        if not isinstance(error, BaseException) or id(error) in seen:
            continue
        if isinstance(error, socket.gaierror) or type(error).__name__ == "NameResolutionError":
            return "dns_error"
        seen.add(id(error))
        pending.extend((*error.args, error.__cause__, error.__context__, getattr(error, "reason", None)))
    return "connection_error" if isinstance(exc, requests.ConnectionError) else "request_error"


def resolve_destination(host: str) -> dict:
    addresses = {socket.AF_INET: set(), socket.AF_INET6: set()}
    try:
        results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return {"result": "dns_error", "error_code": exc.errno}
    except OSError:
        return {"result": "resolver_error"}
    for family, _, _, _, address in results:
        if family in addresses:
            ip = ipaddress.ip_address(address[0].split("%", 1)[0])
            # Record public destinations only. A private answer is still evidence of resolution.
            addresses[family].add(str(ip) if ip.is_global else "REDACTED_NON_PUBLIC")
    return {"result": "resolved" if any(addresses.values()) else "no_addresses",
            "ipv4": sorted(addresses[socket.AF_INET]), "ipv6": sorted(addresses[socket.AF_INET6])}


def sanitized_request_headers(headers) -> dict | None:
    if headers is None:
        return None
    headers = CaseInsensitiveDict(headers)
    safe = {}
    for name, pattern in {"user-agent": r"BasketballDataResearch/\d+\.\d+", "accept": r"text/html",
                          "accept-encoding": r"(?:gzip|deflate|br|zstd|identity)(?:,\s*(?:gzip|deflate|br|zstd|identity))*",
                          "connection": r"keep-alive|close"}.items():
        value = headers.get(name)
        if value is not None:
            safe[name] = value if re.fullmatch(pattern, value, re.I) else "REDACTED"
    return safe


def sanitized_response_headers(headers) -> dict:
    headers = CaseInsensitiveDict(headers)
    safe = {}
    for name, pattern in {"content-type": r"[a-z]+/[a-z0-9.+-]+", "content-length": r"\d{1,20}",
                          "retry-after": r"\d{1,12}|[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT",
                          "cf-ray": r"[0-9a-fA-F]{16}-[A-Z]{3}", "cf-mitigated": r"challenge"}.items():
        value = headers.get(name)
        if value is not None:
            value = value.partition(";")[0].strip() if name == "content-type" else value.strip()
            safe[name] = value if re.fullmatch(pattern, value) else "REDACTED"
    for name in ("server", "via", "x-cache", "x-akamai-request-id", "x-amz-cf-id", "location"):
        if name in headers:
            products = re.findall(r"\b(?:AkamaiGHost|Akamai|Cloudflare|CloudFront|Varnish|Fastly|nginx|Apache|Envoy)\b",
                                  headers[name], re.I) if name in {"server", "via"} else []
            safe[name] = sorted({product.lower() for product in products}) if products else "present"
    return safe


def connection_details(response: requests.Response) -> dict:
    raw = getattr(response, "raw", None)
    fields = {"http_version": {10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2", 30: "HTTP/3"}.get(
        getattr(raw, "version", None))}
    sock = getattr(getattr(raw, "connection", None), "sock", None)
    if sock is not None:
        try:
            peer = ipaddress.ip_address(sock.getpeername()[0].split("%", 1)[0])
            fields["peer_address"] = str(peer) if peer.is_global else "REDACTED_NON_PUBLIC"
        except (OSError, ValueError):
            pass  # Connection information is best-effort and may already be unavailable.
    return fields


@dataclass
class FetchResult:
    report: dict
    html: str | None
    text: str | None = None


class HttpClient:
    def __init__(self, source, log_path="logs/requests.jsonl", session=None,
                 timeout=20, retries=1, sleep=time.sleep, environment="unspecified",
                 allowed_hosts=None, capture_diagnostics=False):
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
        self.capture_diagnostics = capture_diagnostics

    def check_url(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.hosts
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError(f"Use an HTTPS URL on an approved {self.source} host without credentials")
        return url

    def fetch(self, url, *, follow_redirects=True, request_window: tuple[datetime, datetime] | None = None):
        self.check_url(url)
        report = {"source": self.source, "requested_url": safe_url(url),
                  "checked_at": utc_now(), "environment": self.environment, **self.context,
                  "attempts": [], "redirect_history": [], "outcome": "unknown", "method": "GET"}
        overall_started = time.monotonic()
        current, current_logged, html, document, redirects, transient_retries = url, safe_url(url), None, None, 0, 0
        while True:
            if self.url_policy is not None and not self.url_policy(current):
                report["outcome"] = "robots_disallowed_redirect"
                report["classification"] = "robots_policy_restriction"
                report["blocked_url"] = current_logged
                # The last response came from the previous URL, not the blocked target.
                current_logged = report["attempts"][-1]["url"] if report["attempts"] else current_logged
                break
            started = time.monotonic()
            attempt = {"url": current_logged, "method": "GET"}
            options = {}
            response = None
            if request_window and not request_window[0] <= datetime.fromisoformat(utc_now()) < request_window[1]:
                report.update(outcome="window_closed", classification="not_requested", window_closed_stage="before_dns")
                break
            if self.capture_diagnostics:
                host = urlsplit(current).hostname
                attempt.update(destination_hostname=host, dns=resolve_destination(host))
                started = time.monotonic()  # Resolver snapshot is separate from request duration.
                options["stream"] = True
            if self.capture_diagnostics or request_window:
                attempt["request_started_at"] = utc_now()
                if request_window and not request_window[0] <= datetime.fromisoformat(attempt["request_started_at"]) < request_window[1]:
                    report.update(outcome="window_closed", classification="not_requested", window_closed_stage="before_http")
                    break
            try:
                response = self.session.get(current, timeout=self.timeout,
                    headers=REQUEST_HEADERS.copy(), allow_redirects=False, **options)
                try:
                    if self.capture_diagnostics:
                        prepared = getattr(response, "request", None)
                        attempt.update(connection_details(response),
                                       request_headers=sanitized_request_headers(getattr(prepared, "headers", None)),
                                       response_headers=sanitized_response_headers(response.headers))
                    body = response.content
                finally:
                    if self.capture_diagnostics:
                        response.close()
            except requests.RequestException as exc:
                # Exception strings can contain credentials, query values or proxy URLs.
                category = network_error_category(exc)
                report["attempts"].append({**attempt, "error_category": category,
                    "error_type": type(exc).__name__,
                    "elapsed_seconds": round(time.monotonic() - started, 3)})
                report["outcome"] = "network_error"
                report["error_category"] = category
                report["classification"] = category
                if response is not None:
                    status = response.status_code
                    observed = report["attempts"][-1]
                    report["status_code"] = observed["status_code"] = status
                    signals = ["cf_mitigated_challenge"] if response.headers.get("CF-Mitigated", "").lower() == "challenge" else []
                    observed["response_signals"] = signals
                    ray = observed.get("response_headers", {}).get("cf-ray")
                    if ray and ray != "REDACTED":
                        observed["reference_ids"] = {"cf_ray": ray}
                    if status in DENIAL_OUTCOMES:
                        report["outcome"] = DENIAL_OUTCOMES[status]
                        report["classification"] = classify_response(status, signals)
                    elif signals:
                        report["outcome"] = "access_challenge"
                        report["classification"] = "verification_challenge"
                    elif status >= 500 and "Retry-After" in response.headers:
                        report["outcome"] = "server_requested_backoff"
                    observed["classification"] = report["classification"]
                break
            status = response.status_code
            content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
            if not re.fullmatch(r"[a-z]+/[a-z0-9.+-]+", content_type):
                content_type = ""
            if not isinstance(body, bytes):  # injected test transports may expose text only
                body = response.text.encode("utf-8")
            elapsed = round(time.monotonic() - started, 3)
            inspect_body = "html" in content_type or (status in DENIAL_OUTCOMES and content_type == "text/plain")
            signals, references = page_evidence(response.text) if inspect_body else ([], {})
            if response.headers.get("CF-Mitigated", "").lower() == "challenge":
                signals.append("cf_mitigated_challenge")
            classification = classify_response(status, signals)
            ray = attempt.get("response_headers", {}).get("cf-ray")
            if ray and ray != "REDACTED":
                references["cf_ray"] = ray
            report["attempts"].append({**attempt, "status_code": status,
                "content_type": content_type, "classification": classification,
                "response_signals": signals, "reference_ids": references,
                "response_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                "elapsed_seconds": elapsed,
                "retry_after_present": "Retry-After" in response.headers})
            report["status_code"] = status
            report["classification"] = classification
            report["content_type"] = content_type
            report["response_bytes"] = len(body)
            blocker = blocker_from_signals(signals)
            if blocker and status not in (200, 401, 403, 429):
                report["outcome"] = blocker
                break
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not follow_redirects:
                    if self.capture_diagnostics and location:
                        try:
                            target = safe_redirect_url(urljoin(current, location), self.hosts)
                        except ValueError:
                            target = None
                        report["redirect_history"].append({"from": current_logged, "to": target, "status_code": status,
                                                          "followed": False})
                    report["outcome"] = "redirect_not_followed"
                    break
                if not location or redirects >= 3:
                    report["outcome"] = "redirect_limit_or_missing_location"
                    break
                target = urljoin(current, location)
                target_logged = safe_redirect_url(target, self.hosts) if self.capture_diagnostics else safe_url(target)
                report["redirect_history"].append({"from": current_logged, "to": target_logged, "status_code": status})
                try:
                    self.check_url(target)
                except ValueError:
                    report["outcome"] = "redirect_outside_allowed_hosts"
                    break
                current, current_logged = target, target_logged
                redirects += 1
                continue
            if status in DENIAL_OUTCOMES:
                report["outcome"] = DENIAL_OUTCOMES[status]
                # Record (never act on) a challenge page served with the denial.
                if inspect_body or signals:
                    report["challenge_detected"] = blocker is not None
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
            if blocker:
                report["outcome"] = blocker
                break
            if "text/html" not in content_type.lower() and "application/xhtml+xml" not in content_type.lower():
                report["outcome"] = "non_html_response"
                break
            report["outcome"] = "html_available"
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            report["table_count"] = len(soup.find_all("table"))
            report["script_count"] = len(soup.find_all("script"))
            report["embedded_json_count"] = len(soup.select('script[type="application/json"], script[type="application/ld+json"]'))
            report["rendering_required"] = "not_determined"
            break
        report["final_url"] = current_logged
        report["elapsed_seconds"] = round(time.monotonic() - overall_started, 3)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False) + "\n")
        return FetchResult(report, html, document)
