"""Robots-first, paced fetch of one game's box score from a Sidearm school athletics site.

Order per run: robots.txt -> schedule page -> the one box score that matches opponent
and date. Rules (as in src/common/probe.py and docs/stage3_access_test_plan.md):
- robots.txt is read first on every run; an unreadable file (other than 404) means stop.
- Each URL is checked against the ``User-agent: *`` rules, including ``*``/``$``
  wildcards, which urllib's parser ignores. A disallowed URL is never requested.
- A ``Visit-time`` window (e.g. atlantic10.com's 0100-0645) is honored as UTC.
- Delay between requests to the host is max(10 s, Crawl-delay), persisted across runs
  by src/school/pacing.py.
- One attempt per request (HttpClient retries=0: its 502/503/504 retry waits only 1 s,
  shorter than any allowed delay, so it is not used); stop on 401/403/429, Retry-After,
  challenge/login pages, network errors and off-host redirects.
- Honest User-Agent (BasketballDataResearch/0.1), cookies cleared after each request,
  no proxies.
"""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from ..common.http_client import HttpClient, safe_url
from ..common.probe import STOP_OUTCOMES
from .pacing import HostPacer

USER_AGENT = "BasketballDataResearch"
BLOCKING = STOP_OUTCOMES | {"access_challenge", "login_required", "network_error", "robots_disallowed_redirect",
                            "redirect_outside_allowed_hosts", "redirect_limit_or_missing_location"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class FetchRefused(Exception):
    """Policy or access stop: nothing further is requested from the host."""


def star_group(text):
    """Allow/Disallow rules and Visit-time of the 'User-agent: *' group."""
    rules, visit, active, in_agents = [], None, False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            active = (active and in_agents) or value == "*"
            in_agents = True
            continue
        in_agents = False
        if active and key in ("allow", "disallow") and value:
            rules.append((key, value))
        elif active and key == "visit-time":
            visit = value
    return rules, visit


def wildcard_rule(rules, url):
    """Longest matching rule with * and $ (RFC 9309); ties go to Allow. Returns (allowed, rule)."""
    parts = urlsplit(url)
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    best = None
    for kind, pattern in rules:
        anchored = pattern.endswith("$")
        body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
        if re.match("^" + body + ("$" if anchored else ""), path):
            if best is None or len(pattern) > len(best[1]) or (len(pattern) == len(best[1]) and kind == "allow"):
                best = (kind, pattern)
    return (best is None or best[0] == "allow"), best


def in_visit_window(window, now):
    found = re.fullmatch(r"(\d{2})(\d{2})-(\d{2})(\d{2})", window.replace(" ", ""))
    if not found:
        return False  # unreadable window: do not visit
    start, end = int(found[1]) * 60 + int(found[2]), int(found[3]) * 60 + int(found[4])
    minute = now.hour * 60 + now.minute
    return start <= minute <= end if start <= end else minute >= start or minute <= end


def find_boxscore(html, schedule_url, host, opponent, game_date):
    """The single box score link of the completed game with this opponent on this date."""
    label = f"{MONTHS[game_date.month - 1]} {game_date.day}"
    links = set()
    for game in BeautifulSoup(html, "html.parser").select("li.sidearm-schedule-game"):
        when = game.select_one(".sidearm-schedule-game-opponent-date")
        name = game.select_one(".sidearm-schedule-game-opponent-name")
        if not when or not name:
            continue
        if not re.match(rf"{label}\b", when.get_text(" ", strip=True)):
            continue
        if opponent.casefold() not in name.get_text(" ", strip=True).casefold():
            continue
        for anchor in game.find_all("a", href=True):
            url = urljoin(schedule_url, anchor["href"])
            if urlsplit(url).hostname == host and "/boxscore/" in urlsplit(url).path:
                links.add(url)
    if len(links) != 1:
        raise FetchRefused(f"Expected one box score link for {opponent!r} on {label}, found {len(links)}")
    return links.pop()


class SchoolFetcher:
    def __init__(self, host, log_path, environment, region="unknown", pacer=None, session=None, clock=None):
        self.host = host
        self.client = HttpClient(f"school-{host}", log_path=log_path, retries=0, environment=environment,
                                 allowed_hosts={host}, session=session)
        self.pacer = pacer or HostPacer()
        self.region = region
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.run_id = f"{self.clock():%Y%m%dT%H%M%SZ}"
        self.crawl_delay = None
        self.rules = self.robots = self.robots_sha256 = self.visit_time = None
        self.requests = []

    def _fetch(self, url, kind, policy):
        saved = self.pacer.saved_crawl_delay(self.host)
        waited = self.pacer.wait(self.host, self.crawl_delay)
        self.client.context = {"run_id": self.run_id, "kind": kind, "host": self.host, "region": self.region,
                               "min_delay_seconds": max(self.pacer.delay_for(self.crawl_delay),
                                                        self.pacer.delay_for(saved)),
                               "saved_crawl_delay_seconds": saved,
                               "waited_seconds": round(waited, 3), **policy}
        try:
            result = self.client.fetch(url)
        finally:
            self.pacer.mark(self.host, self.crawl_delay)  # recorded even if the request failed
            cookies = getattr(self.client.session, "cookies", None)
            if hasattr(cookies, "clear"):
                cookies.clear()
        report = result.report
        self.requests.append({key: report.get(key) for key in ("kind", "requested_url", "status_code", "outcome",
                                                               "elapsed_seconds", "waited_seconds")})
        if report.get("outcome") in BLOCKING or report.get("challenge_detected"):
            raise FetchRefused(f"Stopped on {report.get('outcome')} for {safe_url(url)}")
        return result

    def load_robots(self):
        result = self._fetch(f"https://{self.host}/robots.txt", "robots", {})
        status = result.report.get("status_code")
        if status == 404:
            self.robots, self.rules = None, []
        elif status == 200 and result.text is not None:
            self.robots = RobotFileParser()
            self.robots.parse(result.text.splitlines())
            self.rules, self.visit_time = star_group(result.text)
            self.crawl_delay = self.robots.crawl_delay(USER_AGENT)
        else:
            raise FetchRefused(f"robots.txt not readable (status {status}); host treated as not permitted")
        self.robots_sha256 = (result.report.get("attempts") or [{}])[-1].get("body_sha256")

    def check(self, url):
        if urlsplit(url).hostname != self.host:
            raise FetchRefused(f"{safe_url(url)} is not on {self.host}")
        allowed, rule = wildcard_rule(self.rules, url)
        allowed = allowed and (self.robots is None or self.robots.can_fetch(USER_AGENT, url))
        policy = {"robots_allows": allowed, "robots_matched_rule": f"{rule[0]}: {rule[1]}" if rule else None,
                  "robots_sha256": self.robots_sha256, "crawl_delay_seconds": self.crawl_delay,
                  "visit_time": self.visit_time}
        if not allowed:
            record = {"source": self.client.source, "requested_url": safe_url(url),
                      "checked_at": self.clock().isoformat(), "environment": self.client.environment,
                      "run_id": self.run_id, "host": self.host, "region": self.region,
                      "outcome": "robots_disallowed", "target_requested": False, "attempts": [], **policy}
            self.client.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.client.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            raise FetchRefused(f"robots.txt disallows {safe_url(url)} ({policy['robots_matched_rule']})")
        if self.visit_time and not in_visit_window(self.visit_time, self.clock()):
            raise FetchRefused(f"Outside the host's Visit-time window {self.visit_time} (UTC)")
        return policy

    def fetch_game(self, schedule_url, opponent, game_date, raw_dir):
        if isinstance(game_date, str):
            game_date = date.fromisoformat(game_date)
        self.load_robots()
        schedule = self._fetch(schedule_url, "schedule", self.check(schedule_url))
        box_url = find_boxscore(schedule.html, schedule_url, self.host, opponent, game_date)
        box = self._fetch(box_url, "boxscore", self.check(box_url))
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = raw_dir / (urlsplit(box_url).path.strip("/").replace("/", "_") + ".html")
        saved.write_text(box.html, encoding="utf-8", newline="")
        (raw_dir / (urlsplit(schedule_url).path.strip("/").replace("/", "_") + ".html")).write_text(
            schedule.html, encoding="utf-8", newline="")
        return box_url, box.html, saved
