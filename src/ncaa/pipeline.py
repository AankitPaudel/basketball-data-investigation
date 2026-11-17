"""Deterministic offline enrichment of explicitly sourced records or saved flat HTML.

The JSON bundle is an input contract, NOT an asserted NCAA API response format.
No current live NCAA adapter is verified. HTML support reuses the starter parser.
"""
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..common.export import csv_safe
from ..common.http_client import safe_url, validate_url
from ..common.models import PlayerRecord
from ..common.tables import STAT_KEYS
from ..common.validation import validate
from .affiliations import apply_affiliations
from .boxscore_parser import parse_boxscore
from .enrichment import (clean, match_conference, match_player, matching_rosters,
                         name_key, normalize_class, normalize_height,
                         normalize_position, same_team)
from .roster_parser import parse_roster

STAT_COLUMNS = {
    "fgm": "fgm", "fga": "fga", "three_pm": "fg3m", "three_pa": "fg3a",
    "ftm": "ftm", "fta": "fta", "points": "pts", "offensive_rebounds": "oreb",
    "defensive_rebounds": "dreb", "total_rebounds": "reb", "assists": "ast",
    "steals": "stl", "blocks": "blk", "turnovers": "tov", "fouls": "pf",
}
REFERENCE_HOSTS = {"stats.ncaa.org", "web3.ncaa.org"}
PROVENANCES = {"synthetic", "local_validation_preview", "authorized_source"}
SOURCE_VERIFICATION = {"synthetic": "synthetic", "local_validation_preview": "real_html_not_permission_attested",
                       "authorized_source": "operator_attested"}
COLUMNS = ["season", "sport", "division", "game_id", "game_date", "team_id", "team_name",
    "opponent_team_id", "opponent_name", "conference", "player_id", "player_name",
    "jersey_number", "position", "height", "class_year", "hometown", "minutes",
    *STAT_COLUMNS, "boxscore_source_url", "roster_source_url", "conference_source_url",
    "extracted_at", "player_match_status", "validation_status"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_url(value):
    if value is None:
        return None
    validate_url(value, "ncaa")
    return safe_url(value)


def reference_url(value):
    """Conference evidence may also come from the NCAA Directory (web3.ncaa.org)."""
    if value is None:
        return None
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname in REFERENCE_HOSTS and not parsed.username
            and not parsed.password and parsed.port in (None, 443),
            "Conference source must be an HTTPS stats.ncaa.org or web3.ncaa.org URL")
    return safe_url(value)


