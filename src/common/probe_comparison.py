"""Compare recorded probe observations without contacting any host."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .http_client import classify_response, safe_url, sanitized_request_headers, sanitized_response_headers
from .probe import NCAA_ROBOTS_URL, check_output

ENVIRONMENT_FIELDS = ("environment", "started_at", "region", "platform", "python", "requests", "urllib3", "runner",
                      "config_sha256", "proxy_environment_configured", "no_proxy_environment_configured", "network_provider")
ADDITIONAL_ENVIRONMENT_FIELDS = ("openssl", "ipv6_socket_support", "request_config", "request_window_utc")
ATTEMPT_FIELDS = ("method", "status_code", "content_type", "response_bytes", "body_sha256", "elapsed_seconds",
                  "retry_after_present", "response_signals", "reference_ids", "destination_hostname", "dns", "http_version",
                  "peer_address", "error_category")
RESPONSE_METADATA = ("method", "request_headers", "response_headers", "content_type", "response_bytes", "body_sha256",
                     "elapsed_seconds", "response_signals", "reference_ids", "destination_hostname", "dns", "http_version",
                     "peer_address")


def recorded_classification(record: dict) -> str:
    outcome = record.get("outcome", "")
    if record.get("classification"):
        return record["classification"]
    if outcome.startswith("robots_"):
        return "robots_policy_restriction"
    if outcome in {"policy_only_not_requested", "skipped_host_stopped"}:
        return "not_requested"
    if outcome == "network_error" or (record.get("error_category") and record.get("status_code") is None):
        return record.get("error_category") or "unknown_response"
    if outcome == "access_challenge":
        return "legacy_blocker_ambiguous"
    if outcome == "login_required":
        return "authentication_required"
    return classify_response(record.get("status_code"), [])


def attempt_details(attempt: dict) -> dict:
    request = attempt.get("request_headers")
    response = attempt.get("response_headers")
    if response is not None:
        # A current record stores recognized Server and Via product names as lists.
        response = {key: " ".join(value) if isinstance(value, list) else value for key, value in response.items()}
    recorded = {key: attempt.get(key) for key in ATTEMPT_FIELDS}
    if "request_started_at" in attempt:
        recorded["request_started_at"] = attempt["request_started_at"]
    recorded.update(url=safe_url(attempt["url"]) if attempt.get("url") else None,
                    classification=recorded_classification(attempt),
                    request_headers=sanitized_request_headers(request),
                    response_headers=sanitized_response_headers(response) if response is not None else None)
    recorded["missing_fields"] = [key for key in RESPONSE_METADATA if recorded[key] is None]
    return recorded


def summarize_run(path: Path, focus_url: str) -> dict:
    content = path.read_bytes()
    rows = []
    for line, raw in enumerate(content.decode("utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid JSON in {path.name} at line {line}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Expected a diagnostic object in {path.name} at line {line}")
        rows.append((line, record))
    environments = {record["environment"] for _, record in rows if record.get("environment")}
    run_ids = {record["run_id"] for _, record in rows if record.get("run_id")}
    if len(environments) != 1 or len(run_ids) > 1:
        raise ValueError(f"{path.name} must contain diagnostic records for one run and environment")
    start = next((record for _, record in rows if record.get("kind") == "run_start"), {})
    end = next((record for _, record in rows if record.get("kind") == "run_end"), {})
    metadata = {key: start.get(key) for key in ENVIRONMENT_FIELDS}
    metadata.update({key: start[key] for key in ADDITIONAL_ENVIRONMENT_FIELDS if key in start})
    metadata["environment"] = next(iter(environments))
    focus_host = urlsplit(focus_url).hostname
    endpoints = defaultdict(list)
    focus = []
    for line, record in rows:
        if not record.get("requested_url"):
            continue
        url = safe_url(record["requested_url"])
        classification = recorded_classification(record)
        attempts = record.get("attempts") or []
        endpoints[url].append((line, record.get("kind"), record.get("outcome"), classification,
                               record.get("status_code"), len(attempts)))
        if url == focus_url:
            focus.append({"line": line, "checked_at": record.get("checked_at"), "method": record.get("method"),
                          "outcome": record.get("outcome"), "classification": classification,
                          "status_code": record.get("status_code"), "elapsed_seconds": record.get("elapsed_seconds"),
                          "legacy_challenge_flag": record.get("challenge_detected"),
                          "redirect_history": [{"from": safe_url(item["from"]),
                                                "to": safe_url(item["to"]) if item.get("to") else None,
                                                "status_code": item["status_code"],
                                                **({"followed": item["followed"]} if "followed" in item else {})}
                                               for item in record["redirect_history"]] if "redirect_history" in record else None,
                          "attempts": [attempt_details(attempt) for attempt in attempts]})
            if "window_closed_stage" in record:
                focus[-1]["window_closed_stage"] = record["window_closed_stage"]
    if not endpoints:
        raise ValueError(f"{path.name} contains no request observations")
    return {"source": {"file": path.name, "sha256": hashlib.sha256(content).hexdigest(), "run_id": next(iter(run_ids), None)},
            "metadata": metadata, "missing_environment_fields": [key for key, value in metadata.items() if value in (None, "unknown")],
            "finished_at": end.get("finished_at"), "reported_requests_sent": end.get("requests_sent"),
            "reported_stopped_hosts": end.get("stopped_hosts"), "robots_policy": end.get("robots", {}).get(focus_host),
            "focus_observations": focus, "endpoints": endpoints}


def compare_runs(left_path: Path, right_path: Path, url: str = NCAA_ROBOTS_URL) -> dict:
    url = safe_url(url)
    left, right = summarize_run(left_path, url), summarize_run(right_path, url)
    left_endpoints, right_endpoints = left.pop("endpoints"), right.pop("endpoints")
    endpoints = []
    for endpoint in sorted(left_endpoints.keys() | right_endpoints.keys()):
        comparison = {"url": endpoint}
        for side, source in (("left", left_endpoints), ("right", right_endpoints)):
            if endpoint not in source:
                comparison[side] = None
                continue
            lines, kinds, outcomes, categories, statuses, attempts = zip(*source[endpoint])
            comparison[side] = {"lines": lines, "kind": kinds[0], "outcomes": dict(Counter(outcomes)),
                                "classifications": dict(Counter(categories)), "status_codes": list(statuses),
                                "attempt_count": sum(attempts)}
        endpoints.append(comparison)
    left_focus, right_focus = left["focus_observations"], right["focus_observations"]
    comparable = all(any(record["outcome"] != "window_closed" for record in side) for side in (left_focus, right_focus))
    focus_differences = []
    if comparable:
        for key in ("status_code", "classification", "method", "redirect_history"):
            values = [[record[key] for record in side] for side in (left_focus, right_focus)]
            if key in {"method", "redirect_history"} and any(None in group for group in values):
                continue
            if values[0] != values[1]:
                focus_differences.append(key)
        for key in RESPONSE_METADATA:
            if key == "method":
                continue
            values = [[attempt[key] for record in side for attempt in record["attempts"]]
                      for side in (left_focus, right_focus)]
            if all(group and all(value is not None for value in group) for group in values) and values[0] != values[1]:
                focus_differences.append(key)
    observed = ("Insufficient comparable records for the selected URL." if not comparable else
                "Access behavior differs between the recorded observations." if
                {"status_code", "classification"}.intersection(focus_differences) else
                "Recorded status and classification agree at the selected URL.")
    report = {"schema_version": 1, "url": url, "observation": observed, "exact_root_cause": "undetermined",
            "interpretation": "A status difference and any provider indicators are observations. Rule attribution requires independent evidence.",
            "left": left, "right": right, "focus_differences": focus_differences,
            "environment_differences": [key for key in (*ENVIRONMENT_FIELDS, *ADDITIONAL_ENVIRONMENT_FIELDS)
                                        if left["metadata"].get(key) is not None and right["metadata"].get(key) is not None
                                        and left["metadata"][key] != right["metadata"][key]],
            "endpoints": endpoints}
    starts = [[attempt.get("request_started_at") for record in side for attempt in record["attempts"]]
              for side in (left_focus, right_focus)]
    if all(len(group) == 1 and group[0] for group in starts):
        first, second = (datetime.fromisoformat(group[0]) for group in starts)
        report["request_start_gap_seconds"] = round(abs((second - first).total_seconds()), 3)
    return report


def write_comparison(report: dict, output: Path, inputs: tuple[Path, Path]) -> None:
    output = check_output(output)
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("Comparison output must be separate from the source logs")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
