"""Polite, robots-respecting access probe for comparing environments (local vs. cloud).

Rules enforced here (see docs/stage3_access_test_plan.md):
- only HTTPS URLs on an explicit host allowlist;
- robots.txt is read once per host first; unreadable policies fail closed (404 = no rules);
- one attempt per request (no retries); a host stops for the rest of the run after
  401/403/429, a Retry-After backoff, a challenge/CAPTCHA page or a login page;
- at least `min_delay_seconds` (>= 10) between requests to the same host and
  `round_interval_seconds` (>= 600) between rounds;
- dry run unless `execute=True`; no cookies, proxy values, egress IP or credential logging.
"""
import csv
import hashlib
import json
import os
import platform
import socket
import ssl
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
import urllib3

from .http_client import REQUEST_HEADERS, HttpClient, safe_url
from .models import utc_now

USER_AGENT = "BasketballDataResearch"
MIN_DELAY_SECONDS = 10
MIN_ROUND_INTERVAL_SECONDS = 600
MAX_ROUNDS = 3
MAX_REQUESTS = 60
# Hosts whose pages are never fetched automatically, whatever robots.txt says: only the
# policy (robots.txt) is read and the robots decision for each URL is recorded.
POLICY_ONLY_HOSTS = {"stats.ncaa.org"}
NCAA_ROBOTS_URL = "https://stats.ncaa.org/robots.txt"
PROBE_TIMEOUT_SECONDS = 20
REQUEST_WINDOW_SECONDS = 120
# Outcomes after which the host receives no further requests in this run.
STOP_OUTCOMES = {"authentication_required", "forbidden", "rate_limited", "server_requested_backoff",
                 "access_challenge", "login_required"}
# Runner facts that are safe to record. Tokens, secrets and IPs are never read.
METADATA_VARIABLES = ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "RUNNER_NAME", "ImageOS",
                      "ImageVersion", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW", "GITHUB_REPOSITORY",
                      "GITHUB_SHA")
SUMMARY_COLUMNS = ["environment", "region", "host", "url", "purpose", "attempts", "outcomes", "status_codes",
                   "median_seconds", "max_seconds", "network_errors", "robots_outcome", "robots_sha256"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_config(path):
    path = Path(path)
    content = path.read_bytes()
    config = json.loads(content.decode("utf-8-sig"))
    require(config.get("schema_version") == 1, "Probe config needs schema_version 1")
    hosts = config.get("hosts")
    require(isinstance(hosts, list) and hosts and all(isinstance(h, str) and h and "*" not in h for h in hosts),
            "hosts must be an explicit list of host names (no wildcards)")
    require(len(set(hosts)) == len(hosts), "hosts must not repeat")
    delay = config.get("min_delay_seconds", MIN_DELAY_SECONDS)
    interval = config.get("round_interval_seconds", MIN_ROUND_INTERVAL_SECONDS)
    rounds = config.get("rounds", MAX_ROUNDS)
    require(isinstance(delay, (int, float)) and delay >= MIN_DELAY_SECONDS,
            f"min_delay_seconds must be at least {MIN_DELAY_SECONDS}")
    require(isinstance(interval, (int, float)) and interval >= MIN_ROUND_INTERVAL_SECONDS,
            f"round_interval_seconds must be at least {MIN_ROUND_INTERVAL_SECONDS}")
    require(isinstance(rounds, int) and 1 <= rounds <= MAX_ROUNDS, f"rounds must be 1-{MAX_ROUNDS}")
    urls = config.get("urls")
    require(isinstance(urls, list), "urls must be a list (empty for robots-only probes)")
    seen = set()
    for item in urls:
        url = item.get("url", "")
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username
                and not parsed.password and parsed.port in (None, 443) and not parsed.fragment,
                f"URL must be HTTPS on an allowlisted host without credentials: {url}")
        require(safe_url(url) == url, f"URL contains unrecognized query values (would be redacted): {url}")
        require(url not in seen, f"Duplicate probe URL: {url}")
        require(bool(item.get("purpose")), f"Each URL needs a purpose: {url}")
        require(isinstance(item.get("policy_only", False), bool), f"policy_only must be true or false: {url}")
        require(parsed.hostname not in POLICY_ONLY_HOSTS or item.get("policy_only") is True,
                f"URLs on {parsed.hostname} must be marked policy_only (never fetched): {url}")
        seen.add(url)
    total = len(hosts) + sum(not item.get("policy_only") for item in urls) * rounds
    require(total <= MAX_REQUESTS, f"Plan allows at most {MAX_REQUESTS} requests; this config needs up to {total}")
    return {**config, "min_delay_seconds": delay, "round_interval_seconds": interval, "rounds": rounds,
            "config_sha256": hashlib.sha256(content).hexdigest(), "config_path": str(path)}


