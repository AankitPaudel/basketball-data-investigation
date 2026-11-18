"""Compare a parsed school-site box score with the validated NCAA output for the same game.

Read-only on the NCAA side: the game's _LOCAL_PREVIEW CSV and manifest.json are only
read. Rows are matched by team + jersey + normalized name (src/school/sidearm_boxscore
.name_match_key); no NCAA player ID is inferred for school rows.
"""
import csv
import io
import json
from pathlib import Path

from ..common.tables import STAT_KEYS
from ..ncaa.pipeline import STAT_COLUMNS, write_atomic
from .sidearm_boxscore import MATCH_STATUS, name_match_key

KEY_TO_COLUMN = {key: column for column, key in STAT_COLUMNS.items()}
SCHOOL_COLUMNS = ["game_id", "game_date", "team_name", "jersey_number", "player_name", "player_name_raw",
                  "player_id", "school_player_id", "minutes", *STAT_COLUMNS, "boxscore_source_url",
                  "player_match_status"]


def jersey_relation(ncaa, school):
    """'same', 'leading_zero' (5 vs 05), 'zero_vs_double_zero' (0 vs 00) or None."""
    if ncaa == school:
        return "same"
    if ncaa.isdigit() and school.isdigit() and int(ncaa) == int(school):
        return "zero_vs_double_zero" if int(ncaa) == 0 else "leading_zero"
    return None


def seconds(mm_ss):
    minutes, secs = mm_ss.split(":")
    return int(minutes) * 60 + int(secs)


def minutes_note(ncaa_minutes, school_minutes):
    total = seconds(ncaa_minutes)
    floor, exact = total // 60, total / 60
    return {"ncaa": ncaa_minutes, "school_whole_minutes": school_minutes,
            "difference_minutes": round(school_minutes - exact, 2),
            "school_equals": "round" if school_minutes == int(exact + 0.5) else
                             "floor" if school_minutes == floor else
                             "ceil" if school_minutes == floor + (1 if total % 60 else 0) else "neither"}


def stat_diff(ncaa_stats, school_stats):
    return {key: {"ncaa": ncaa_stats[key], "school": school_stats[key]}
            for key in STAT_KEYS if ncaa_stats[key] != school_stats[key]}


def load_ncaa(game_dir):
    game_dir = Path(game_dir)
    csvs = sorted(game_dir.glob("ncaa_player_game_enriched_*.csv"))
    if len(csvs) != 1:
        raise ValueError(f"Expected one NCAA player CSV in {game_dir}")
    with csvs[0].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["stats"] = {key: int(row[column]) for column, key in STAT_COLUMNS.items()}
    manifest = json.loads((game_dir / "manifest.json").read_text(encoding="utf-8"))
    game = manifest["games"][0]
    names = {team["team_id"]: team["team_name"] for team in game["teams"]}
    team_rows = {names[entry["team_id"]]: entry for entry in game["non_player_rows"]}
    return csvs[0], rows, game, team_rows


