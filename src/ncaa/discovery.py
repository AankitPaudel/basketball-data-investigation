"""Turn a parsed scoreboard into pipeline inputs and cross-checks.

Discovered games carry game-level facts only (IDs, teams, final scores, box-score
URLs). Player rows must still come from box scores; nothing here supplies them.
"""
import csv
import io
import json
from pathlib import Path

from ..common.export import csv_safe
from .pipeline import write_atomic

GAME_COLUMNS = ["contest_id", "game_date", "start_time_local", "status", "status_text",
    "status_source", "completed", "boxscore_url",
    "team1_name", "team1_id", "team1_url", "team1_score",
    "team2_name", "team2_id", "team2_url", "team2_score",
    "attendance", "linescore_check", "issues", "scoreboard_source_url", "scoreboard_html_sha256",
    "player_statistics_verified"]


def game_row(game, provenance):
    row = {key: game[key] for key in GAME_COLUMNS if key in game}
    for team in game["teams"]:
        prefix = f"team{team['listed_order']}_"
        row.update({prefix + "name": team["team_name"], prefix + "id": team["team_id"],
                    prefix + "url": team["team_url"], prefix + "score": team["final_score"]})
    row["issues"] = "|".join(game["issues"])
    row["scoreboard_source_url"] = provenance["source_url"]
    row["scoreboard_html_sha256"] = provenance["html_sha256"]
    return row


def bundle_games(discovery):
    """Completed games in the offline input contract, still lacking player records."""
    provenance = discovery["provenance"]
    games = []
    for game in discovery["games"]:
        if not game["completed"]:
            continue
        games.append({"game_id": game["contest_id"], "status": "final",
            "game_date": game["game_date"], "boxscore_source_url": game["boxscore_url"],
            # The scoreboard's division is the listing filter, not each team's division.
            "teams": [{"team_name": team["team_name"], "team_id": team["team_id"],
                       "division": None, "final_score": team["final_score"]} for team in game["teams"]],
            "scoreboard_source_url": provenance["source_url"],
            "scoreboard_html_sha256": provenance["html_sha256"],
            "player_statistics": "pending_boxscore"})
    return games


def bundle_skeleton(discovery):
    provenance = discovery["provenance"]
    return {"schema_version": 1, "season": provenance["season"],
        "ncaa_season_id": provenance["ncaa_season_id"], "sport": provenance["sport"],
        "provenance": "authorized_source", "permission_reference": None,
        "verification_reference": None, "extracted_at": provenance["parsed_at"],
        "games": bundle_games(discovery), "rosters": [], "conferences": []}


def write_discovery(discovery, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=GAME_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for game in discovery["games"]:
        writer.writerow({key: csv_safe(value) for key, value in game_row(game, discovery["provenance"]).items()})
    paths = {"json": output / "scoreboard_games.json", "csv": output / "scoreboard_games.csv",
             "bundle": output / "ncaa_bundle.discovered.json"}
    write_atomic(paths["json"], json.dumps(discovery, ensure_ascii=False, indent=2) + "\n")
    write_atomic(paths["csv"], buffer.getvalue())
    write_atomic(paths["bundle"], json.dumps(bundle_skeleton(discovery), ensure_ascii=False, indent=2) + "\n")
    return paths


def cross_check(bundle, discovery):
    """Compare bundle games with scoreboard evidence; mismatches are reported, not fixed."""
    discovered = {game["contest_id"]: game for game in discovery["games"]}
    excluded = {entry["contest_id"] for entry in discovery["excluded"]}
    checks = []
    for game in bundle["games"]:
        gid = str(game.get("game_id"))
        found = discovered.get(gid)
        if found is None:
            checks.append({"game_id": gid, "status": "excluded_on_scoreboard" if gid in excluded
                           else "not_on_scoreboard", "differences": []})
            continue
        differences = []
        if game.get("game_date") != found["game_date"]:
            differences.append("game_date")
        if not found["completed"]:
            differences.append("scoreboard_not_completed")
        listed = {team["team_id"] or team["team_name"]: team for team in found["teams"]}
        for team in game.get("teams", []):
            key = team.get("team_id") or team.get("team_name")
            source = listed.get(key)
            if source is None:
                differences.append(f"team_not_on_scoreboard:{key}")
                continue
            if team.get("team_name") != source["team_name"]:
                differences.append(f"team_name:{key}")
            if team.get("final_score") is not None and team["final_score"] != source["final_score"]:
                differences.append(f"final_score:{key}")
        checks.append({"game_id": gid, "status": "mismatch" if differences else "match",
                       "differences": differences})
    return checks
