"""Adapter for a Sidearm Sports school-site basketball box score page.

Built from a real saved page: athletics.bowdoin.edu
``/sports/mens-basketball/stats/2026-27/university-of-chicago/boxscore/14335``
(Bowdoin 63 at UChicago 87, 2026-11-14), fetched 2026-11-18 under robots.txt.
Observed layout: one ``table.sidearm-table.overall-stats`` per team, captioned
"{team} {score}", with a fixed header (below), one row per player, a ``TM TEAM`` row in
``<tbody>`` and a ``Totals`` row in ``<tfoot>``.

The page carries no NCAA player ID. Rows keep the school site's own roster ID when a
player link exists (only the home site's own players have one) as
``school_player_id``; ``player_id`` is always null, so nothing downstream can mistake
it for an NCAA ID. Minutes are whole minutes, coarser than NCAA's MM:SS.
"""
import hashlib
import re
from datetime import date

from bs4 import BeautifulSoup

from ..common.http_client import page_blocker
from ..common.models import utc_now
from ..common.tables import ParseError, STAT_KEYS

PARSER = "sidearm_basketball_boxscore_v1"
# Exact header sequence observed on the real page; any drift stops parsing.
HEADERS = ["##", "Player", "GS", "MIN", "FG", "3PT", "FT", "ORB-DRB", "REB", "PF", "A", "TO", "BLK", "STL", "PTS"]
PAIRS = {"FG": ("fgm", "fga"), "3PT": ("fg3m", "fg3a"), "FT": ("ftm", "fta"), "ORB-DRB": ("oreb", "dreb")}
COUNTS = {"REB": "reb", "PF": "pf", "A": "ast", "TO": "tov", "BLK": "blk", "STL": "stl", "PTS": "pts"}
SUFFIXES = {"jr": "Jr.", "sr": "Sr.", "ii": "II", "iii": "III", "iv": "IV", "v": "V"}
MATCH_STATUS = "school_site_no_ncaa_id"
CAPTION = re.compile(r"^(?P<team>.+?)\s+(?P<score>\d+)$")
PAIR = re.compile(r"^(\d+)-(\d+)$")
COUNT = re.compile(r"^\d+$")
ROSTER_LINK = re.compile(r"/roster/[^/]+/(\d+)$")


def text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def normalize_name(raw):
    """'Last, First' / 'Last,First' / 'Last, Jr., First' -> ('First Last [Suffix]', issue or None).

    A suffix is recognised only as the middle of exactly three comma parts, so a first
    name that happens to follow a comma is never mistaken for one.
    """
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) == 2 and all(parts):
        last, first = parts
        return f"{first} {last}", None
    if len(parts) == 3 and all(parts) and parts[1].rstrip(".").casefold() in SUFFIXES:
        last, suffix, first = parts
        return f"{first} {last} {SUFFIXES[suffix.rstrip('.').casefold()]}", None
    return " ".join(raw.split()), "unrecognized_name_format"


def name_match_key(name):
    """Comparison key shared by both sources: casefold, drop . and , and generational suffixes.

    'Corey Baylor Jr.' and NCAA's 'Corey Baylor, Jr.' both become 'corey baylor'. No
    fuzzy matching: any other spelling difference stays a difference.
    """
    tokens = re.sub(r"[.,]", " ", name or "").casefold().split()
    return " ".join(token for token in tokens if token not in SUFFIXES)


def parse_row(cells, where):
    values = dict(zip(HEADERS, cells))
    stats, issues = {}, []
    for column, keys in PAIRS.items():
        found = PAIR.match(values[column])
        if not found:
            raise ParseError(f"{where}: {column} cell {values[column]!r} is not 'n-m'")
        stats[keys[0]], stats[keys[1]] = int(found[1]), int(found[2])
    for column, key in COUNTS.items():
        if not COUNT.match(values[column]):
            raise ParseError(f"{where}: {column} cell {values[column]!r} is not a whole number")
        stats[key] = int(values[column])
    if stats["oreb"] + stats["dreb"] != stats["reb"]:
        issues.append("rebounds_orb_drb_differ_from_reb")
    for made, attempted in (("fgm", "fga"), ("fg3m", "fg3a"), ("ftm", "fta")):
        if stats[made] > stats[attempted]:
            issues.append(f"{made}_exceeds_{attempted}")
    if stats["fg3m"] > stats["fgm"] or stats["fg3a"] > stats["fga"]:
        issues.append("three_pointers_exceed_field_goals")
    if stats["pts"] != 2 * stats["fgm"] + stats["fg3m"] + stats["ftm"]:
        issues.append("points_differ_from_shooting")
    minutes = values["MIN"]
    if not COUNT.match(minutes):
        raise ParseError(f"{where}: MIN cell {minutes!r} is not whole minutes")
    return {key: stats[key] for key in STAT_KEYS}, int(minutes), values["GS"] == "*", issues


