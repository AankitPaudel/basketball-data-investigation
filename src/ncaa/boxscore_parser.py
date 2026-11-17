"""NCAA box-score adapters.

``parse_boxscore`` is the original provisional flat-table adapter (synthetic fixtures).
``parse_individual_stats`` is built from a real saved stats.ncaa.org
``/contests/{id}/individual_stats`` page (contest 6643366, 11/16/2026): one
``table#competitor_{id}_year_stat_category_0_data_table`` per team with a fixed header,
``tr#game_player_*`` rows, player links ``/players/{id}``, then a ``TEAM`` row and a
team-totals row. ``parse_team_stats`` reads the "Team Stats" panel of ``/box_score``.
"""
import hashlib
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..common.http_client import page_blocker, safe_url, validate_url
from ..common.models import utc_now
from ..common.tables import ParseError, STAT_KEYS, parse_tables

# Exact header sequence observed on the real page; any drift stops parsing.
INDIVIDUAL_HEADERS = ["#", "Name", "P", "MP", "FGM", "FGA", "FG%", "3FG", "3FGA", "FT", "FTA",
    "PTS", "ORebs", "DRebs", "Tot Reb", "AST", "TO", "STL", "BLK", "Fouls", "DQ",
    "Tech Fouls", "Bench"]
COLUMN_KEYS = {"FGM": "fgm", "FGA": "fga", "3FG": "fg3m", "3FGA": "fg3a", "FT": "ftm",
    "FTA": "fta", "PTS": "pts", "ORebs": "oreb", "DRebs": "dreb", "Tot Reb": "reb",
    "AST": "ast", "TO": "tov", "STL": "stl", "BLK": "blk", "Fouls": "pf"}
# Present on the page but outside the 39-column schema; kept in JSON only.
EXTRA_KEYS = {"FG%": "fg_pct", "DQ": "dq", "Tech Fouls": "tech_fouls", "Bench": "bench_points"}
PARSER = "ncaa_individual_stats_v1"


def parse_boxscore(html, **metadata):
    return parse_tables(html, source="ncaa", kind="boxscore", **metadata)


def text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def link_id(node, kind):
    link = node.find("a", href=re.compile(rf"^/{kind}/\d+$")) if node else None
    return (link["href"].rsplit("/", 1)[1], link) if link else (None, None)


def page_contest_id(soup):
    ids = {match.group(1) for link in soup.find_all("a", href=True)
           if (match := re.fullmatch(r"/contests/(\d+)/\w+", link["href"]))}
    if len(ids) != 1:
        raise ParseError(f"Expected one contest ID in page links, found {sorted(ids)}")
    return ids.pop()


def check_source(source_url, contest_id, page):
    validate_url(source_url, "ncaa")
    match = re.fullmatch(rf"/contests/(\d+)/{page}", urlsplit(source_url).path)
    if not match or match.group(1) != contest_id:
        raise ParseError(f"Source URL is not /contests/{contest_id}/{page}")


