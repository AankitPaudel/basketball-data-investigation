"""Per-host request pacing that survives across runs.

The last request time for each host is kept in a small JSON state file, so a new run
waits for whatever remains of the delay instead of starting its timer from zero (the
2026-11-18 Bowdoin fetch sent a request 28.1 s after the previous run's last one,
against a 30 s robots.txt Crawl-delay).

Times are wall-clock UTC seconds (time.time), because monotonic clocks do not carry
across processes. A recorded time in the future (clock moved back) counts as "just
now", so the full delay is applied. Single-process use only; no file locking.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE = Path("logs/host-pacing-state.json")


class HostPacer:
    def __init__(self, state_path=DEFAULT_STATE, min_delay=10.0, clock=time.time, sleep=time.sleep):
        if min_delay < 10:
            raise ValueError("Project minimum is 10 s between requests to one host")
        self.state_path = Path(state_path)
        self.min_delay = float(min_delay)
        self.clock = clock
        self.sleep = sleep

    def _read(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(state, dict):
            raise ValueError(f"{self.state_path}: pacing state must be a JSON object")
        return state

    def _write(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="")
        os.replace(temporary, self.state_path)

    def delay_for(self, crawl_delay=None):
        """The longer of the project minimum and the host's robots.txt Crawl-delay."""
        return max(self.min_delay, float(crawl_delay or 0))

    def last_request(self, host):
        entry = self._read().get(host)
        return None if entry is None else float(entry["last_request_epoch"])

    def saved_crawl_delay(self, host):
        """Crawl-delay recorded by an earlier run, used before this run has re-read robots.txt."""
        value = (self._read().get(host) or {}).get("crawl_delay_seconds")
        return None if value is None else float(value)

    def wait(self, host, crawl_delay=None):
        """Sleep until the delay since the host's last recorded request has passed; return seconds slept.

        The delay is the longest of the 10 s minimum, the crawl-delay passed in, and the
        crawl-delay saved by an earlier run, so a run's first request (robots.txt itself)
        is paced too.
        """
        last = self.last_request(host)
        if last is None:
            return 0.0
        delay = max(self.delay_for(crawl_delay), self.delay_for(self.saved_crawl_delay(host)))
        remaining = delay - max(0.0, self.clock() - last) if last <= self.clock() else delay
        if remaining > 0:
            self.sleep(remaining)
            return remaining
        return 0.0

    def mark(self, host, crawl_delay=None):
        """Record a request to the host as finished now (call after every request, success or not).

        A crawl-delay read from robots.txt replaces the saved one; with none known, the
        saved value is kept.
        """
        state = self._read()
        now = self.clock()
        saved = (state.get(host) or {}).get("crawl_delay_seconds")
        state[host] = {"last_request_epoch": now,
                       "last_request_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                       "crawl_delay_seconds": float(crawl_delay) if crawl_delay is not None else saved}
        self._write(state)

    def seed_from_log(self, log_path):
        """Take each host's latest request (start time + elapsed) from a JSONL request log."""
        state = self._read()
        latest_delay = {}
        for line in Path(log_path).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("host") and record.get("checked_at") and record.get("crawl_delay_seconds") is not None:
                when = datetime.fromisoformat(record["checked_at"]).timestamp()
                if record["host"] not in latest_delay or when >= latest_delay[record["host"]][0]:
                    latest_delay[record["host"]] = (when, float(record["crawl_delay_seconds"]))
        for line in Path(log_path).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            host, checked = record.get("host"), record.get("checked_at")
            if not host or not checked or not record.get("attempts"):
                continue  # robots-skipped records sent nothing
            finished = datetime.fromisoformat(checked).timestamp() + float(record.get("elapsed_seconds") or 0)
            if host not in state or finished > float(state[host]["last_request_epoch"]):
                previous = (state.get(host) or {}).get("crawl_delay_seconds")
                logged = record.get("crawl_delay_seconds")
                state[host] = {"last_request_epoch": finished,
                               "last_request_utc": datetime.fromtimestamp(finished, timezone.utc).isoformat(),
                               "crawl_delay_seconds": float(logged) if logged is not None else previous}
        for host, (_, delay) in latest_delay.items():
            if host in state and state[host].get("crawl_delay_seconds") is None:
                state[host]["crawl_delay_seconds"] = delay  # e.g. state written before delays were saved
        self._write(state)
        return state
