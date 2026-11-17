"""Season-specific, source-backed team affiliations (division and conference).

A mapping entry is used only when its season, sport and team ID match the game team
exactly, its name matches, and every evidence item agrees with it. Conflicts stop
processing instead of picking a value. Nothing here infers division from a scoreboard
filter or URL parameter; entries must cite an NCAA Directory page.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .enrichment import clean, name_key

EVIDENCE_KINDS = {"ncaa_directory", "conference_official", "institution_official"}
DIVISIONS = {"I", "II", "III"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def academic_year_matches(label, season):
    """'2026-2027' or '2026-27' both name season '2026-27'."""
    match = re.fullmatch(r"(\d{4})-(\d{2}|\d{4})", label or "")
    return bool(match) and f"{match.group(1)}-{match.group(2)[-2:]}" == season


def check_entry(entry):
    key = f"{entry.get('season')}/{entry.get('sport')}/{entry.get('team_id')}"
    require(bool(re.fullmatch(r"\d{4}-\d{2}", entry.get("season") or "")), f"{key}: season must be YYYY-YY")
    require(entry.get("sport") in {"MBB", "WBB"}, f"{key}: sport must be MBB or WBB")
    require(bool(clean(entry.get("team_id"))) and bool(clean(entry.get("team_name"))), f"{key}: team_id and team_name required")
    # "division" is the sport-specific division written to the CSV; the institution's
    # membership division is recorded separately because the two can differ.
    require(entry.get("division") in DIVISIONS, f"{key}: sport division must be I, II or III")
    require(entry.get("institution_division") in DIVISIONS, f"{key}: institution_division must be I, II or III")
    require(bool(clean(entry.get("institution_status"))), f"{key}: institution_status required (e.g. Active)")
    require(isinstance(entry.get("reclassifying"), bool), f"{key}: reclassifying must be true or false")
    if entry["reclassifying"]:
        require(entry.get("reclass_division") in DIVISIONS, f"{key}: a reclassifying member needs reclass_division")
        require(entry["reclass_division"] != entry["institution_division"],
                f"{key}: reclass_division must differ from institution_division")
        require(entry["division"] in (entry["institution_division"], entry["reclass_division"]),
                f"{key}: sport division must be the institution or reclass division")
    else:
        require(entry.get("reclass_division") is None, f"{key}: reclass_division requires reclassifying: true")
        # e.g. a Division III school sponsoring one Division I sport must say so explicitly.
        require(entry["division"] == entry["institution_division"] or bool(clean(entry.get("sport_division_note"))),
                f"{key}: sport division differs from institution division without a sport_division_note")
    require(bool(clean(entry.get("conference"))), f"{key}: conference required")
    aliases = entry.get("conference_aliases", [])
    require(isinstance(aliases, list) and all(bool(clean(alias)) for alias in aliases),
            f"{key}: conference_aliases must be a list of names")
    evidence = entry.get("evidence")
    require(isinstance(evidence, list) and bool(evidence), f"{key}: evidence required")
    directory = [item for item in evidence if item.get("kind") == "ncaa_directory"]
    require(bool(directory), f"{key}: an NCAA Directory evidence item is required")
    for item in evidence:
        require(item.get("kind") in EVIDENCE_KINDS, f"{key}: unknown evidence kind {item.get('kind')!r}")
        url = urlsplit(item.get("url") or "")
        require(url.scheme == "https" and bool(url.hostname), f"{key}: evidence URL must be HTTPS")
        if item["kind"] == "ncaa_directory":
            require(url.hostname == "web3.ncaa.org" and url.path == "/directory/orgDetail",
                    f"{key}: NCAA Directory evidence must be a web3.ncaa.org orgDetail page")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", item.get("sha256") or "")), f"{key}: evidence sha256 required")
        require(bool(clean(item.get("retrieved_at"))) and bool(clean(item.get("quote"))),
                f"{key}: evidence needs retrieved_at and quote")
        require(academic_year_matches(item.get("academic_year"), entry["season"]),
                f"{key}: evidence academic year {item.get('academic_year')!r} is not season {entry['season']}")
        observed = item.get("observed") or {}
        require(bool(observed), f"{key}: evidence must state what it observed")
        for field in ("division", "conference", "institution_division", "reclass_division"):
            if field in observed:
                # Official pages may print an abbreviation (e.g. "NESCAC"); only declared aliases count.
                accepted = {entry.get(field), *(aliases if field == "conference" else ())}
                require(observed[field] in accepted,
                        f"{key}: conflicting {field}: evidence {item['url']} shows {observed[field]!r}, entry has {entry.get(field)!r}")
    needed = {"division", "conference", "institution_division"} | ({"reclass_division"} if entry["reclassifying"] else set())
    for item in directory:
        require(needed <= set(item.get("observed", {})),
                f"{key}: NCAA Directory evidence must observe {', '.join(sorted(needed))}")
        # The directory is the naming authority: its conference must be the canonical name, not an alias.
        require(item["observed"]["conference"] == entry["conference"],
                f"{key}: NCAA Directory conference must equal the entry's conference name")


def load_affiliations(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    require(isinstance(data, dict) and data.get("schema_version") == 1, "Affiliations file needs schema_version 1")
    entries = data.get("affiliations")
    require(isinstance(entries, list), "affiliations must be a list")
    by_key = {}
    for entry in entries:
        check_entry(entry)
        key = (entry["season"], entry["sport"], clean(entry["team_id"]))
        previous = by_key.get(key)
        if previous is not None:
            require((previous["division"], previous["conference"]) == (entry["division"], entry["conference"]),
                    f"Conflicting affiliation entries for {'/'.join(key)}")
            previous["evidence"] = previous["evidence"] + entry["evidence"]
            continue
        by_key[key] = dict(entry)
    return {"source_file": str(path), "entries": by_key}


def apply_affiliations(bundle, affiliations):
    """Set division and add conference records in place; returns the entries applied."""
    season, sport = bundle["season"], bundle["sport"]
    conferences = bundle.setdefault("conferences", [])
    applied = {}
    for game in bundle["games"]:
        for team in game["teams"]:
            entry = affiliations["entries"].get((season, sport, clean(team.get("team_id"))))
            if entry is None:
                continue
            require(name_key(entry["team_name"]) == name_key(team.get("team_name")),
                    f"Affiliation for team {team['team_id']} names {entry['team_name']!r}, game has {team.get('team_name')!r}")
            require(team.get("division") in (None, entry["division"]),
                    f"Conflicting division for team {team['team_id']}: bundle {team.get('division')!r}, evidence {entry['division']!r}")
            team["division"] = entry["division"]
            team["division_reclassifying"] = bool(entry.get("reclassifying"))
            source = next(item["url"] for item in entry["evidence"] if item["kind"] == "ncaa_directory")
            existing = [record for record in conferences if record.get("season") == season and record.get("sport") == sport
                        and clean(record.get("team_id")) == clean(team["team_id"])]
            for record in existing:
                require(record.get("conference") == entry["conference"],
                        f"Conflicting conference for team {team['team_id']}: bundle {record.get('conference')!r}, evidence {entry['conference']!r}")
            if not existing:
                conferences.append({"season": season, "sport": sport, "team_id": team["team_id"],
                                    "team_name": team["team_name"], "conference": entry["conference"],
                                    "source_url": source})
            applied[team["team_id"]] = entry
    return list(applied.values())