def compare(school, game_dir):
    csv_path, ncaa_rows, game, ncaa_team_rows = load_ncaa(game_dir)
    school_teams = {team["team_name"]: team for team in school["teams"]}
    ncaa_teams = {team["team_name"] for team in game["teams"]}
    if set(school_teams) != ncaa_teams:
        raise ValueError(f"Team names differ: school {sorted(school_teams)} vs NCAA {sorted(ncaa_teams)}")
    if school.get("game_date") != game["game_date"]:
        raise ValueError(f"Game date differs: school {school.get('game_date')} vs NCAA {game['game_date']}")
    used, matches, unmatched = set(), [], []
    for row in ncaa_rows:
        team = school_teams[row["team_name"]]
        key = name_match_key(row["player_name"])
        found = None
        for index, player in enumerate(team["players"]):
            relation = jersey_relation(row["jersey_number"], player["jersey_number"])
            if relation and player["name_match_key"] == key and (row["team_name"], index) not in used:
                found = (index, player, relation)
                break
        if found:
            index, player, relation = found
            used.add((row["team_name"], index))
            diff = stat_diff(row["stats"], player["stats"])
            matches.append({"team_name": row["team_name"], "ncaa_player_id": row["player_id"],
                "ncaa_name": row["player_name"], "school_name_raw": player["player_name_raw"],
                "ncaa_jersey": row["jersey_number"], "school_jersey": player["jersey_number"],
                "jersey_relation": relation, "stats_agree": not diff, "stat_differences": diff,
                "minutes": minutes_note(row["minutes"], player["minutes"])})
            continue
        same_jersey = [player for player in team["players"]
                       if jersey_relation(row["jersey_number"], player["jersey_number"])]
        same_name = [player for player in team["players"] if player["name_match_key"] == key]
        if same_jersey:
            reason, candidate = "name_differs_same_jersey", same_jersey[0]
        elif same_name:
            reason, candidate = "jersey_differs_same_name", same_name[0]
        else:
            reason, candidate = "missing_from_school_site", None
        unmatched.append({"source": "ncaa", "team_name": row["team_name"], "ncaa_player_id": row["player_id"],
            "ncaa_name": row["player_name"], "ncaa_jersey": row["jersey_number"], "reason": reason,
            "candidate": None if candidate is None else {
                "school_name_raw": candidate["player_name_raw"], "school_jersey": candidate["jersey_number"],
                "name_match_keys": [key, candidate["name_match_key"]],
                "stat_differences_if_same_player": stat_diff(row["stats"], candidate["stats"]),
                "minutes": minutes_note(row["minutes"], candidate["minutes"])}})
    candidates = {(entry["team_name"], entry["candidate"]["school_name_raw"])
                  for entry in unmatched if entry["candidate"]}
    for name, team in school_teams.items():
        for index, player in enumerate(team["players"]):
            if (name, index) not in used and (name, player["player_name_raw"]) not in candidates:
                unmatched.append({"source": "school", "team_name": name, "school_name_raw": player["player_name_raw"],
                                  "school_jersey": player["jersey_number"], "reason": "missing_from_ncaa"})
    team_comparison = []
    for name, team in school_teams.items():
        ncaa = ncaa_team_rows[name]
        team_comparison.append({"team_name": name,
            "team_row_differences": stat_diff(ncaa["team_row"]["stats"], team["team_row"]["stats"]),
            "totals_differences": stat_diff(ncaa["totals"]["stats"], team["totals"]["stats"]),
            "team_row": {"ncaa": ncaa["team_row"]["stats"], "school": team["team_row"]["stats"]},
            "totals_points": {"ncaa": ncaa["totals"]["stats"]["pts"], "school": team["totals"]["stats"]["pts"]}})
    stat_fields = len(STAT_KEYS)
    agreeing = sum(stat_fields - len(match["stat_differences"]) for match in matches)
    return {"game_id": game["game_id"], "game_date": game["game_date"],
        "ncaa_csv": csv_path.as_posix(), "school_source_url": school["source_url"],
        "school_html_sha256": school["html_sha256"],
        "match_rule": "same team + jersey number (leading zeros ignored, flagged) + name_match_key "
                      "(casefold, '.' and ',' removed, Jr./Sr./II-V removed); no fuzzy matching",
        "counts": {"ncaa_rows": len(ncaa_rows), "school_rows": sum(len(t["players"]) for t in school["teams"]),
            "matched": len(matches),
            "matched_identical_jersey_text": sum(m["jersey_relation"] == "same" for m in matches),
            "matched_leading_zero_jersey": sum(m["jersey_relation"] == "leading_zero" for m in matches),
            "matched_zero_vs_double_zero_jersey": sum(m["jersey_relation"] == "zero_vs_double_zero" for m in matches),
            "matched_all_stats_agree": sum(m["stats_agree"] for m in matches),
            "matched_stat_values_compared": stat_fields * len(matches), "matched_stat_values_agree": agreeing,
            "unmatched_ncaa": sum(u["source"] == "ncaa" for u in unmatched),
            "unmatched_school_only": sum(u["source"] == "school" for u in unmatched),
            "minutes_school_equals": {kind: sum(m["minutes"]["school_equals"] == kind for m in matches)
                                      for kind in ("round", "floor", "ceil", "neither")}},
        "school_source_checks_failed": [c for c in school["source_checks"] if c["status"] == "fail"],
        "matches": matches, "unmatched": unmatched, "teams": team_comparison}


