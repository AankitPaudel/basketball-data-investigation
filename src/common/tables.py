"""Conservative flat-table adapter. Live source layouts remain unverified."""
import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .http_client import page_blocker, safe_url
from .models import PlayerRecord


class ParseError(ValueError):
    pass


ALIASES = {
    "name": "player", "player": "player", "playername": "player",
    "no": "jersey", "#": "jersey", "jersey": "jersey",
    "pos": "position", "position": "position", "min": "minutes", "minutes": "minutes",
    "pts": "pts", "points": "pts", "reb": "reb", "totreb": "reb", "trb": "reb",
    "ast": "ast", "assists": "ast", "oreb": "oreb", "orb": "oreb",
    "dreb": "dreb", "drb": "dreb", "stl": "stl", "blk": "blk",
    "to": "tov", "tov": "tov", "pf": "pf", "fgm": "fgm", "fga": "fga",
    "3fgm": "fg3m", "3pm": "fg3m", "3fga": "fg3a", "3pa": "fg3a",
    "ftm": "ftm", "fta": "fta", "fg": "fg_pair", "fgma": "fg_pair",
    "fgmfga": "fg_pair", "3pt": "fg3_pair",
    "3p": "fg3_pair", "3fg": "fg3_pair", "3pm3pa": "fg3_pair",
    "ft": "ft_pair", "ftmfta": "ft_pair",
}
STAT_KEYS = ("pts", "reb", "ast", "oreb", "dreb", "stl", "blk", "tov", "pf",
             "fgm", "fga", "fg3m", "fg3a", "ftm", "fta")
MISSING = {"", "-", "--", "—", "n/a", "na"}


def header(text):
    return ALIASES.get(re.sub(r"[\s./()_-]", "", text.lower()))


def number(text, field, issues):
    if text.lower().strip() in MISSING:
        return None
    if not re.fullmatch(r"\d+", text.strip()):
        issues.append(f"invalid_number:{field}")
        return None
    return int(text)


def identifier(cell):
    link = cell.find("a", href=True)
    if not link:
        return None
    parsed = urlsplit(link["href"])
    match = re.search(r"/(?:players?|people)/(\d+)(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    return next((query[key][0] for key in ("stats_player_seq", "player_id") if key in query), None)


def parse_tables(html, *, source, source_url, game_id=None, game_date=None,
                 team=None, kind="boxscore", provenance="local_unverified"):
    if page_blocker(html):
        raise ParseError("The document is an access challenge or login page")
    if kind == "boxscore" and not game_id:
        raise ParseError("A game ID is required; it is never inferred from a page title")
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for table in soup.find_all("table"):
        if table.find_parent("table"):
            continue
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        for index, row in enumerate(rows):
            cells = row.find_all(["th", "td"], recursive=False)
            mapping = [header(cell.get_text(" ", strip=True)) for cell in cells]
            if "player" in mapping and (kind == "roster" or "pts" in mapping):
                known = [key for key in mapping if key]
                if len(known) != len(set(known)):
                    raise ParseError("Duplicate mapped headers; an explicit source adapter is required")
                candidates.append((table, rows[index + 1:], mapping))
                break
    if not candidates:
        raise ParseError("No supported flat player table found; save an authorized sample to adapt the parser")
    if team and len(candidates) > 1:
        raise ParseError("--team applies only to a single table; multiple tables need team captions")
    records = []
    for table, rows, mapping in candidates:
        if any(cell.get("colspan", "1") != "1" or cell.get("rowspan", "1") != "1"
               for cell in table.find_all(["th", "td"])):
            raise ParseError("Spanning cells require an explicit source adapter")
        caption = table.find("caption")
        team_name = team or (caption.get_text(" ", strip=True) if caption else None)
        if not team_name:
            raise ParseError("Missing team: supply --team for one table or use a table with a team caption")
        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            if [header(value) for value in values] == mapping:
                continue
            if len(cells) != len(mapping):
                raise ParseError("Irregular row width; refusing a potentially shifted statistic mapping")
            data = {key: value for key, value in zip(mapping, values) if key}
            name = data["player"].strip()
            if name.lower() in {"total", "totals", "team", "team totals", "team total"}:
                continue
            if not name:
                raise ParseError("Empty player name in a player row")
            record = PlayerRecord(source=source, source_url=safe_url(source_url),
                game_id=game_id, game_date=game_date, team=team_name, player=name,
                player_id=identifier(cells[mapping.index("player")]),
                jersey=data.get("jersey"), position=data.get("position"),
                minutes=data.get("minutes"), provenance=provenance)
            if kind == "boxscore":
                dnp = any(value.lower() in {"dnp", "did not play", "did not dress"} for value in values)
                record.participation = "did_not_play" if dnp else "unknown"
                record.stats = {key: None for key in STAT_KEYS}
                for key in STAT_KEYS:
                    if key in data and not (dnp and data[key].lower() in {"dnp", "did not play", "did not dress"}):
                        record.stats[key] = number(data[key], key, record.validation)
                for pair, made, attempted in (("fg_pair", "fgm", "fga"),
                        ("fg3_pair", "fg3m", "fg3a"), ("ft_pair", "ftm", "fta")):
                    if pair not in data or data[pair].lower() in MISSING:
                        continue
                    match = re.fullmatch(r"(\d+)\s*[-/]\s*(\d+)", data[pair])
                    if not match:
                        record.validation.append(f"invalid_shooting_pair:{pair}")
                        continue
                    for key, value in zip((made, attempted), map(int, match.groups())):
                        if record.stats[key] is not None and record.stats[key] != value:
                            record.validation.append(f"conflicting_stat:{key}")
                        record.stats[key] = value
            records.append(record)
    if not records:
        raise ParseError("Supported tables contained no player records")
    return records
