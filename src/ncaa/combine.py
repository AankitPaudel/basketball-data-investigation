"""Merge the per-game player CSVs listed in a games plan into one CSV, with a column dictionary.

Offline only. Each source CSV is checked against the content hash in its own
quality_report.json before it is used, so an edited or stale file is refused.
"""
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

from ..common.models import utc_now
from .pipeline import COLUMNS, write_atomic

# (column, type, format/unit, meaning and source)
COLUMN_DICTIONARY = [
    ("season", "text", "YYYY-YY", "Season label of the game bundle, e.g. 2026-27."),
    ("sport", "text", "code", "MBB = men's basketball."),
    ("division", "text", "I / II / III",
     "Sport-specific NCAA division of the player's team, from the NCAA Directory sport row via "
     "config/ncaa_team_affiliations.json. Can differ from the institution's division (Ferrum: institution III, "
     "men's basketball II while reclassifying). Never taken from the scoreboard filter."),
    ("game_id", "text", "integer ID", "stats.ncaa.org contest ID, as in /contests/{game_id}/individual_stats."),
    ("game_date", "date", "YYYY-MM-DD", "Game date from the contest page header, checked against the scoreboard."),
    ("team_id", "text", "integer ID",
     "stats.ncaa.org team-season ID of the player's team, as in /teams/{team_id}/roster. A new ID each season."),
    ("team_name", "text", "", "Team name as shown on the contest page."),
    ("opponent_team_id", "text", "integer ID", "stats.ncaa.org team-season ID of the opponent."),
    ("opponent_name", "text", "", "Opponent name as shown on the contest page."),
    ("conference", "text", "",
     "Conference of the player's team for this season, from config/ncaa_team_affiliations.json. Blank if no "
     "source-backed entry exists."),
    ("player_id", "text", "integer ID",
     "stats.ncaa.org player ID from the /players/{id} link on the individual-stats page; used to match the roster."),
    ("player_name", "text", "", "Player name as shown on the individual-stats page."),
    ("jersey_number", "text", "", "Jersey number from the individual-stats page (roster value if missing)."),
    ("position", "text", "G / F / C", "Roster position, normalized; blank when the roster leaves it empty."),
    ("height", "number", "centimeters", "Roster height converted from feet-inches, 2 decimals; blank if not listed."),
    ("class_year", "text", "Freshman / Sophomore / Junior / Senior / Graduate", "Roster class, normalized."),
    ("hometown", "text", "", "Roster hometown as listed."),
    ("minutes", "text", "MM:SS", "Minutes played, as shown on the individual-stats page."),
    ("fgm", "integer", "count", "Field goals made."),
    ("fga", "integer", "count", "Field goals attempted."),
    ("three_pm", "integer", "count", "Three-point field goals made."),
    ("three_pa", "integer", "count", "Three-point field goals attempted."),
    ("ftm", "integer", "count", "Free throws made."),
    ("fta", "integer", "count", "Free throws attempted."),
    ("points", "integer", "count", "Points scored."),
    ("offensive_rebounds", "integer", "count", "Offensive rebounds."),
    ("defensive_rebounds", "integer", "count", "Defensive rebounds."),
    ("total_rebounds", "integer", "count", "Total rebounds."),
    ("assists", "integer", "count", "Assists."),
    ("steals", "integer", "count", "Steals."),
    ("blocks", "integer", "count", "Blocked shots."),
    ("turnovers", "integer", "count", "Turnovers."),
    ("fouls", "integer", "count", "Personal fouls."),
    ("boxscore_source_url", "url", "", "stats.ncaa.org individual-stats page the statistics were read from."),
    ("roster_source_url", "url", "", "stats.ncaa.org roster page the biodata were read from."),
    ("conference_source_url", "url", "", "NCAA Directory orgDetail page cited for division and conference."),
    ("extracted_at", "timestamp", "ISO 8601, UTC", "When the game was built from the saved pages."),
    ("player_match_status", "text", "matched_id / matched_name / ambiguous_id / ambiguous_name / jersey_conflict / unmatched",
     "How the box-score player was matched to the roster. matched_id = same NCAA player ID."),
    ("validation_status", "text", "PASS / WARNING / FAIL",
     "Row-level result. WARNING reasons (e.g. division_reclassifying, missing_position, missing_height) are listed "
     "per row in the game's JSON output and counted in its quality_report.json."),
]
DICTIONARY_FIELDS = ["column", "type", "format", "description"]
STEM = "ncaa_player_game_enriched_combined"