def school_rows_csv(school, game_id):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=SCHOOL_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for team in school["teams"]:
        for player in team["players"]:
            writer.writerow({"game_id": game_id, "game_date": school["game_date"], "team_name": team["team_name"],
                **{key: player[key] for key in ("jersey_number", "player_name", "player_name_raw", "player_id",
                                                "school_player_id", "minutes")},
                **{column: player["stats"][key] for column, key in STAT_COLUMNS.items()},
                "boxscore_source_url": school["source_url"], "player_match_status": MATCH_STATUS})
    return buffer.getvalue()


def summary_markdown(report):
    c = report["counts"]
    lines = [f"# School site vs. NCAA: game {report['game_id']} ({report['game_date']})", "",
        f"- School source: {report['school_source_url']} (SHA-256 `{report['school_html_sha256'][:12]}…`)",
        f"- NCAA source: `{report['ncaa_csv']}`", f"- Match rule: {report['match_rule']}", "",
        "## Counts", "", "| | Value |", "|---|---|",
        f"| NCAA rows | {c['ncaa_rows']} |", f"| School-site rows | {c['school_rows']} |",
        f"| Matched | {c['matched']} of {c['ncaa_rows']} |",
        f"| ... jersey text identical | {c['matched_identical_jersey_text']} |",
        f"| ... jersey differs only by a leading zero (5 vs 05) | {c['matched_leading_zero_jersey']} |",
        f"| ... jersey 0 vs 00 | {c['matched_zero_vs_double_zero_jersey']} |",
        f"| Matched rows with all 15 stats equal | {c['matched_all_stats_agree']} of {c['matched']} |",
        f"| Stat values equal | {c['matched_stat_values_agree']} of {c['matched_stat_values_compared']} |",
        f"| Unmatched NCAA rows | {c['unmatched_ncaa']} |", f"| School-only rows | {c['unmatched_school_only']} |",
        f"| School minutes = NCAA rounded / floor / ceil / neither | {c['minutes_school_equals']['round']} / "
        f"{c['minutes_school_equals']['floor']} / {c['minutes_school_equals']['ceil']} / "
        f"{c['minutes_school_equals']['neither']} |", "", "## Unmatched", ""]
    if not report["unmatched"]:
        lines.append("None.")
    for entry in report["unmatched"]:
        if entry["source"] == "ncaa":
            candidate = entry["candidate"]
            extra = "" if not candidate else (f"; school row #{candidate['school_jersey']} "
                f"'{candidate['school_name_raw']}', stats if same player: "
                f"{'all equal' if not candidate['stat_differences_if_same_player'] else candidate['stat_differences_if_same_player']}")
            lines.append(f"- {entry['team_name']} #{entry['ncaa_jersey']} '{entry['ncaa_name']}' (NCAA): "
                         f"{entry['reason']}{extra}")
        else:
            lines.append(f"- {entry['team_name']} #{entry['school_jersey']} '{entry['school_name_raw']}' (school): "
                         f"{entry['reason']}")
    lines += ["", "## Stat differences in matched rows", ""]
    diffs = [m for m in report["matches"] if m["stat_differences"]]
    lines += [f"- {m['team_name']} '{m['ncaa_name']}': {m['stat_differences']}" for m in diffs] or ["None."]
    lines += ["", "## TEAM rows and totals", ""]
    for team in report["teams"]:
        lines.append(f"- {team['team_name']}: TEAM row "
                     f"{'identical' if not team['team_row_differences'] else team['team_row_differences']}; totals "
                     f"{'identical' if not team['totals_differences'] else team['totals_differences']} "
                     f"(points NCAA {team['totals_points']['ncaa']}, school {team['totals_points']['school']})")
    lines += ["", "Minutes: the school site shows whole minutes, NCAA shows MM:SS, so minutes are "
              "reported but not counted as a stat difference.", ""]
    return "\n".join(lines)


def write_comparison(school, report, output):
    output = Path(output)
    if "exports" in output.parts:
        raise ValueError("Comparison output never goes to data/exports")
    output.mkdir(parents=True, exist_ok=True)
    write_atomic(output / "school_boxscore_parsed.json", json.dumps(school, ensure_ascii=False, indent=2) + "\n")
    write_atomic(output / "school_player_rows.csv", school_rows_csv(school, report["game_id"]))
    write_atomic(output / "comparison.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_atomic(output / "comparison_summary.md", summary_markdown(report))
    return output