def game_header(soup):
    """Linescore block above the tabs: short team names, period scores, totals, date."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr", recursive=False)
        if not rows or [text(cell) for cell in rows[0].find_all("td")][-1:] != ["S"]:
            continue
        teams = []
        for row in rows[1:3]:
            cells = [text(cell) for cell in row.find_all("td")]
            if len(cells) < 3 or not all(value.isdigit() for value in cells[1:]):
                raise ParseError("Unexpected game-header linescore row")
            teams.append({"team_name": cells[0], "period_scores": [int(v) for v in cells[1:-1]],
                          "final_score": int(cells[-1])})
        extra = [text(row) for row in rows[3:]]
        when = next((re.match(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", value)
                     for value in extra if re.match(r"\d{2}/\d{2}/\d{4}", value)), None)
        attendance = next((value for value in extra if value.startswith("Attendance:")), None)
        venue = next((value for value in extra if value is not attendance
                      and not re.match(r"\d{2}/\d{2}/\d{4}", value)), None)
        if len(teams) != 2 or not when:
            raise ParseError("Game header lacks two teams or a date")
        return {"teams": teams,
                "game_date": f"{when.group(3)}-{when.group(1)}-{when.group(2)}",
                "start_time_local": when.group(4),
                "venue": venue,
                "attendance": int(re.sub(r"\D", "", attendance)) if attendance else None}
    raise ParseError("Game header linescore not found")


def stat_value(cell, label, issues):
    value = text(cell)
    if value == "":
        return None
    if label == "FG%":
        return float(value) if re.fullmatch(r"\d+(?:\.\d+)?", value) else None
    if not re.fullmatch(r"\d+", value):
        issues.append(f"invalid_number:{COLUMN_KEYS.get(label) or EXTRA_KEYS.get(label)}")
        return None
    return int(value)


def team_table(table, issues):
    card = table.find_parent("div", class_="card")
    team_id, team_link = link_id(card.find("div", class_="card-header") if card else None, "teams")
    if not team_id:
        raise ParseError("Individual stats table without a /teams/{id} card header")
    team_name = text(team_link)
    headers = [text(cell) for cell in table.thead.find_all("th")] if table.thead else []
    if headers != INDIVIDUAL_HEADERS:
        raise ParseError(f"Unexpected individual-stats header for {team_name}: {headers}")
    players, team_row, totals = [], None, None
    rows = table.tbody.find_all("tr", recursive=False) if table.tbody else []
    for position, row in enumerate(rows):
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(INDIVIDUAL_HEADERS):
            raise ParseError(f"Irregular row width in {team_name} table")
        values = dict(zip(INDIVIDUAL_HEADERS, cells))
        row_issues = []
        stats = {key: stat_value(values[label], label, row_issues) for label, key in COLUMN_KEYS.items()}
        extra = {key: stat_value(values[label], label, row_issues) for label, key in EXTRA_KEYS.items()}
        name = text(values["Name"])
        player_id, _ = link_id(values["Name"], "players")
        minutes = text(values["MP"]) or None
        entry = {"source_row_id": row.get("id"), "stats": stats, "extra": extra, "minutes": minutes}
        if not player_id and name == "TEAM":
            team_row = entry
            continue
        if not player_id and name == team_name and position == len(rows) - 1:
            totals = entry
            continue
        if not name:
            raise ParseError(f"Empty name in {team_name} player row")
        if not player_id:
            row_issues.append("missing_player_id")
        # A blank minutes cell with no recorded statistics is treated as not playing.
        played = minutes is not None or any(value for value in stats.values())
        players.append({**entry, "team_id": team_id, "team_name": team_name,
            "player_id": player_id, "player_name": name,
            "jersey_number": text(values["#"]) or None, "position": text(values["P"]) or None,
            "participation": "unknown" if played else "did_not_play",
            "parser_issues": row_issues})
        issues.extend(f"{team_name}:{issue}" for issue in row_issues)
    if totals is None:
        raise ParseError(f"No team-totals row found for {team_name}")
    if not players:
        raise ParseError(f"No player rows found for {team_name}")
    competitor = re.match(r"competitor_(\d+)_", table.get("id", ""))
    return {"team_id": team_id, "team_name": team_name,
            "competitor_id": competitor.group(1) if competitor else None,
            "players": players, "team_row": team_row, "totals": totals}


def parse_individual_stats(html, *, source_url, provenance="operator_saved_html", parsed_at=None):
    """Accepts saved bytes (hashed exactly as stored) or decoded text."""
    raw = html if isinstance(html, bytes) else html.encode("utf-8")
    html = html.decode("utf-8-sig") if isinstance(html, bytes) else html
    if page_blocker(html):
        raise ParseError("The document is an access challenge or login page")
    soup = BeautifulSoup(html, "html.parser")
    contest_id = page_contest_id(soup)
    check_source(source_url, contest_id, "individual_stats")
    header = game_header(soup)
    issues = []
    tables = soup.find_all("table", id=re.compile(r"^competitor_\d+_year_stat_category_0_data_table$"))
    if len(tables) != 2:
        raise ParseError(f"Expected two team individual-stats tables, found {len(tables)}")
    teams = [team_table(table, issues) for table in tables]
    for team, listed in zip(teams, header["teams"]):
        if team["team_name"] != listed["team_name"]:
            raise ParseError("Stats table order does not match the game header")
        team["final_score"] = listed["final_score"]
        team["period_scores"] = listed["period_scores"]
    return {"schema_version": 1, "kind": "ncaa_individual_stats", "contest_id": contest_id,
            **{key: header[key] for key in ("game_date", "start_time_local", "venue", "attendance")},
            "teams": teams, "issues": issues,
            "provenance": {"source": "ncaa", "source_url": safe_url(source_url),
                           "provenance": provenance, "html_sha256": hashlib.sha256(raw).hexdigest(),
                           "parsed_at": parsed_at or utc_now(), "parser": PARSER}}


def parse_team_stats(html, *, source_url):
    """The /box_score "Team Stats" panel: {label: {team_name: value}}."""
    raw = html if isinstance(html, bytes) else html.encode("utf-8")
    html = html.decode("utf-8-sig") if isinstance(html, bytes) else html
    soup = BeautifulSoup(html, "html.parser")
    contest_id = page_contest_id(soup)
    check_source(source_url, contest_id, "box_score")
    panel = soup.find("table", id="team_stats_table")
    if panel is None:
        raise ParseError("Team Stats panel not found")
    result = {}
    for table in panel.find_all("table"):
        rows = table.find_all("tr")
        label = text(rows[0]) if rows else ""
        values = {}
        for row in rows[1:]:
            cells = [text(cell) for cell in row.find_all("td")]
            if len(cells) >= 2 and re.fullmatch(r"\d+(?:\.\d+)?", cells[-1]):
                values[cells[0]] = float(cells[-1]) if "." in cells[-1] else int(cells[-1])
        if label and len(values) == 2:
            result[label] = values
    if not result:
        raise ParseError("Team Stats panel contained no values")
    return {"contest_id": contest_id, "stats": result,
            "source_url": safe_url(source_url), "html_sha256": hashlib.sha256(raw).hexdigest()}


__all__ = ["parse_boxscore", "parse_individual_stats", "parse_team_stats", "STAT_KEYS"]
