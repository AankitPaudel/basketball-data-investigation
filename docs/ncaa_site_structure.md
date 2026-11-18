# stats.ncaa.org site structure (as observed, 2026-27 men's basketball)

Everything here was observed on pages saved by hand in a browser on 2026-11-17.
stats.ncaa.org's robots.txt disallows these pages for automated clients, so the
project never requests them itself; the pipeline only reads the saved copies.

## Page types used

| Page | URL pattern | What it gives the pipeline | Parser |
|---|---|---|---|
| Scoreboard (one day, one division) | `/season_divisions/{season_division_id}/livestream_scoreboards?game_date=MM%2FDD%2FYYYY&conference_id=0` | Every game that day: contest ID, team IDs and names, final and period scores, status, attendance | `src/ncaa/scoreboard_parser.py` |
| Contest: individual stats | `/contests/{contest_id}/individual_stats` | One table per team: player rows with player IDs, minutes and all counting stats; a `TEAM` row; a totals row | `src/ncaa/boxscore_parser.py:parse_individual_stats` |
| Contest: box score | `/contests/{contest_id}/box_score` | The "Team Stats" panel, used to cross-check team totals | `src/ncaa/boxscore_parser.py:parse_team_stats` |
| Team roster | `/teams/{team_id}/roster` | Jersey, class, position, height, hometown and player ID per player | `src/ncaa/roster_parser.py:parse_ncaa_roster` |
| Team page (not used for data) | `/teams/{team_id}` | Links `org_id={ncaa_org_id}` and the school logo `{ncaa_org_id}.gif`, which tie the team to its NCAA Directory entry | – |
| Player (not saved) | `/players/{player_id}` | Only the link is used, to read the player ID | – |

## Season-division IDs

The scoreboard is selected by a season-division ID, one per division and season:

| Season | Season ID | D-I | D-II | D-III |
|---|---|---|---|---|
| 2026-27 MBB | 17240 | 18983 | 18984 | 18985 |

The season ID appears in the roster table's HTML id
(`rosters_form_players_17240_data_table`). The scoreboard's division only says which
list a game was shown on; it is **not** the division of either team (a D-II team can
appear on the D-I scoreboard, as Ferrum did at George Washington).

## How the IDs link the pages

```text
scoreboard (season_division_id, game_date)
  └─ contest_id ──► /contests/{contest_id}/individual_stats   player rows
  │                 /contests/{contest_id}/box_score          team totals
  └─ team_id ─────► /teams/{team_id}/roster                   biodata
                    /teams/{team_id} ─ org_id ─► NCAA Directory orgDetail?id={org_id}
player_id: individual-stats row ◄──── same ID ────► roster row
```

- **contest_id** identifies a game. It appears on the scoreboard (`tr#contest_{id}` and
  the `/contests/{id}/box_score` link) and in the contest page URLs.
- **team_id** is a *team-season* ID (George Washington 2026-27 = 627529). A team gets a
  new ID each season, so the same school cannot be joined across seasons by this ID.
  Some non-NCAA opponents have no team page, so their team ID is blank.
- **player_id** comes from the `/players/{id}` link on both the individual-stats page
  and the roster. The pipeline matches players by this ID first; all 77 rows in the
  three validated games matched by ID.
- **org_id** is the NCAA Directory institution ID (George Washington = 249). It is read
  from the team page's `org_id=` link and logo file name, and confirmed by the head
  coach listed in both places.

## Where division and conference come from

stats.ncaa.org pages do not reliably state a team's division or conference, so both
come from `config/ncaa_team_affiliations.json`:

1. **NCAA Directory** (`https://web3.ncaa.org/directory/orgDetail?id={org_id}`) for the
   2026-2027 academic year. The *sport row* for Men's Basketball gives the division and
   conference written to the CSV.
2. A second official page (conference schedule release or the school's own schedule)
   that agrees with the directory.

Each entry stores both URLs, retrieval time, SHA-256 and a quote. Conflicting evidence
stops processing.

## Quirks found

| Quirk | What happened | How it is handled |
|---|---|---|
| Ferrum is reclassifying | Directory: institution Division III, "Expedited Reclassifying Member", Reclass Division II (Year 2); its men's basketball row says Division II | CSV `division` = II (sport row). Institution division is kept separately in the mapping and quality report; rows get a `division_reclassifying` warning |
| "Final" hidden in a comment | On the saved scoreboards, the `livestream_status_{id}` element holding "Final" sits inside an HTML comment | Comment text is accepted and recorded as `status_source: html_comment`; an active status element would take precedence |
| Wrong UChicago page | The first file saved as `d3_uchicago_roster.html` was the team overview page (`/teams/628193`), not the roster | Detected because the page had no roster table; re-saved from `/teams/628193/roster` |
| Teams without team pages | 8 teams on the D-I scoreboard and 13 on the D-III scoreboard had no `/teams/{id}` link | Their team ID stays blank; no ID is guessed |
| Conference short names | Schools write "NESCAC" and "UAA"; the directory uses the full names | Declared `conference_aliases`; the CSV uses the directory name |
| Session tokens in saved pages | Saved HTML contains CSRF/authenticity tokens and scripts | Raw saves stay in git-ignored `ankit/`; committed fixtures are sanitized (`sanitize-html`) |
| Cloud access | From GitHub Actions even robots.txt returned HTTP 403 with a challenge page | See `docs/stage3_local_vs_cloud_report.md`; no workaround attempted |

## Checks that tie the pages together (57 per game)

For every statistic: player sums plus the `TEAM` row equal the team-totals row, and the
totals equal the box-score "Team Stats" panel. Points also equal the contest header and
the scoreboard score, and team minutes are checked against 200:00. Results are in each
game's `quality_report.json` under `source_checks`.
