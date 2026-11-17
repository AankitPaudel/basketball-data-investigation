"""Source-independent enrichment; no live NCAA layout assumptions."""
import re
import unicodedata


def clean(value):
    if value is None:
        return None
    value = " ".join(unicodedata.normalize("NFC", str(value)).split())
    return None if value.casefold() in {"", "-", "--", "—", "n/a", "unknown"} else value


def name_key(value):
    # Deliberately retain punctuation/diacritics/suffixes; no fuzzy matching.
    return (clean(value) or "").casefold()


def normalize_height(value):
    """Return centimeters and an issue; ambiguous unitless values stay unknown."""
    raw = clean(value)
    if raw is None:
        return None, None
    text = raw.lower().replace("′", "'").replace("″", '"')
    metric = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(cm|m)", text)
    imperial = re.fullmatch(r"(\d)\s*(?:-|')\s*(\d{1,2})\s*\"?", text)
    if metric:
        height = float(metric[1]) * (100 if metric[2] == "m" else 1)
    elif imperial and int(imperial[2]) < 12:
        height = (12 * int(imperial[1]) + int(imperial[2])) * 2.54
    else:
        return None, "unrecognized_height"
    if not 100 <= height <= 260:
        return None, "height_out_of_range"
    return round(height, 2), None


def normalize_class(value):
    value = clean(value)
    mapping = {"fr": "Freshman", "freshman": "Freshman", "so": "Sophomore",
               "sophomore": "Sophomore", "jr": "Junior", "junior": "Junior",
               "sr": "Senior", "senior": "Senior", "gr": "Graduate", "graduate": "Graduate"}
    return mapping.get(value.lower().rstrip("."), value) if value else None


def normalize_position(value):
    value = clean(value)
    mapping = {"guard": "G", "forward": "F", "center": "C"}
    return mapping.get(value.lower(), value.upper()) if value else None


def same_team(a, b):
    aid, bid = clean(a.get("team_id")), clean(b.get("team_id"))
    if aid and bid:
        return aid == bid
    # If only one side has an ID, a name does not establish the missing identity.
    if aid or bid:
        return False
    return bool(name_key(a.get("team_name"))) and name_key(a.get("team_name")) == name_key(b.get("team_name"))


def matching_rosters(rosters, team, season, sport):
    return [roster for roster in rosters if roster.get("season") == season
            and roster.get("sport") == sport and same_team(roster, team)]


def match_player(player, rosters):
    candidates = [(entry, roster) for roster in rosters for entry in roster.get("players", [])]
    source_id = clean(player.get("player_id"))
    if source_id:
        exact = [(entry, roster) for entry, roster in candidates if clean(entry.get("player_id")) == source_id]
        if len(exact) == 1:
            return "matched_id", *exact[0]
        if len(exact) > 1:
            return "ambiguous_id", None, None
        # Do not fall back to a conflicting roster ID because the names match.
        candidates = [(entry, roster) for entry, roster in candidates if not clean(entry.get("player_id"))]
    matches = [(entry, roster) for entry, roster in candidates
               if name_key(entry.get("player_name")) == name_key(player.get("player_name"))
               and name_key(player.get("player_name"))]
    if len(matches) == 1:
        entry, roster = matches[0]
        jersey, other = clean(player.get("jersey_number")), clean(entry.get("jersey_number"))
        if jersey and other and jersey != other:
            return "jersey_conflict", None, None
        return "matched_name", entry, roster
    return ("ambiguous_name" if len(matches) > 1 else "unmatched"), None, None


def match_conference(conferences, team, season, sport):
    matches = [entry for entry in conferences if entry.get("season") == season
               and entry.get("sport") == sport and same_team(entry, team)]
    if len(matches) == 1:
        return matches[0], None
    return None, "ambiguous_conference" if matches else "missing_conference"