def parse_team_table(table, index):
    header = [text(cell) for cell in table.thead.find_all("th")] if table.thead else []
    if header != HEADERS:
        raise ParseError(f"Player table {index}: header {header} differs from the observed layout")
    caption = CAPTION.match(text(table.caption)) if table.caption else None
    if not caption:
        raise ParseError(f"Player table {index}: caption is not '<team> <score>'")
    players, team_row = [], None
    for position, tr in enumerate(table.tbody.find_all("tr")):
        cells = tr.find_all(["td", "th"])
        if len(cells) != len(HEADERS):
            raise ParseError(f"Player table {index} row {position}: {len(cells)} cells")
        jersey = text(cells[0])
        name_cell = cells[1]
        for span in name_cell.select("span.mobile-jersey-number"):
            span.extract()  # the same jersey repeated for narrow screens
        raw_name = text(name_cell)
        stat_cells = [jersey, raw_name] + [text(cell) for cell in cells[2:]]
        stats, minutes, starter, issues = parse_row(stat_cells, f"{caption['team']} row {position}")
        if jersey == "TM" and raw_name == "TEAM":
            team_row = {"stats": stats, "minutes": minutes, "issues": issues}
            continue
        link = name_cell.find("a", href=True)
        school_id = ROSTER_LINK.search(link["href"]) if link else None
        name, name_issue = normalize_name(raw_name)
        players.append({"jersey_number": jersey, "player_name_raw": raw_name, "player_name": name,
            "name_match_key": name_match_key(name), "player_id": None,
            "school_player_id": school_id[1] if school_id else None,
            "school_roster_url": link["href"] if link else None, "starter": starter,
            "minutes": minutes, "minutes_precision": "whole_minutes", "stats": stats,
            "parser_issues": issues + ([name_issue] if name_issue else []),
            "player_match_status": MATCH_STATUS})
    footer = table.tfoot.find_all("tr") if table.tfoot else []
    if len(footer) != 1 or text(footer[0].find_all(["td", "th"])[1]) != "Totals":
        raise ParseError(f"Player table {index}: expected one Totals row in <tfoot>")
    totals_cells = [text(cell) for cell in footer[0].find_all(["td", "th"])]
    totals_cells[2] = ""  # GS shows '-' on the totals row
    totals, total_minutes, _, total_issues = parse_row(totals_cells, f"{caption['team']} totals")
    if team_row is None:
        raise ParseError(f"Player table {index}: no TM TEAM row")
    return {"team_name": caption["team"], "caption_score": int(caption["score"]), "players": players,
            "team_row": team_row, "totals": {"stats": totals, "minutes": total_minutes, "issues": total_issues}}


def game_details(soup):
    details = {}
    for dl in soup.select("aside dl, .game-details dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                details.setdefault(text(dt), text(dd))
    when = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", details.get("Date", ""))
    return {"game_date": date(2000 + int(when[3]), int(when[1]), int(when[2])).isoformat() if when else None,
            "start_time_local": details.get("Time"),
            "attendance": int(details["Attendance"].replace(",", "")) if details.get("Attendance", "").replace(",", "").isdigit() else None,
            "site": details.get("Site")}


def source_checks(team):
    """Same idea as the NCAA source_checks: player sums + TEAM row vs Totals, points vs caption."""
    checks = []
    for key in STAT_KEYS:
        observed = sum(player["stats"][key] for player in team["players"]) + team["team_row"]["stats"][key]
        expected = team["totals"]["stats"][key]
        checks.append({"check": f"players_plus_team_vs_totals:{key}", "team_name": team["team_name"],
                       "observed": observed, "expected": expected, "status": "pass" if observed == expected else "fail"})
    points = team["totals"]["stats"]["pts"]
    checks.append({"check": "totals_points_vs_caption_score", "team_name": team["team_name"], "observed": points,
                   "expected": team["caption_score"], "status": "pass" if points == team["caption_score"] else "fail"})
    minutes = sum(player["minutes"] for player in team["players"])
    # Whole-minute rounding can make players' minutes miss the 200 total; recorded, not failed.
    checks.append({"check": "player_minutes_vs_totals_minutes", "team_name": team["team_name"], "observed": minutes,
                   "expected": team["totals"]["minutes"], "status": "pass" if minutes == team["totals"]["minutes"] else "info",
                   "note": "whole minutes; rounding differences are expected"})
    return checks


def parse_sidearm_boxscore(html, source_url):
    if isinstance(html, bytes):
        raw = html
        html = html.decode("utf-8")
    else:
        raw = html.encode("utf-8")
    blocker = page_blocker(html)
    if blocker:
        raise ParseError(f"Page is not a box score ({blocker})")
    soup = BeautifulSoup(html, "html.parser")
    tables = [table for table in soup.select("table.sidearm-table.overall-stats")
              if table.thead and [text(th) for th in table.thead.find_all("th")][:2] == ["##", "Player"]]
    if len(tables) != 2:
        raise ParseError(f"Expected two player tables, found {len(tables)}")
    teams = [parse_team_table(table, index) for index, table in enumerate(tables)]
    title = text(soup.title) if soup.title else None
    return {"parser": PARSER, "source_url": source_url, "html_sha256": hashlib.sha256(raw).hexdigest(),
            "parsed_at": utc_now(), "page_title": title, **game_details(soup), "teams": teams,
            "source_checks": [check for team in teams for check in source_checks(team)],
            "ncaa_player_id_available": False}