def check_output(output):
    """Probe logs never go into data/ (which holds the validated CSVs)."""
    resolved = Path(output).resolve()
    data_root = (Path.cwd() / "data").resolve()
    require(not resolved.is_relative_to(data_root), "Probe output must not be inside data/")
    return Path(output)


def runner_metadata(region, environ=None):
    environ = os.environ if environ is None else environ
    return {"region": region or "unknown", "python": platform.python_version(),
            "platform": platform.platform(terse=True), "requests": requests.__version__,
            "runner": {name: environ[name] for name in METADATA_VARIABLES if environ.get(name)}}


def diagnostic_metadata(environ=None) -> dict:
    environ = os.environ if environ is None else environ
    return {"urllib3": urllib3.__version__, "openssl": ssl.OPENSSL_VERSION, "ipv6_socket_support": socket.has_ipv6,
            "proxy_environment_configured": any(value and name.lower() in {"http_proxy", "https_proxy", "all_proxy"}
                                                for name, value in environ.items()),
            "no_proxy_environment_configured": any(value and name.lower() == "no_proxy"
                                                   for name, value in environ.items())}


def ncaa_robots_only(config: dict) -> bool:
    return config["hosts"] == ["stats.ncaa.org"] and not config["urls"] and config["rounds"] == 1


def request_window(config: dict, start: str | None) -> tuple[datetime, datetime] | None:
    if start is None:
        return None
    require(ncaa_robots_only(config), "An execution window is only supported for the one-request NCAA robots probe")
    try:
        parsed = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("Window start must be UTC in the form YYYY-MM-DDTHH:MM:SSZ") from None
    require(parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == start, "Window start must be UTC in the form YYYY-MM-DDTHH:MM:SSZ")
    return parsed, parsed + timedelta(seconds=REQUEST_WINDOW_SECONDS)


def diagnostic_plan(config: dict, window: tuple[datetime, datetime] | None = None,
                    timeout: int = PROBE_TIMEOUT_SECONDS) -> dict:
    plan = {"request_config": {"method": "GET", "configured_headers": REQUEST_HEADERS.copy(),
                              "timeout_seconds": timeout, "retries": 0,
                              "follow_redirects": False if ncaa_robots_only(config) else "policy_checked"}}
    if window:
        plan["request_window_utc"] = {"start": window[0].isoformat(), "end_exclusive": window[1].isoformat()}
    return plan


def interleave(urls):
    """Round-robin by host so same-host spacing rarely forces a wait."""
    queues = {}
    for item in urls:
        queues.setdefault(urlsplit(item["url"]).hostname, []).append(item)
    ordered = []
    while any(queues.values()):
        for host in list(queues):
            if queues[host]:
                ordered.append(queues[host].pop(0))
    return ordered


class Pacer:
    """Enforces per-host spacing using an injectable clock and sleep."""

    def __init__(self, delay, clock=time.monotonic, sleep=time.sleep):
        self.delay, self.clock, self.sleep, self.last = delay, clock, sleep, {}
        self.waits = []

    def wait(self, host):
        if host in self.last:
            remaining = self.last[host] + self.delay - self.clock()
            if remaining > 0:
                self.waits.append((host, round(remaining, 3)))
                self.sleep(remaining)
        self.last[host] = self.clock()


