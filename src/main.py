"""Run with python -m src.main from the repository root."""
import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .common.export import export_records
from .common.http_client import validate_url
from .common.models import utc_now
from .common.probe import (NCAA_ROBOTS_URL, POLICY_ONLY_HOSTS, Probe, diagnostic_metadata, diagnostic_plan, load_config,
                           plan_requests, request_window, runner_metadata)
from .common.probe_comparison import compare_runs, write_comparison as write_probe_comparison
from .common.sanitize import sanitize_html
from .common.validation import validate
from .ncaa.boxscore_parser import parse_boxscore as ncaa_boxscore
from .ncaa.client import NCAAClient
from .ncaa.roster_parser import parse_roster
from .ncaa.affiliations import load_affiliations
from .ncaa.combine import combine_games
from .ncaa.discovery import write_discovery
from .ncaa.pipeline import run_pipeline, write_atomic
from .ncaa.real_game import build_bundle
from .ncaa.scoreboard_parser import parse_scoreboard
from .school.compare import compare, write_comparison
from .school.fetcher import FetchRefused, SchoolFetcher
from .school.pacing import HostPacer
from .school.sidearm_boxscore import parse_sidearm_boxscore

ROOT = Path(__file__).resolve().parents[1]


def parse_and_export(html, args, provenance):
    parser = parse_roster if args.kind == "roster" else ncaa_boxscore
    records = parser(html, source_url=args.url, game_id=args.game_id,
        game_date=args.game_date, team=args.team, provenance=provenance)
    validate(records, args.kind)
    payload = export_records(records, args.output, args.kind)
    print(json.dumps({"output": str(args.output), "record_count": len(records),
                      "records_with_issues": payload["records_with_issues"],
                      "provenance": provenance}, indent=2))
    return 2 if payload["records_with_issues"] else 0


def read_scoreboard(path, source_url):
    discovery = parse_scoreboard(Path(path).read_bytes(), source_url=source_url)
    discovery["provenance"]["source_file"] = Path(path).name
    return discovery


