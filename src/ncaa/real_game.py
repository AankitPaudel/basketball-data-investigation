"""Assemble one real NCAA game from saved pages into the offline input contract.

Inputs are operator-saved stats.ncaa.org pages: the livestream scoreboard (Final
status and scores), ``/contests/{id}/individual_stats`` (player rows), optionally
``/contests/{id}/box_score`` (Team Stats panel) and ``/teams/{id}/roster`` pages.
Every cross-page comparison is recorded in ``source_checks``; nothing is corrected.
Division and conference are left null: none of these pages states either explicitly.
"""
from ..common.models import utc_now
from ..common.tables import STAT_KEYS
from .boxscore_parser import parse_individual_stats, parse_team_stats
from .roster_parser import parse_ncaa_roster
from .scoreboard_parser import parse_scoreboard

TEAM_STAT_LABELS = {"Assists": "ast", "Rebounds": "reb", "Turnovers": "tov"}
PERCENT_LABELS = {"Field Goal %": ("fgm", "fga"), "3 PT Field Goal %": ("fg3m", "fg3a"),
                  "FT %": ("ftm", "fta")}


def seconds(value):
    minutes, _, secs = (value or "").partition(":")
    return int(minutes) * 60 + int(secs or 0) if minutes.isdigit() else None


def check(name, observed, expected, **detail):
    return {"check": name, "observed": observed, "expected": expected,
            "status": "pass" if observed == expected else "fail", **detail}


def team_checks(team, scoreboard_team, team_stats):
    players = team["players"]
    totals, team_row = team["totals"]["stats"], (team["team_row"] or {"stats": {}})["stats"]
    results = []
    player_points = sum(player["stats"]["pts"] or 0 for player in players)
    results.append(check("player_points_vs_header_score", player_points, team["final_score"]))
    results.append(check("player_points_vs_totals_row", player_points, totals["pts"]))
    if scoreboard_team is not None:
        results.append(check("player_points_vs_scoreboard_score", player_points, scoreboard_team["final_score"]))
        results.append(check("team_id_vs_scoreboard", team["team_id"], scoreboard_team["team_id"]))
    for key in STAT_KEYS:
        observed = sum(player["stats"][key] or 0 for player in players) + (team_row.get(key) or 0)
        results.append(check(f"players_plus_team_row_vs_totals:{key}", observed, totals[key]))
    results.append(check("totals_points_from_shooting", 2 * totals["fgm"] + totals["fg3m"] + totals["ftm"], totals["pts"]))
    results.append(check("totals_rebounds_oreb_plus_dreb", totals["oreb"] + totals["dreb"], totals["reb"]))
    played = [seconds(player["minutes"]) for player in players if player["minutes"]]
    results.append(check("player_minutes_vs_totals_row", sum(value or 0 for value in played),
                         seconds(team["totals"]["minutes"])))
    if team_stats:
        for label, key in TEAM_STAT_LABELS.items():
            if label in team_stats:
                results.append(check(f"totals_vs_box_score_team_stats:{label}", totals[key],
                                     team_stats[label].get(team["team_name"])))
        for label, (made, attempted) in PERCENT_LABELS.items():
            if label in team_stats and totals[attempted]:
                results.append(check(f"totals_vs_box_score_team_stats:{label}",
                                     round(100 * totals[made] / totals[attempted], 2),
                                     team_stats[label].get(team["team_name"])))
    return results


def build_bundle(*, scoreboard, individual_stats, box_score=None, rosters=(),
                 permission_reference=None, verification_reference=None, extracted_at=None):
    """Each argument is a (bytes, source_url) pair; rosters is a sequence of pairs."""
    extracted_at = extracted_at or utc_now()
    board = parse_scoreboard(scoreboard[0], source_url=scoreboard[1], parsed_at=extracted_at)
    game = parse_individual_stats(individual_stats[0], source_url=individual_stats[1], parsed_at=extracted_at)
    listed = {entry["contest_id"]: entry for entry in board["games"]}.get(game["contest_id"])
    if listed is None:
        raise ValueError(f"Contest {game['contest_id']} is not a listed (non-excluded) game on the scoreboard")
    if not listed["completed"]:
        raise ValueError(f"Scoreboard does not show contest {game['contest_id']} as a completed Final game")
    panel = parse_team_stats(box_score[0], source_url=box_score[1]) if box_score else None
    if panel and panel["contest_id"] != game["contest_id"]:
        raise ValueError("Box-score page is for a different contest")
    parsed_rosters = [parse_ncaa_roster(html, source_url=url, parsed_at=extracted_at) for html, url in rosters]
    meta = board["provenance"]
    for roster in parsed_rosters:
        if (roster["season"], roster["sport"], roster["ncaa_season_id"]) != (meta["season"], meta["sport"], meta["ncaa_season_id"]):
            raise ValueError(f"Roster {roster['team_id']} season/sport differs from the scoreboard")
        if roster["team_id"] not in {team["team_id"] for team in game["teams"]}:
            raise ValueError(f"Roster team {roster['team_id']} did not play in contest {game['contest_id']}")
    board_teams = {team["team_id"]: team for team in listed["teams"] if team["team_id"]}
    checks = [check("game_date_vs_scoreboard", game["game_date"], listed["game_date"])]
    for team in game["teams"]:
        checks += [dict(entry, team_id=team["team_id"], team_name=team["team_name"])
                   for entry in team_checks(team, board_teams.get(team["team_id"]),
                                            panel["stats"] if panel else None)]
    sources = [{"kind": "scoreboard", "source_url": meta["source_url"], "sha256": meta["html_sha256"]},
               {"kind": "individual_stats", "source_url": game["provenance"]["source_url"],
                "sha256": game["provenance"]["html_sha256"]}]
    if panel:
        sources.append({"kind": "box_score", "source_url": panel["source_url"], "sha256": panel["html_sha256"]})
    sources += [{"kind": "roster", "team_id": roster["team_id"], "source_url": roster["source_url"],
                 "sha256": roster["provenance"]["html_sha256"]} for roster in parsed_rosters]
    authorized = bool(permission_reference)
    return {"schema_version": 1, "season": meta["season"], "ncaa_season_id": meta["ncaa_season_id"],
        "sport": meta["sport"],
        # Without an operator permission reference this is a local validation preview only.
        "provenance": "authorized_source" if authorized else "local_validation_preview",
        "permission_reference": permission_reference, "verification_reference": verification_reference,
        "extracted_at": extracted_at, "source_files": sources,
        "games": [{"game_id": game["contest_id"], "status": "final", "game_date": game["game_date"],
            "boxscore_source_url": game["provenance"]["source_url"],
            "teams": [{"team_name": team["team_name"], "team_id": team["team_id"], "division": None,
                       "final_score": team["final_score"]} for team in game["teams"]],
            "players": [player for team in game["teams"] for player in team["players"]],
            "non_player_rows": [{"team_id": team["team_id"], "team_row": team["team_row"],
                                 "totals": team["totals"]} for team in game["teams"]],
            "source_checks": checks}],
        "rosters": [{key: value for key, value in roster.items() if key != "provenance"}
                    for roster in parsed_rosters],
        "conferences": []}