def plan_requests(config):
    robots = [f"https://{host}/robots.txt" for host in config["hosts"]]
    requestable = [item["url"] for item in interleave(config["urls"]) if not item.get("policy_only")]
    rounds = [list(requestable) for _ in range(config["rounds"])]
    return {"robots": robots, "rounds": rounds,
            "policy_only_never_requested": [item["url"] for item in config["urls"] if item.get("policy_only")],
            "max_requests": len(robots) + sum(map(len, rounds)),
            "min_delay_seconds": config["min_delay_seconds"],
            "round_interval_seconds": config["round_interval_seconds"]}


class Probe:
    def __init__(self, config, *, environment, region="unknown", output="logs", session=None,
                 clock=time.monotonic, sleep=time.sleep, environ=None, timeout=PROBE_TIMEOUT_SECONDS,
                 capture_diagnostics=False, network_provider=None, window: tuple[datetime, datetime] | None = None):
        require(bool(environment) and environment != "unspecified", "An environment label is required")
        self.config, self.environment = config, environment
        self.output = check_output(output)
        self.window = window
        self.metadata = runner_metadata(region, environ)
        if capture_diagnostics:
            self.metadata.update(diagnostic_metadata(environ))
            self.metadata["network_provider"] = network_provider
            self.metadata.update(diagnostic_plan(config, window, timeout))
        self.run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        stem = f"access-probe-{environment}-{self.run_id}"
        self.log_path = self.output / f"{stem}.jsonl"
        self.summary_path = self.output / f"{stem}-summary.csv"
        self.pacer = Pacer(config["min_delay_seconds"], clock, sleep)
        self.sleep = sleep
        self.client = HttpClient("probe", log_path=self.log_path, session=session, timeout=timeout,
                                 retries=0, sleep=sleep, environment=environment,
                                 allowed_hosts=config["hosts"], capture_diagnostics=capture_diagnostics)
        self.policies, self.robots, self.stopped, self.results = {}, {}, {}, []

    def fetch(self, url, *, follow_redirects=True):
        result = self.client.fetch(url, follow_redirects=follow_redirects, request_window=self.window)
        # No cookies are kept between requests.
        cookies = getattr(self.client.session, "cookies", None)
        if hasattr(cookies, "clear"):
            cookies.clear()
        return result

    def log(self, record):
        self.output.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_robots(self, host):
        self.pacer.wait(host)
        self.client.context = {"run_id": self.run_id, "kind": "robots", "host": host, "region": self.metadata["region"]}
        self.client.url_policy = lambda url: urlsplit(url).hostname == host
        result = self.fetch(f"https://{host}/robots.txt", follow_redirects=host not in POLICY_ONLY_HOSTS)
        report = result.report
        status, kind = report.get("status_code"), (report.get("content_type") or "").lower()
        rules = None
        if status == 404 and report.get("outcome") == "not_found":
            decision = "no_robots_file_allowed"
        elif status == 200 and result.text is not None and kind == "text/plain" and report.get("outcome") == "non_html_response":
            rules = RobotFileParser()
            rules.parse(result.text.splitlines())
            decision = "parsed"
        else:
            # 401/403/5xx, HTML instead of text, or a network error: do not crawl the host.
            decision = "unavailable_fail_closed"
        self.robots[host] = {"decision": decision, "status_code": status, "outcome": report.get("outcome"),
                             "attempted": bool(report["attempts"]),
                             "sha256": (report.get("attempts") or [{}])[-1].get("body_sha256"),
                             "elapsed_seconds": report.get("elapsed_seconds")}
        self.policies[host] = (lambda url: True) if decision == "no_robots_file_allowed" else \
            (lambda url, rules=rules: rules.can_fetch(USER_AGENT, url)) if rules else (lambda url: False)
        if report.get("outcome") in STOP_OUTCOMES:
            self.stopped[host] = report["outcome"]

    def robots_allows(self, url):
        host = urlsplit(url).hostname
        return host in self.policies and self.policies[host](url)

    def allowed(self, url):
        # Also used for redirects: nothing on a policy-only host is ever requested.
        host = urlsplit(url).hostname
        return host not in POLICY_ONLY_HOSTS and host not in self.stopped and self.robots_allows(url)

    def probe_url(self, item, round_number):
        url, host = item["url"], urlsplit(item["url"]).hostname
        base = {"run_id": self.run_id, "kind": "target", "round": round_number, "host": host,
                "purpose": item["purpose"], "region": self.metadata["region"]}
        if host in self.stopped:
            record = {**base, "requested_url": safe_url(url), "environment": self.environment, "checked_at": utc_now(),
                      "outcome": "skipped_host_stopped", "classification": "not_requested",
                      "stopped_because": self.stopped[host], "target_requested": False}
            self.log(record)
            return record
        if item.get("policy_only") or not self.allowed(url):
            robots_allows = self.robots_allows(url)
            record = {**base, "requested_url": safe_url(url), "environment": self.environment, "checked_at": utc_now(),
                      "outcome": "policy_only_not_requested" if robots_allows else "robots_disallowed",
                      "classification": "not_requested" if robots_allows else "robots_policy_restriction",
                      "robots_decision": self.robots[host]["decision"], "robots_allows": robots_allows,
                      "policy_only": bool(item.get("policy_only")), "target_requested": False}
            self.log(record)
            return record
        self.pacer.wait(host)
        self.client.context = base
        # Redirects are followed only within the allowlist and only where robots allows.
        self.client.url_policy = self.allowed
        report = self.fetch(url).report
        report["target_requested"] = True
        if report["outcome"] in STOP_OUTCOMES or report.get("challenge_detected"):
            self.stopped[host] = report["outcome"]
            self.stopped[urlsplit(report["final_url"]).hostname] = report["outcome"]
        return report

    def run(self):
        self.log({"run_id": self.run_id, "kind": "run_start", "environment": self.environment,
                  "started_at": utc_now(), "config_sha256": self.config["config_sha256"],
                  "plan": plan_requests(self.config), **self.metadata})
        if self.window:
            remaining = (self.window[0] - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                self.sleep(remaining)
        for host in self.config["hosts"]:
            self.load_robots(host)
        for round_number in range(1, self.config["rounds"] + 1):
            if round_number > 1:
                self.sleep(self.config["round_interval_seconds"])
            for item in interleave(self.config["urls"]):
                self.results.append(self.probe_url(item, round_number))
        summary = self.write_summary()
        self.log({"run_id": self.run_id, "kind": "run_end", "environment": self.environment,
                  "finished_at": utc_now(), "requests_sent": self.requests_sent(),
                  "stopped_hosts": self.stopped, "robots": self.robots, "pacing_waits": len(self.pacer.waits)})
        return summary

    def requests_sent(self):
        return sum(policy["attempted"] for policy in self.robots.values()) + sum(bool(r.get("target_requested")) for r in self.results)

    def write_summary(self):
        rows = []
        for item in self.config["urls"]:
            mine = [r for r in self.results if r.get("requested_url") == safe_url(item["url"])]
            times = [r["elapsed_seconds"] for r in mine if r.get("target_requested") and r.get("elapsed_seconds") is not None]
            host = urlsplit(item["url"]).hostname
            rows.append({"environment": self.environment, "region": self.metadata["region"], "host": host,
                "url": safe_url(item["url"]), "purpose": item["purpose"],
                "attempts": sum(bool(r.get("target_requested")) for r in mine),
                "outcomes": "|".join(r["outcome"] for r in mine),
                "status_codes": "|".join(str(r.get("status_code") or "") for r in mine),
                "median_seconds": round(statistics.median(times), 3) if times else None,
                "max_seconds": max(times) if times else None,
                "network_errors": sum(r["outcome"] == "network_error" for r in mine),
                "robots_outcome": self.robots[host]["decision"], "robots_sha256": self.robots[host]["sha256"]})
        self.output.mkdir(parents=True, exist_ok=True)
        with self.summary_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=SUMMARY_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return {"run_id": self.run_id, "log": str(self.log_path), "summary": str(self.summary_path),
                "requests_sent": self.requests_sent(), "stopped_hosts": self.stopped, "robots": self.robots, "rows": rows}
