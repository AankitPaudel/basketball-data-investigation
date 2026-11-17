from datetime import date
import re


def validate(records, kind="boxscore"):
    seen = set()
    for record in records:
        issues = record.validation
        identity = (record.source, record.game_id, record.team,
                    record.player_id or record.player.casefold())
        if identity in seen:
            issues.append("duplicate_player")
        seen.add(identity)
        if record.game_date:
            try:
                date.fromisoformat(record.game_date)
            except ValueError:
                issues.append("invalid_game_date")
        elif kind == "boxscore":
            issues.append("missing_game_date")
        if kind != "boxscore":
            continue
        stats = record.stats
        if record.minutes and record.minutes.lower() not in {"dnp", "did not play", "did not dress", "-", "--", "—"}:
            if not re.fullmatch(r"\d+(?::[0-5]\d)?", record.minutes):
                issues.append("invalid_minutes")
        for key in ("pts", "reb", "ast"):
            if stats.get(key) is None and record.participation != "did_not_play":
                issues.append(f"missing_stat:{key}")
        for key, value in stats.items():
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                issues.append(f"invalid_stat:{key}")
        for made, attempted in (("fgm", "fga"), ("fg3m", "fg3a"), ("ftm", "fta")):
            if all(stats.get(key) is not None for key in (made, attempted)) and stats[made] > stats[attempted]:
                issues.append(f"made_exceeds_attempted:{made}")
        if all(stats.get(key) is not None for key in ("fgm", "fg3m")) and stats["fg3m"] > stats["fgm"]:
            issues.append("three_pointers_exceed_field_goals")
        if all(stats.get(key) is not None for key in ("fga", "fg3a")) and stats["fg3a"] > stats["fga"]:
            issues.append("three_point_attempts_exceed_field_goal_attempts")
        if all(stats.get(key) is not None for key in ("pts", "fgm", "fg3m", "ftm")):
            if stats["pts"] != 2 * stats["fgm"] + stats["fg3m"] + stats["ftm"]:
                issues.append("points_do_not_reconcile")
        if all(stats.get(key) is not None for key in ("reb", "oreb", "dreb")):
            if stats["reb"] != stats["oreb"] + stats["dreb"]:
                issues.append("rebounds_do_not_reconcile")
        if record.participation == "did_not_play" and any(value for value in stats.values()):
            issues.append("dnp_has_nonzero_stats")
        record.validation = list(dict.fromkeys(issues))
    return records
