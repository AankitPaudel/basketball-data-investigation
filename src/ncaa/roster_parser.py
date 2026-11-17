"""NCAA roster adapters.

``parse_roster`` is the original provisional flat-table adapter (synthetic fixtures).
``parse_ncaa_roster`` is built from real saved stats.ncaa.org ``/teams/{id}/roster``
pages (George Washington 627529 and Ferrum 627884, 2026-27 MBB): one
``table#rosters_form_players_{season_id}_data_table`` with a fixed header and
``/players/{id}`` links. Values are kept as printed; normalization happens downstream.
"""
import hashlib
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..common.http_client import page_blocker, safe_url, validate_url
from ..common.models import utc_now
from ..common.tables import ParseError, parse_tables

ROSTER_HEADERS = ["GP", "GS", "#", "Name", "Class", "Position", "Height", "Hometown", "High School"]
SPORTS = {"Men's Basketball": "MBB", "Women's Basketball": "WBB"}
PARSER = "ncaa_team_roster_v1"


def parse_roster(html, **metadata):
    return parse_tables(html, source="ncaa", kind="roster", **metadata)


def text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def team_identity(soup):
    """Team ID, season and sport from the selected options in the team-page header."""
    year = soup.select_one("#year_list option[selected]")
    sport = soup.select_one("#sport_list option[selected]")
    header = soup.select_one("div.card-header a[target=ATHLETICS_URL]")
    logo = soup.select_one("div.card-header img.logo_image")
    if not year or not sport:
        raise ParseError("Team page header (season/sport selectors) not found")
    if year.get("value") != sport.get("value"):
        raise ParseError("Season and sport selectors point to different team IDs")
    return {"team_id": year["value"], "season": text(year), "sport": SPORTS.get(text(sport)),
            "sport_label": text(sport), "team_display_name": text(header) if header else None,
            # The logo alt text is the short name used on box scores and scoreboards.
            "team_name": logo.get("alt") if logo else None}


def parse_ncaa_roster(html, *, source_url, provenance="operator_saved_html", parsed_at=None):
    raw = html if isinstance(html, bytes) else html.encode("utf-8")
    html = html.decode("utf-8-sig") if isinstance(html, bytes) else html
    if page_blocker(html):
        raise ParseError("The document is an access challenge or login page")
    validate_url(source_url, "ncaa")
    soup = BeautifulSoup(html, "html.parser")
    identity = team_identity(soup)
    match = re.fullmatch(r"/teams/(\d+)/roster", urlsplit(source_url).path)
    if not match or match.group(1) != identity["team_id"]:
        raise ParseError(f"Source URL is not /teams/{identity['team_id']}/roster")
    tables = soup.find_all("table", id=re.compile(r"^rosters_form_players_\d+_data_table$"))
    if len(tables) != 1:
        raise ParseError(f"Expected one roster table, found {len(tables)}")
    table = tables[0]
    headers = [text(cell) for cell in table.thead.find_all("th")] if table.thead else []
    if headers != ROSTER_HEADERS:
        raise ParseError(f"Unexpected roster header: {headers}")
    players, seen = [], set()
    for row in table.tbody.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(ROSTER_HEADERS):
            raise ParseError("Irregular roster row width")
        values = dict(zip(ROSTER_HEADERS, cells))
        link = values["Name"].find("a", href=re.compile(r"^/players/\d+$"))
        player_id = link["href"].rsplit("/", 1)[1] if link else None
        if player_id and player_id in seen:
            raise ParseError(f"Duplicate roster player ID {player_id}")
        seen.add(player_id)
        players.append({"player_id": player_id, "player_name": text(values["Name"]),
            "jersey_number": text(values["#"]) or None, "class_year": text(values["Class"]) or None,
            "position": text(values["Position"]) or None, "height": text(values["Height"]) or None,
            "hometown": text(values["Hometown"]) or None,
            "high_school": text(values["High School"]) or None,
            "games_played": int(text(values["GP"])) if text(values["GP"]).isdigit() else None,
            "games_started": int(text(values["GS"])) if text(values["GS"]).isdigit() else None})
    if not players:
        raise ParseError("Roster table contained no players")
    season_id = re.search(r"_(\d+)_data_table$", table["id"]).group(1)
    return {"season": identity["season"], "sport": identity["sport"], "ncaa_season_id": season_id,
            "team_id": identity["team_id"], "team_name": identity["team_name"],
            "team_display_name": identity["team_display_name"], "source_url": safe_url(source_url),
            "players": players,
            "provenance": {"source": "ncaa", "provenance": provenance,
                           "html_sha256": hashlib.sha256(raw).hexdigest(),
                           "parsed_at": parsed_at or utc_now(), "parser": PARSER}}
