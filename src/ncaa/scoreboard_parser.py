"""Adapter for the stats.ncaa.org livestream scoreboard page.

Built from a real saved 2026-27 men's D-I scoreboard (11/16/2026). Observed layout:
one card table per game, two ``tr#contest_{id}`` team rows, ``td.totalcol`` scores,
an optional ``table#linescore_{id}_table``, a ``/contests/{id}/box_score`` link and
``/teams/{id}`` links (absent for some non-NCAA opponents). In that capture the
``livestream_status_{id}`` element holding "Final" sits inside an HTML comment, so the
parser accepts comment evidence but prefers an active (uncommented) status element.

Only game-level facts are extracted. Player statistics are NOT read or verified here.
"""
import hashlib
import re
from collections import Counter
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from ..common.http_client import page_blocker, safe_url, validate_url
from ..common.models import utc_now
from ..common.tables import ParseError

BASE = "https://stats.ncaa.org"
PARSER = "ncaa_livestream_scoreboard_v1"
SPORTS = {"men's basketball": "MBB", "women's basketball": "WBB"}
DIVISIONS = {"D-I": "I", "D-II": "II", "D-III": "III"}
EXCLUDED = {"postponed", "canceled"}
RECORD = re.compile(r"\s*\((\d+)-(\d+)\)$")


def us_date(text):
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text.strip())
    if not match:
        raise ParseError(f"Unrecognized scoreboard date: {text!r}")
    month, day, year = map(int, match.groups())
    return date(year, month, day).isoformat()


def classify(text):
    folded = text.casefold().strip()
    if folded.startswith("final"):
        return "final"
    if "postpon" in folded or folded.rstrip(".") == "ppd":
        return "postponed"
    if "cancel" in folded:
        return "canceled"
    # Scheduled vs. live is not distinguishable without a verified sample of either.
    return "not_final" if folded else "unknown"


def page_metadata(soup, source_url):
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if "scoreboard" not in title.casefold():
        raise ParseError("Not an NCAA scoreboard page (no scoreboard heading)")
    season = re.search(r"\b(\d{4}-\d{2})\b", title)
    sport = next((code for name, code in SPORTS.items() if name in title.casefold()), None)
    division = next((value for label, value in sorted(DIVISIONS.items(), key=lambda item: -len(item[0]))
                     if re.search(rf"(?<![\w-]){re.escape(label)}(?![\w-])", title)), None)
    selected = soup.select_one("#game_sport_year_ctl_id_select option[selected]")
    division_input = soup.select_one("#change_season_division_id")
    date_input = soup.select_one("input#game_date")
    year_input = soup.select_one("input#academic_year")
    if not date_input or not date_input.get("value"):
        raise ParseError("Scoreboard date input not found")
    season_division_id = division_input.get("value") if division_input else None
    path_match = re.search(r"/season_divisions/(\d+)/", urlsplit(source_url).path)
    if path_match and season_division_id and path_match.group(1) != season_division_id:
        raise ParseError("Source URL season division does not match the page")
    return {"page_title": title, "season": season.group(1) if season else None,
            "ncaa_season_id": selected.get("value") if selected else None,
            "season_division_id": season_division_id,
            "academic_year": year_input.get("value") if year_input else None,
            "sport": sport, "division": division,
            "scoreboard_date": us_date(date_input["value"])}


def status_evidence(rows, contest_id):
    """Return (text, source); an active element wins over commented-out markup."""
    marker = f"livestream_status_{contest_id}"
    for row in rows:
        active = row.find(class_=marker)
        if active:
            return active.get_text(" ", strip=True), "active_markup"
    for row in rows:
        for comment in row.find_all(string=lambda node: isinstance(node, Comment) and marker in node):
            element = BeautifulSoup(comment, "html.parser").find(class_=marker)
            if element:
                return element.get_text(" ", strip=True), "html_comment"
    return "", None


def team_row(row, issues, position):
    cells = row.find_all("td", recursive=False)
    name_cell = next((cell for cell in cells if "opponents_min_width" in (cell.get("class") or [])), None)
    if name_cell is None:
        raise ParseError("Team row without a team-name cell")
    link = name_cell.find("a", href=True)
    text = name_cell.get_text(" ", strip=True)
    team_id = team_url = None
    if link:
        match = re.fullmatch(r"/teams/(\d+)", urlsplit(link["href"]).path)
        if match:
            team_id, team_url = match.group(1), urljoin(BASE, link["href"])
        else:
            issues.append(f"unrecognized_team_link:{position}")
    record = RECORD.search(text)
    name = RECORD.sub("", text).strip()
    if not name:
        raise ParseError("Empty team name in scoreboard row")
    score_cell = row.find("td", class_="totalcol", recursive=False)
    score_text = score_cell.get_text(" ", strip=True) if score_cell else ""
    score = int(score_text) if score_text.isdigit() else None
    if score_text and score is None:
        issues.append(f"invalid_score:{position}")
    return {"listed_order": position, "team_name": name, "team_id": team_id,
            "team_url": team_url, "has_team_page": team_id is not None,
            "record_shown": f"{record.group(1)}-{record.group(2)}" if record else None,
            "final_score": score, "marked_winner": "winner_background" in (name_cell.get("class") or []),
            "period_scores": None}