def lf_sha256(path):
    """SHA-256 of a file's text with CRLF normalized to LF (outputs written before the LF fix are CRLF on Windows)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def game_source(game):
    folder = Path(game["output"])
    csvs = sorted(folder.glob("ncaa_player_game_enriched_*.csv"))
    if len(csvs) != 1:
        raise ValueError(f"Game {game.get('game_id')}: expected one player CSV in {folder}, found {len(csvs)}")
    quality = json.loads((folder / "quality_report.json").read_text(encoding="utf-8"))
    if lf_sha256(csvs[0]) != quality.get("csv_sha256"):
        raise ValueError(f"Game {game.get('game_id')}: {csvs[0].name} does not match its quality_report.json hash")
    return csvs[0], quality


def combine_games(plan_path, output, only=None):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8-sig"))
    if plan.get("schema_version") != 1 or not isinstance(plan.get("games"), list):
        raise ValueError("Plan needs schema_version 1 and a games list")
    output = Path(output)
    rows, sources, seen = [], [], set()
    for game in plan["games"]:
        if only and str(game.get("game_id")) not in only:
            continue
        path, quality = game_source(game)
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != COLUMNS:
                raise ValueError(f"{path}: columns differ from the 39-column contract")
            game_rows = list(reader)
        for row in game_rows:
            if row["game_id"] != str(game["game_id"]):
                raise ValueError(f"{path}: row game_id {row['game_id']} differs from plan game {game['game_id']}")
            key = (row["game_id"], row["team_id"], row["player_id"] or row["player_name"])
            if key in seen:
                raise ValueError(f"Duplicate player-game row {key}")
            seen.add(key)
        rows.extend(game_rows)
        sources.append({"game_id": str(game["game_id"]), "csv": path.as_posix(), "csv_sha256": quality["csv_sha256"],
                        "rows": len(game_rows), "provenance": quality["provenance"]})
    if not sources:
        raise ValueError("No plan games selected")
    provenances = sorted({source["provenance"] for source in sources})
    authorized = provenances == ["authorized_source"]
    if "exports" in output.parts and not authorized:
        raise ValueError("data/exports accepts only authorized_source games; this combination is a preview")
    stem = STEM if authorized else STEM + "_LOCAL_PREVIEW"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = buffer.getvalue().encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / f"{stem}.csv"
    csv_path.write_bytes(csv_bytes)  # LF on every platform, so the file hash is the content hash.
    dictionary = io.StringIO(newline="")
    writer = csv.writer(dictionary, lineterminator="\n")
    writer.writerow(DICTIONARY_FIELDS)
    writer.writerows(COLUMN_DICTIONARY)
    (output / "column_dictionary.csv").write_bytes(dictionary.getvalue().encode("utf-8"))
    report = {"generated_at": utc_now(), "plan": Path(plan_path).as_posix(), "csv": csv_path.as_posix(),
        "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(), "rows": len(rows), "games": len(sources),
        "provenance": provenances[0] if len(provenances) == 1 else provenances,
        "rows_by_division": dict(sorted(Counter(row["division"] for row in rows).items())),
        "validation_counts": dict(sorted(Counter(row["validation_status"] for row in rows).items())),
        "sources": sources}
    write_atomic(output / "combine_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