def build_game(*, scoreboard, individual_stats, box_score=None, rosters=(), affiliations=None, output,
               permission_reference=None, verification_reference=None, extracted_at=None):
    """Build one game's manifest from saved pages and run the pipeline; returns (exit status, summary)."""
    page = lambda pair: (Path(pair[0]).read_bytes(), pair[1])
    bundle = build_bundle(scoreboard=page(scoreboard), individual_stats=page(individual_stats),
        box_score=page(box_score) if box_score else None, rosters=[page(pair) for pair in rosters],
        permission_reference=permission_reference, verification_reference=verification_reference,
        extracted_at=extracted_at)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.json"
    write_atomic(manifest, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    mapping = load_affiliations(affiliations) if affiliations else None
    quality, csv_path = run_pipeline(manifest, output, bundle["season"], bundle["ncaa_season_id"], affiliations=mapping)
    failed = [entry for entry in quality["source_checks"] if entry["status"] != "pass"]
    summary = {"game_id": bundle["games"][0]["game_id"], "csv": str(csv_path), "provenance": quality["provenance"],
        "rows": quality["row_count"], "validation_counts": quality["validation_counts"],
        "player_match_counts": quality["player_match_counts"],
        "affiliations": [{key: entry.get(key) for key in ("team_name", "division", "institution_division", "conference")}
                         for entry in quality.get("affiliations", {}).get("applied", [])],
        "issue_counts": quality["issue_counts"], "csv_sha256": quality["csv_sha256"],
        "team_score_checks": [{key: entry[key] for key in ("team_name", "source_score", "player_points_sum", "status")}
                              for entry in quality["team_score_checks"]],
        "source_checks": {"passed": len(quality["source_checks"]) - len(failed), "failed": failed},
        "quality_report": str(csv_path.parent / "quality_report.json")}
    status = 2 if failed or any(quality["validation_counts"].get(key, 0) for key in ("WARNING", "FAIL")) else 0
    return status, summary


def build_plan(plan_path, only=None):
    """Process every game listed in a plan file; one failing game does not stop the others."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8-sig"))
    if plan.get("schema_version") != 1 or not isinstance(plan.get("games"), list):
        raise ValueError("Plan needs schema_version 1 and a games list")
    results, status = [], 0
    for game in plan["games"]:
        if only and str(game.get("game_id")) not in only:
            continue
        try:
            code, summary = build_game(scoreboard=game["scoreboard"], individual_stats=game["individual_stats"],
                box_score=game.get("box_score"), rosters=game.get("rosters", []),
                affiliations=game.get("affiliations", plan.get("affiliations")), output=game["output"],
                extracted_at=game.get("extracted_at"))
            expected = game.get("expected_csv_sha256")
            if expected:
                # Rebuild check: compare the CSV content hash (LF line endings, platform-independent).
                summary["expected_csv_sha256"] = expected
                summary["rebuild_match"] = summary["csv_sha256"] == expected
                if not summary["rebuild_match"]:
                    code = max(code, 2)
            results.append({"label": game.get("label"), "status": code, **summary})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            code = 4
            results.append({"label": game.get("label"), "game_id": game.get("game_id"), "status": code,
                            "error": str(exc), "type": type(exc).__name__})
        status = max(status, code)
    if not results:
        raise ValueError("No plan games selected")
    print(json.dumps({"games": results}, indent=2))
    return status


def _iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from None


def fetch_school_boxscore(args):
    """Plan or run: robots -> schedule -> box score, then parse and compare. Exit 3 on any policy stop."""
    schedule = urlsplit(args.schedule_url)
    if schedule.scheme != "https" or schedule.hostname != args.host or schedule.username or schedule.port:
        raise ValueError("--schedule-url must be an HTTPS URL on --host")
    if args.host in POLICY_ONLY_HOSTS or args.host.endswith("ncaa.org"):
        raise ValueError("NCAA hosts are manual-only; this command is for school athletics sites")
    if not (args.ncaa_game_dir / "manifest.json").exists():
        raise ValueError(f"{args.ncaa_game_dir} is not a built NCAA game folder")
    if "exports" in args.output.parts:
        raise ValueError("Comparison output never goes to data/exports")
    log = args.log or Path(f"logs/school-fetch-{args.host}-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
    raw_dir = args.raw_dir or Path("data/raw/school") / args.host
    plan = {"host": args.host, "requests_in_order": [f"https://{args.host}/robots.txt", args.schedule_url,
            f"box score for {args.opponent!r} on {args.date.isoformat()} (found on the schedule page)"],
            "delay": "max(10 s, robots Crawl-delay) since the last request to the host, persisted in "
                     + args.pacing_state.as_posix(), "log": log.as_posix(), "raw_dir": raw_dir.as_posix(),
            "compare_with": args.ncaa_game_dir.as_posix(), "output": args.output.as_posix()}
    if not args.execute:
        print(json.dumps({"dry_run": True, **plan}, indent=2))
        return 0
    fetcher = SchoolFetcher(args.host, log, args.environment, args.region, pacer=HostPacer(args.pacing_state))
    try:
        box_url, html, saved = fetcher.fetch_game(args.schedule_url, args.opponent, args.date, raw_dir)
    except FetchRefused as exc:
        print(json.dumps({"stopped": str(exc), "requests": fetcher.requests, "log": log.as_posix()}, indent=2))
        return 3
    parsed = parse_sidearm_boxscore(html, box_url)
    report = compare(parsed, args.ncaa_game_dir)
    write_comparison(parsed, report, args.output)
    counts = report["counts"]
    print(json.dumps({"boxscore_url": box_url, "saved": saved.as_posix(), "requests": fetcher.requests,
                      "counts": counts, "school_source_checks_failed": len(report["school_source_checks_failed"]),
                      "output": args.output.as_posix()}, indent=2))
    clean = (not report["unmatched"] and not report["school_source_checks_failed"]
             and counts["matched_all_stats_agree"] == counts["matched"])
    return 0 if clean else 2


def build_parser():
    parser = argparse.ArgumentParser(description="NCAA basketball diagnostics, parsing, and enrichment")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("diagnose", "parse", "collect"):
        command = commands.add_parser(name)
        command.add_argument("source", choices=("ncaa",))
        command.add_argument("url", help="HTTPS source URL (query values are redacted in output)")
        if name in ("diagnose", "collect"):
            command.add_argument("--log", default="logs/requests.jsonl")
            command.add_argument("--environment", default="unspecified",
                                 help="Operator-supplied label, e.g. local-windows or cloud-vm")
            command.add_argument("--save-html", type=Path,
                                 help="Explicitly retain accessible HTML locally; never saves denial pages")
        if name in ("parse", "collect"):
            command.add_argument("--kind", choices=("boxscore", "roster"), default="boxscore")
            command.add_argument("--game-id")
            command.add_argument("--game-date", help="ISO date, supplied by the operator")
            command.add_argument("--team", help="Team name for a single table without a team caption")
            command.add_argument("--output", type=Path, default=Path("data/processed"))
        if name == "parse":
            command.add_argument("--file", type=Path, required=True)
    demo = commands.add_parser("demo", help="Offline fictional examples; makes no HTTP requests")
    demo.add_argument("--output", type=Path, default=Path("data/processed/demo"))
    pipeline = commands.add_parser("process-ncaa", help="Offline enrichment of an explicitly sourced JSON bundle")
    pipeline.add_argument("--manifest", type=Path, required=True)
    pipeline.add_argument("--season", default="2026-27", help="Expected season label; never inferred from an NCAA numeric ID")
    pipeline.add_argument("--ncaa-season-id", help="Optional source ID observed on NCAA; no guessed default")
    pipeline.add_argument("--output", type=Path)
    pipeline.add_argument("--scoreboard", type=Path, help="Saved NCAA scoreboard HTML to cross-check games against")
    pipeline.add_argument("--scoreboard-url", help="Source URL of --scoreboard (required with it)")
    pipeline.add_argument("--affiliations", type=Path, help="Source-backed team division/conference mapping")
    game = commands.add_parser("build-ncaa-game",
        help="Build and process one real game from saved NCAA pages (scoreboard, individual stats, rosters)")
    for flag, required in (("scoreboard", True), ("individual-stats", True), ("box-score", False)):
        game.add_argument(f"--{flag}", nargs=2, metavar=("FILE", "URL"), required=required,
                          help="Saved HTML file and the stats.ncaa.org URL it was saved from")
    game.add_argument("--roster", nargs=2, metavar=("FILE", "URL"), action="append", default=[],
                      help="Saved /teams/{id}/roster page and its URL; repeat per team")
    game.add_argument("--permission-reference", help="Operator attestation of permission; never defaulted")
    game.add_argument("--verification-reference", help="Operator reference for a reviewed manual/source comparison")
    game.add_argument("--extracted-at", help="Timezone-aware ISO timestamp (default: now)")
    game.add_argument("--affiliations", type=Path, help="Source-backed team division/conference mapping")
    game.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("build-ncaa-games", help="Process every game listed in a plan file")
    plan.add_argument("--plan", type=Path, default=Path("config/ncaa_games.json"))
    plan.add_argument("--only", nargs="+", metavar="GAME_ID", help="Process only these game IDs")
    combine = commands.add_parser("combine-ncaa-games",
        help="Merge the per-game CSVs of a plan into one CSV with a column dictionary (offline)")
    combine.add_argument("--plan", type=Path, default=Path("config/ncaa_games.json"))
    combine.add_argument("--only", nargs="+", metavar="GAME_ID", help="Combine only these game IDs")
    combine.add_argument("--output", type=Path, default=Path("data/processed/ncaa-combined"))
    combine.add_argument("--expect-sha256", help="Exit 2 unless the combined CSV has this SHA-256 (rebuild check)")
    school = commands.add_parser("fetch-school-boxscore",
        help="Fetch one game's box score from a permitted Sidearm school site and compare it with an NCAA game")
    school.add_argument("--host", required=True, help="School athletics host, e.g. athletics.bowdoin.edu")
    school.add_argument("--schedule-url", required=True, help="HTTPS schedule page on --host listing the game")
    school.add_argument("--opponent", required=True, help="Opponent name as shown on the schedule (substring)")
    school.add_argument("--date", required=True, type=_iso_date, help="Game date, YYYY-MM-DD")
    school.add_argument("--ncaa-game-dir", type=Path, required=True,
                        help="Validated NCAA game folder to compare against (read only)")
    school.add_argument("--output", type=Path, required=True)
    school.add_argument("--environment", required=True, help="Label, e.g. local-windows")
    school.add_argument("--region", default="unknown")
    school.add_argument("--log", type=Path, help="Request log (default logs/school-fetch-<host>-<UTC date>.jsonl)")
    school.add_argument("--raw-dir", type=Path, help="Where the fetched pages are saved (default data/raw/school/<host>)")
    school.add_argument("--pacing-state", type=Path, default=Path("logs/host-pacing-state.json"))
    school.add_argument("--execute", action="store_true",
                        help="Actually send requests (otherwise print the plan; no network access)")
    probe = commands.add_parser("probe", help="Robots-respecting access probe; dry run unless --execute")
    probe.add_argument("--config", type=Path, default=Path("config/access_probe_urls.json"))
    probe.add_argument("--environment", required=True, help="Label, e.g. local-windows or cloud-github-actions-ubuntu")
    probe.add_argument("--region", default="unknown", help="Runner location if actually known; otherwise unknown")
    probe.add_argument("--output", type=Path, default=Path("logs"))
    probe.add_argument("--execute", action="store_true",
                       help="Actually send requests (only after the run has been approved)")
    probe.add_argument("--capture-diagnostics", action="store_true",
                       help="Include sanitized HTTP and destination network metadata; DNS only with --execute")
    probe.add_argument("--window-start-utc", help="Start of an approved 120-second NCAA robots window (YYYY-MM-DDTHH:MM:SSZ)")
    probe.add_argument("--network-provider", help="Verified network provider name, if known")
    probe.add_argument("--network-asn", type=int, help="Verified network ASN as a number, if known")
    probe.add_argument("--network-source", choices=("operator", "provider-console", "existing-network-record"),
                       help="Provenance for an operator-supplied provider or ASN; no external lookup")
    comparison = commands.add_parser("compare-probes", help="Compare two saved JSONL probe runs offline")
    comparison.add_argument("left", type=Path, help="First diagnostic JSONL log")
    comparison.add_argument("right", type=Path, help="Second diagnostic JSONL log")
    comparison.add_argument("--url", default=NCAA_ROBOTS_URL, help="Endpoint to compare in detail (default: NCAA robots.txt)")
    comparison.add_argument("--output", type=Path, help="Write JSON to this path (default: print JSON)")
    sanitize = commands.add_parser("sanitize-html", help="Remove scripts and session tokens from a saved page")
    sanitize.add_argument("--file", type=Path, required=True)
    sanitize.add_argument("--output", type=Path, required=True)
    discover = commands.add_parser("discover-ncaa-games",
        help="Parse a saved NCAA livestream scoreboard into game-level records (no player statistics)")
    discover.add_argument("--file", type=Path, required=True)
    discover.add_argument("--source-url", required=True, help="URL the HTML was saved from")
    discover.add_argument("--output", type=Path, default=Path("data/processed/ncaa-discovery"))
    return parser


def probe_network_provider(args):
    if not (args.network_provider is not None or args.network_asn is not None or args.network_source):
        return None
    if not args.capture_diagnostics or not args.network_source or (args.network_provider is None and args.network_asn is None):
        raise ValueError("Network attribution requires --capture-diagnostics, --network-source and a provider or ASN")
    if args.network_provider is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .&()-]{0,79}", args.network_provider):
        raise ValueError("Supply a short public network provider name")
    if args.network_asn is not None and not 1 <= args.network_asn <= 4294967295:
        raise ValueError("Network ASN must be a positive 32-bit AS number")
    return {"provider": args.network_provider, "asn": args.network_asn, "source": args.network_source}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            config = load_config(args.config)
            provider = probe_network_provider(args)
            window = request_window(config, args.window_start_utc)
            if window and not args.capture_diagnostics:
                raise ValueError("An execution window requires --capture-diagnostics")
            if not args.execute:
                # Dry run: show exactly what would be requested; no network access.
                plan = {"dry_run": True, "environment": args.environment, "region": args.region,
                        "config_sha256": config["config_sha256"], **plan_requests(config)}
                if args.capture_diagnostics:
                    plan.update({**runner_metadata(args.region), **diagnostic_metadata(),
                                 **diagnostic_plan(config, window),
                                 "checked_at": utc_now(), "network_provider": provider})
                print(json.dumps(plan, indent=2))
                return 0
            summary = Probe(config, environment=args.environment, region=args.region, output=args.output,
                            capture_diagnostics=args.capture_diagnostics, network_provider=provider, window=window).run()
            print(json.dumps({key: summary[key] for key in ("run_id", "log", "summary", "requests_sent", "stopped_hosts", "robots")},
                             indent=2))
            return 3 if window and not summary["requests_sent"] else 0
        if args.command == "compare-probes":
            report = compare_runs(args.left, args.right, args.url)
            if args.output:
                write_probe_comparison(report, args.output, (args.left, args.right))
                print(json.dumps({"output": str(args.output), "observation": report["observation"]}))
            else:
                print(json.dumps(report, indent=2))
            return 0
        if args.command == "sanitize-html":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(sanitize_html(args.file.read_bytes()), encoding="utf-8", newline="")
            print(json.dumps({"output": str(args.output)}))
            return 0
        if args.command == "build-ncaa-game":
            status, summary = build_game(scoreboard=args.scoreboard, individual_stats=args.individual_stats,
                box_score=args.box_score, rosters=args.roster, affiliations=args.affiliations, output=args.output,
                permission_reference=args.permission_reference,
                verification_reference=args.verification_reference, extracted_at=args.extracted_at)
            print(json.dumps(summary, indent=2))
            return status
        if args.command == "build-ncaa-games":
            return build_plan(args.plan, args.only)
        if args.command == "fetch-school-boxscore":
            return fetch_school_boxscore(args)
        if args.command == "combine-ncaa-games":
            report = combine_games(args.plan, args.output, args.only)
            summary = {key: report[key] for key in ("csv", "csv_sha256", "rows", "games", "provenance",
                                                    "rows_by_division", "validation_counts")}
            if args.expect_sha256:
                summary.update(expected_sha256=args.expect_sha256,
                               rebuild_match=report["csv_sha256"] == args.expect_sha256)
            print(json.dumps(summary, indent=2))
            return 2 if args.expect_sha256 and not summary["rebuild_match"] else 0
        if args.command == "discover-ncaa-games":
            discovery = read_scoreboard(args.file, args.source_url)
            paths = write_discovery(discovery, args.output)
            flagged = sum(bool(game["issues"]) for game in discovery["games"])
            print(json.dumps({**discovery["counts"], "games_with_issues": flagged,
                "player_statistics_verified": False,
                "outputs": {key: str(path) for key, path in paths.items()}}, indent=2))
            return 2 if flagged else 0
        if args.command == "process-ncaa":
            if bool(args.scoreboard) != bool(args.scoreboard_url):
                raise ValueError("--scoreboard and --scoreboard-url must be given together")
            scoreboard = read_scoreboard(args.scoreboard, args.scoreboard_url) if args.scoreboard else None
            affiliations = load_affiliations(args.affiliations) if args.affiliations else None
            quality, csv_path = run_pipeline(args.manifest, args.output, args.season, args.ncaa_season_id, scoreboard,
                                             affiliations)
            mismatches = [check for check in quality.get("scoreboard_cross_check", {}).get("games", [])
                          if check["status"] != "match"]
            print(json.dumps({"csv": str(csv_path), "provenance": quality["provenance"],
                "games": quality["game_count"], "rows": quality["row_count"],
                "validation_counts": quality["validation_counts"],
                "quality_report": str(csv_path.parent / "quality_report.json"),
                **({"scoreboard_mismatches": len(mismatches)} if scoreboard else {})}, indent=2))
            return 2 if mismatches or any(quality["validation_counts"].get(key, 0) for key in ("WARNING", "FAIL")) else 0
        if args.command == "demo":
            status = 0
            for kind, fixture in (
                    ("boxscore", "ncaa_boxscore_synthetic.html"),
                    ("roster", "ncaa_roster_synthetic.html")):
                metadata = argparse.Namespace(source="ncaa", kind=kind,
                    url="https://stats.ncaa.org/synthetic-demo",
                    game_id="SYNTHETIC-001" if kind == "boxscore" else None,
                    game_date="2026-01-01" if kind == "boxscore" else None,
                    team=None, output=args.output / "ncaa" / kind)
                html = (ROOT / "tests" / "fixtures" / fixture).read_text(encoding="utf-8")
                status = max(status, parse_and_export(html, metadata, "synthetic"))
            return status
        validate_url(args.url, args.source)
        if args.command in ("parse", "collect"):
            if args.kind == "boxscore" and not args.game_id:
                raise ValueError("Box scores require --game-id")
        if args.command == "parse":
            return parse_and_export(args.file.read_text(encoding="utf-8"), args, "local_unverified")
        result = NCAAClient(log_path=args.log, environment=args.environment).fetch(args.url)
        print(json.dumps(result.report, indent=2))
        if result.html is None:
            return 0 if args.command == "diagnose" and result.report.get("status_code") == 200 else 3
        if args.save_html:
            args.save_html.parent.mkdir(parents=True, exist_ok=True)
            args.save_html.write_text(result.html, encoding="utf-8")
        if args.command == "collect":
            return parse_and_export(result.html, args, "live_http_unverified_layout")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # OS errors can include a local path but no HTTP credentials are printed here.
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