def parse_game(contest_id, rows, issues):
    card = rows[0].find_parent("table")
    header = next((row for row in card.find_all("tr", recursive=False) if not row.get("id")), None)
    header_text = header.get_text(" ", strip=True) if header else ""
    when = re.search(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", header_text)
    attendance = re.search(r"Attend:\s*([\d,]+)", header_text)
    teams = [team_row(row, issues, position) for position, row in enumerate(rows, start=1)]
    linescore = card.find("table", id=f"linescore_{contest_id}_table")
    if linescore:
        lines = [[int(cell.get_text(strip=True)) if cell.get_text(strip=True).isdigit() else None
                  for cell in line.find_all("td")] for line in linescore.find_all("tr")]
        if len(lines) == 2:
            for team, periods in zip(teams, lines):
                team["period_scores"] = periods
        else:
            issues.append("unexpected_linescore_rows")
    box = None
    for link in card.find_all("a", href=True):
        match = re.fullmatch(r"/contests/(\d+)/box_score", urlsplit(link["href"]).path)
        if match:
            if match.group(1) != contest_id:
                issues.append("boxscore_link_contest_mismatch")
            else:
                box = urljoin(BASE, link["href"])
    if box is None:
        issues.append("missing_boxscore_link")
    status_text, status_source = status_evidence(rows, contest_id)
    status = classify(status_text)
    scores = [team["final_score"] for team in teams]
    if all(team["period_scores"] and None not in team["period_scores"] for team in teams):
        linescore_check = "pass" if all(sum(team["period_scores"]) == team["final_score"] for team in teams) else "fail"
    else:
        linescore_check = "not_checked"
    if linescore_check == "fail":
        issues.append("linescore_sum_mismatch")
    if status == "final" and None not in scores and scores[0] != scores[1]:
        leader = 0 if scores[0] > scores[1] else 1
        if [team["marked_winner"] for team in teams] != [index == leader for index in range(2)]:
            issues.append("winner_marker_inconsistent")
    if status == "final" and None in scores:
        issues.append("final_without_scores")
    blocking = {"final_without_scores", "linescore_sum_mismatch", "boxscore_link_contest_mismatch",
                "winner_marker_inconsistent"} | {issue for issue in issues if issue.startswith("invalid_score")}
    return {"contest_id": contest_id,
            "game_date": us_date(when.group(1)) if when else None,
            "start_time_local": when.group(2) if when and when.group(2) else None,
            "attendance": int(attendance.group(1).replace(",", "")) if attendance else None,
            "status": status, "status_text": status_text or None, "status_source": status_source,
            "completed": status == "final" and None not in scores and not blocking.intersection(issues),
            "boxscore_url": box, "teams": teams, "linescore_check": linescore_check,
            "player_statistics_verified": False, "issues": sorted(set(issues))}


def parse_scoreboard(html, *, source_url, provenance="operator_saved_html", parsed_at=None):
    """Accepts saved bytes (hashed exactly as stored) or already-decoded text."""
    validate_url(source_url, "ncaa")
    raw = html if isinstance(html, bytes) else html.encode("utf-8")
    html = html.decode("utf-8-sig") if isinstance(html, bytes) else html
    if page_blocker(html):
        raise ParseError("The document is an access challenge or login page")
    soup = BeautifulSoup(html, "html.parser")
    meta = page_metadata(soup, source_url)
    grouped = {}
    for row in soup.find_all("tr", id=re.compile(r"^contest_\d+$")):
        grouped.setdefault(row["id"].split("_", 1)[1], []).append(row)
    games, excluded = [], []
    for contest_id, rows in grouped.items():
        if len(rows) != 2 or rows[0].find_parent("table") is not rows[1].find_parent("table"):
            raise ParseError(f"Contest {contest_id} does not have exactly two team rows in one card")
        issues = []
        game = parse_game(contest_id, rows, issues)
        if game["game_date"] != meta["scoreboard_date"]:
            game["issues"] = sorted({*game["issues"], "game_date_differs_from_scoreboard_date"})
        if game["status"] in EXCLUDED:
            excluded.append({"contest_id": contest_id, "status": game["status"],
                             "status_text": game["status_text"], "reason": "not_played",
                             "teams": [team["team_name"] for team in game["teams"]]})
            continue
        games.append(game)
    if not grouped:
        raise ParseError("No contest rows found on the scoreboard")
    digest = hashlib.sha256(raw).hexdigest()
    return {"schema_version": 1, "kind": "ncaa_scoreboard_discovery",
            "provenance": {"source": "ncaa", "source_url": safe_url(source_url),
                           "provenance": provenance, "html_sha256": digest,
                           "parsed_at": parsed_at or utc_now(), "parser": PARSER,
                           "player_statistics_verified": False, **meta},
            "counts": {"contests_listed": len(grouped), "games": len(games),
                       "completed": sum(game["completed"] for game in games),
                       "incomplete": sum(not game["completed"] for game in games),
                       "excluded": len(excluded),
                       "status": dict(Counter(game["status"] for game in games)),
                       "teams_without_team_page": sum(not team["has_team_page"]
                                                      for game in games for team in game["teams"])},
            "games": games, "excluded": excluded}