def timestamp(value):
    require(isinstance(value, str), "extracted_at must be an ISO timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "extracted_at must include a timezone")
    return value


def roster_from_html(roster, base):
    if "file" not in roster:
        return roster
    require("players" not in roster, "Roster must specify either file or players")
    path = base / roster["file"]
    records = parse_roster(path.read_text(encoding="utf-8"),
        source_url=roster["source_url"], team=roster.get("team_name"))
    return {**roster, "players": [{"player_id": record.player_id,
        "player_name": record.player, "jersey_number": record.jersey,
        "position": record.position} for record in records]}


def players_from_html(game, base, extracted_at):
    if "boxscore_file" not in game:
        return game.get("players", [])
    require("players" not in game, "Game must specify either boxscore_file or players")
    records = parse_boxscore((base / game["boxscore_file"]).read_text(encoding="utf-8"),
        source_url=game["boxscore_source_url"], game_id=game["game_id"], game_date=game["game_date"])
    players = []
    for record in records:
        teams = [team for team in game["teams"] if team["team_name"] == record.team]
        require(len(teams) == 1, "HTML team captions must match exactly one manifest team")
        players.append({"team_id": teams[0].get("team_id"), "team_name": record.team,
            "player_id": record.player_id, "player_name": record.player,
            "jersey_number": record.jersey, "position": record.position,
            "minutes": record.minutes, "participation": record.participation,
            "stats": record.stats, "parser_issues": record.validation})
    return players


def check_bundle(bundle):
    require(isinstance(bundle, dict), "Bundle must be a JSON object")
    require(bundle.get("schema_version") == 1, "Expected bundle schema_version 1")
    season = bundle.get("season", "")
    require(bool(re.fullmatch(r"\d{4}-\d{2}", season)), "season must be YYYY-YY")
    require(int(season[-2:]) == (int(season[:4]) + 1) % 100, "season years must be consecutive")
    require(bundle.get("sport") in {"MBB", "WBB"}, "sport must be MBB or WBB")
    require(bundle.get("provenance") in PROVENANCES,
            "provenance must explicitly be synthetic, local_validation_preview or authorized_source")
    timestamp(bundle["extracted_at"])
    if bundle["provenance"] == "authorized_source":
        require(bool(clean(bundle.get("permission_reference"))), "An authorized bundle needs permission_reference")
    if bundle["provenance"] == "local_validation_preview":
        # Real saved pages without an operator permission attestation: never exportable.
        require(not clean(bundle.get("verification_reference")), "A preview cannot carry a verification_reference")
        require(isinstance(bundle.get("source_files"), list) and bool(bundle["source_files"]),
                "A local validation preview must list its source_files")
    require(isinstance(bundle.get("games"), list) and bool(bundle["games"]), "Bundle has no games")
    require(isinstance(bundle.get("rosters", []), list), "rosters must be a list")
    require(isinstance(bundle.get("conferences", []), list), "conferences must be a list")
    for game in bundle["games"]:
        require(isinstance(game, dict), "Each game must be an object")
        require(game.get("status") == "final", "Only source-confirmed final games are supported (status: final)")
        require(isinstance(game.get("teams"), list) and all(isinstance(team, dict) for team in game["teams"]),
                "Game teams must be a list of objects")
        if "players" in game:
            require(isinstance(game["players"], list) and all(isinstance(row, dict) for row in game["players"]),
                    "Game players must be a list of objects")
    for entry in [*bundle.get("rosters", []), *bundle.get("conferences", [])]:
        require(isinstance(entry, dict), "Reference records must be objects")
        if "players" in entry:
            require(isinstance(entry["players"], list) and all(isinstance(row, dict) for row in entry["players"]),
                    "Roster players must be a list of objects")


def process_bundle(bundle, base=Path(".")):
    check_bundle(bundle)
    season, sport, extracted = bundle["season"], bundle["sport"], bundle["extracted_at"]
    rosters = [roster_from_html(roster, base) for roster in bundle.get("rosters", [])]
    conferences = bundle.get("conferences", [])
    for roster in rosters:
        source_url(roster.get("source_url"))
    for conference in conferences:
        reference_url(conference.get("source_url"))
    result, duplicates_removed, seen_games, team_checks = [], 0, set(), []
    for game in bundle["games"]:
        gid = clean(game.get("game_id"))
        require(gid is not None, "Each game requires its source game_id")
        require(gid not in seen_games, f"Duplicate game in bundle: {gid}")
        seen_games.add(gid)
        game_date = date.fromisoformat(game["game_date"])
        # NCAA basketball seasons span two calendar years; no exact start/end day assumed.
        require(game_date.year in {int(season[:4]), int(season[:4]) + 1}, "Game date outside season years")
        url = source_url(game["boxscore_source_url"])
        require(bool(url), "Game requires a boxscore_source_url")
        teams = game["teams"]
        require(len(teams) == 2, "A basketball game requires exactly two teams")
        require(all(clean(team.get("team_name")) for team in teams), "Both teams require names")
        require(not same_team(teams[0], teams[1]), "Game teams must be distinct")
        for team in teams:
            require(team.get("division") in {None, "I", "II", "III"}, "division must be I, II, III or null")
        players = players_from_html(game, base, extracted)
        require(game.get("player_statistics") != "pending_boxscore" or bool(players),
                f"Game {gid} was discovered from a scoreboard; add its box-score players before processing")
        require(bool(players), "Game has no player records")
        seen_rows, identities, game_rows = set(), {}, []
        for player in players:
            signature = json.dumps(player, sort_keys=True, ensure_ascii=False)
            if signature in seen_rows:
                duplicates_removed += 1
                continue
            seen_rows.add(signature)
            team_matches = [team for team in teams if same_team(player, team)]
            require(len(team_matches) == 1, "Player must map to exactly one game team")
            team = team_matches[0]
            opponent = teams[1] if team is teams[0] else teams[0]
            require(bool(clean(player.get("player_name"))), "Player name cannot be empty")
            raw_stats = player.get("stats", {})
            require(isinstance(raw_stats, dict), "Player stats must be an object")
            require(not (set(raw_stats) - set(STAT_KEYS)), "Unknown statistic key; use documented normalized keys")
            stats = {key: raw_stats.get(key) for key in STAT_KEYS}
            require(all(value is None or (type(value) is int) for value in stats.values()),
                    "Statistics must be integers or null; no coercion of source text")
            identity = (clean(team.get("team_id")) or name_key(team["team_name"]),
                        clean(player.get("player_id")) or (name_key(player["player_name"]), clean(player.get("jersey_number"))))
            require(identity not in identities, "Conflicting duplicate player-game record; input requires review")
            identities[identity] = True
            record = PlayerRecord(source="ncaa", source_url=url, game_id=gid,
                game_date=game["game_date"], team=team["team_name"], player=player["player_name"],
                player_id=clean(player.get("player_id")), minutes=clean(player.get("minutes")),
                participation=player.get("participation", "unknown"), stats=stats,
                validation=list(player.get("parser_issues", [])), extracted_at=extracted,
                provenance=bundle["provenance"])
            validate([record])
            errors = [issue for issue in record.validation if not issue.startswith("missing_")]
            warnings = [issue for issue in record.validation if issue.startswith("missing_")]
            match_status, matched, roster = match_player(player, matching_rosters(rosters, team, season, sport))
            if match_status != "matched_id":
                warnings.append("player_match:" + match_status)
            if match_status.startswith("ambiguous") or match_status == "jersey_conflict":
                errors.append("player_match:" + match_status)
            bio = matched or {}
            conference, conference_issue = match_conference(conferences, team, season, sport)
            if conference_issue:
                (errors if conference_issue.startswith("ambiguous") else warnings).append(conference_issue)
            conference = conference or {}
            # No source means no asserted conference affiliation.
            conference_url = reference_url(conference.get("source_url"))
            conference_name = clean(conference.get("conference")) if conference_url else None
            if not conference_name:
                warnings.append("missing_conference")
            if team.get("division_reclassifying"):
                # The NCAA lists this institution as still reclassifying into the division shown.
                warnings.append("division_reclassifying")
            height_raw = bio.get("height")
            height, height_issue = normalize_height(height_raw)
            if height_issue:
                warnings.append(height_issue)
            output = {"season": season, "sport": sport, "division": team.get("division"),
                "game_id": gid, "game_date": game["game_date"], "team_id": clean(team.get("team_id")),
                "team_name": clean(team["team_name"]), "opponent_team_id": clean(opponent.get("team_id")),
                "opponent_name": clean(opponent["team_name"]), "conference": conference_name,
                "player_id": record.player_id or clean(bio.get("player_id")),
                "player_name": clean(player["player_name"]),
                "jersey_number": clean(player.get("jersey_number")) or clean(bio.get("jersey_number")),
                "position": normalize_position(bio.get("position") or player.get("position")),
                "height": height, "class_year": normalize_class(bio.get("class_year")),
                "hometown": clean(bio.get("hometown")), "minutes": record.minutes,
                **{column: stats[key] for column, key in STAT_COLUMNS.items()},
                "boxscore_source_url": url, "roster_source_url": source_url(roster.get("source_url")) if roster else None,
                "conference_source_url": conference_url, "extracted_at": extracted,
                "player_match_status": match_status}
            for field in ("player_id", "team_id", "opponent_team_id", "division", "jersey_number",
                          "position", "height", "class_year", "hometown", "roster_source_url"):
                if output[field] is None:
                    warnings.append("missing_" + field)
            if matched and record.player_id and name_key(matched.get("player_name")) != name_key(player["player_name"]):
                warnings.append("id_match_name_differs")
            if (matched and clean(player.get("jersey_number")) and clean(matched.get("jersey_number"))
                    and clean(player["jersey_number"]) != clean(matched["jersey_number"])):
                warnings.append("id_match_jersey_differs")
            game_rows.append({"row": output, "errors": errors, "warnings": warnings,
                "raw": {"boxscore_player": player, "roster_player": matched, "height": height_raw},
                "team": team, "participation": record.participation})
        # Team scores only: team rebounds are not simply a sum of player rebounds.
        for team in teams:
            members = [entry for entry in game_rows if entry["team"] is team]
            require(bool(members), "Both teams must have player rows; partial-game input requires review")
            score = team.get("final_score")
            require(score is None or (type(score) is int and score >= 0), "final_score must be a nonnegative integer or null")
            adjustment = team.get("team_points_adjustment", 0)
            require(type(adjustment) is int and adjustment >= 0, "Team point adjustment must be nonnegative")
            require(not adjustment or bool(clean(team.get("team_points_adjustment_reason"))),
                    "A nonzero team point adjustment needs a source-backed explanation")
            complete = all(entry["row"]["points"] is not None or entry["participation"] == "did_not_play" for entry in members)
            total = sum(entry["row"]["points"] or 0 for entry in members)
            status = "not_checked" if score is None or not complete else ("pass" if total + adjustment == score else "fail")
            team_checks.append({"game_id": gid, "team_id": team.get("team_id"), "team_name": team["team_name"],
                "source_score": score, "player_points_sum": total, "team_points_adjustment": adjustment,
                "adjustment_reason": team.get("team_points_adjustment_reason"), "status": status})
            for entry in members:
                if status == "fail":
                    entry["errors"].append("team_score_mismatch")
                elif status == "not_checked":
                    entry["warnings"].append("team_score_not_checked")
        for entry in game_rows:
            entry["errors"] = sorted(set(entry["errors"]))
            entry["warnings"] = sorted(set(entry["warnings"]))
            entry["row"]["validation_status"] = "FAIL" if entry["errors"] else "WARNING" if entry["warnings"] else "PASS"
            result.append({key: value for key, value in entry.items() if key not in {"team", "participation"}})
    result.sort(key=lambda entry: tuple(str(entry["row"].get(key) or "") for key in ("game_date", "game_id", "team_id", "team_name", "player_id", "player_name", "jersey_number")))
    # Two different input identities may join to the same source player ID.
    enriched_keys = set()
    for entry in result:
        row = entry["row"]
        key = (row["season"], row["sport"], row["game_id"], row["team_id"] or row["team_name"],
               row["player_id"] or (row["player_name"], row["jersey_number"]))
        require(key not in enriched_keys, "Duplicate identity after roster enrichment; input requires review")
        enriched_keys.add(key)
    rows = [entry["row"] for entry in result]
    quality = {"schema_version": 1, "provenance": bundle["provenance"], "season": season,
        "ncaa_season_id": bundle.get("ncaa_season_id"), "sport": sport, "height_unit": "cm",
        "source_verification": SOURCE_VERIFICATION[bundle["provenance"]],
        "verification_reference": bundle.get("verification_reference"),
        "game_count": len(seen_games), "row_count": len(rows), "exact_duplicate_rows_removed": duplicates_removed,
        "validation_counts": dict(Counter(row["validation_status"] for row in rows)),
        "player_match_counts": dict(Counter(row["player_match_status"] for row in rows)),
        "missing_field_counts": {field: sum(row[field] is None for row in rows) for field in COLUMNS},
        "issue_counts": dict(Counter(issue for entry in result for issue in entry["errors"] + entry["warnings"])),
        "team_score_checks": team_checks,
        "source_files": bundle.get("source_files", []),
        "source_checks": [{"game_id": clean(game.get("game_id")), **entry}
                          for game in bundle["games"] for entry in game.get("source_checks", [])],
        "coverage": sorted({(row["sport"], row["division"] or "unknown") for row in rows}),
        "limitations": ["No fuzzy identity matching", "No sum-of-player-rebounds to team-rebounds check",
                        "Source metadata and normalized JSON values are supplied by the operator; not inferred"]}
    return result, quality


def write_atomic(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def run_pipeline(manifest, output=None, expected_season=None, ncaa_season_id=None, scoreboard=None,
                 affiliations=None):
    manifest = Path(manifest)
    content = manifest.read_bytes()
    bundle = json.loads(content.decode("utf-8-sig"))
    require(isinstance(bundle, dict), "Bundle must be a JSON object")
    if expected_season:
        require(bundle.get("season") == expected_season, "Manifest season does not match --season")
    if ncaa_season_id:
        require(bundle.get("ncaa_season_id") in (None, ncaa_season_id), "Conflicting NCAA season identifiers")
        bundle["ncaa_season_id"] = ncaa_season_id
    verified = (bundle.get("provenance") == "authorized_source" and bool(clean(bundle.get("verification_reference"))))
    output = Path(output or ("data/exports" if verified else "data/processed/ncaa-review"))
    export_root = Path("data/exports").resolve()
    require(verified or not output.resolve().is_relative_to(export_root),
            "data/exports is reserved for authorized sources with a verification_reference; use data/processed for tests")
    applied = apply_affiliations(bundle, affiliations) if affiliations else []
    result, quality = process_bundle(bundle, manifest.parent)
    if affiliations:
        quality["affiliations"] = {"source_file": affiliations["source_file"], "applied": applied}
    # Nothing is overwritten if validation of input/schema/identity fails above.
    quality["manifest_sha256"] = hashlib.sha256(content).hexdigest()
    input_files = []
    for item in bundle["games"] + bundle.get("rosters", []):
        file_name = item.get("boxscore_file") or item.get("file")
        if file_name:
            input_files.append({"file": file_name, "sha256": hashlib.sha256((manifest.parent / file_name).read_bytes()).hexdigest()})
    quality["input_files"] = input_files
    if scoreboard is not None:
        # Lazy import: discovery reuses this module's writer.
        from .discovery import cross_check
        provenance = scoreboard["provenance"]
        require(provenance.get("season") in (None, bundle["season"]), "Scoreboard season differs from the bundle")
        require(provenance.get("sport") in (None, bundle["sport"]), "Scoreboard sport differs from the bundle")
        quality["scoreboard_cross_check"] = {"source_url": provenance["source_url"],
            "html_sha256": provenance["html_sha256"], "games": cross_check(bundle, scoreboard)}
    output.mkdir(parents=True, exist_ok=True)
    stem = f"ncaa_player_game_enriched_{bundle['season'].replace('-', '_')}_{bundle['sport'].lower()}"
    if bundle["provenance"] == "local_validation_preview":
        stem += "_LOCAL_PREVIEW"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for entry in result:
        writer.writerow({key: csv_safe(value) for key, value in entry["row"].items()})
    csv_text = buffer.getvalue()
    quality["csv_sha256"] = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    write_atomic(output / f"{stem}.csv", csv_text)
    write_atomic(output / f"{stem}.json", json.dumps({"provenance": bundle["provenance"], "records": result}, ensure_ascii=False, indent=2) + "\n")
    write_atomic(output / "quality_report.json", json.dumps(quality, ensure_ascii=False, indent=2) + "\n")
    return quality, output / f"{stem}.csv"
