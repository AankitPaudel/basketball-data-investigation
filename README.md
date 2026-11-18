# NCAA Basketball Data Investigation

A small Python project for NCAA basketball access diagnostics, saved-page parsing,
player/roster/conference enrichment, validation, and CSV/JSON exports.
The configurable target season is **2026-27**, starting with men's basketball.

**Current status:** three real 2026-27 men's games have been extracted at player level
from operator-saved stats.ncaa.org pages, one from each division scoreboard (season ID
17240). Player points equal both final scores in every game, and all 57 cross-page
checks pass per game.

| Contest | Scoreboard | Game | Player rows | Roster matches |
|---|---|---|---|---|
| 6643366 | D-I, 2026-11-16 | Ferrum 57 at George Washington 94 | 30: 14 Division I (George Washington) + 16 Division II (Ferrum, reclassifying) | 30/30 by ID |
| 6631754 | D-II, 2026-11-15 | Ferrum 74 at Fairmont St. 94 | 23, all Division II | 23/23 by ID |
| 6617400 | D-III, 2026-11-14 | Bowdoin 63 at UChicago 87 | 24, all Division III | 24/24 by ID |

The scoreboard a game appears on is not the division of its rows. Contest 6643366 is on
the D-I scoreboard, but only George Washington's 14 rows are Division I. Ferrum's 16
rows are Division II, the sport division in the NCAA Directory, so the affiliation
mapping assigns each team its own division.

Every CSV is a **local validation preview**: no `permission_reference` has been
supplied, so none is an authorized export. Division and conference come from
`config/ncaa_team_affiliations.json`, which cites the NCAA Directory plus a conference
or institutional page for each team. stats.ncaa.org's robots policy disallows
automated access (verified again for the D-II/D-III scoreboards on 2026-11-17), so all
NCAA pages are saved manually.

## Setup and tests

Python 3.10+ is required. From the repository root, use the existing environment:

```powershell
.\benv\Scripts\python.exe -m pip install -r requirements.txt
.\benv\Scripts\python.exe -m unittest discover -s tests -v
```

For a fresh environment, run `python -m venv .venv`, then use
`.\.venv\Scripts\python.exe` in place of the interpreter above.
`requirements-tested.txt` records the installed runtime versions used for testing.
A clean `git clone` of the committed repository passes the full test suite, using the
existing interpreter. Installing from that snapshot into a new virtual environment has
not been verified locally, because it needs PyPI access. The rebuild-check workflow
does this on GitHub.

How the project got here (what was tried, what failed, and why):
[docs/process_writeup.md](docs/process_writeup.md).

## Enrich and export

Run the offline synthetic example:

```powershell
.\benv\Scripts\python.exe -m src.main process-ncaa --manifest tests/fixtures/ncaa_enrichment_synthetic.json --season 2026-27 --output data/processed/example
```

The output directory contains a 39-column player-game CSV, JSON retaining raw input
values and issue reasons, and `quality_report.json`. Height is in centimeters.
Player IDs take priority within the same team, season, and sport. Unique name-only
matches receive warnings; ambiguous matches retain the player row without candidate
biodata. Missing values stay null. Validation checks shooting, rebounds, team points,
missing enrichment, and duplicate identities. Repeated processing of the same input
produces identical output without adding duplicate rows.

For approved real data, fill [the input template](config/ncaa_bundle.template.json)
using [the input contract](config/README.md), then run:

```powershell
.\benv\Scripts\python.exe -m src.main process-ncaa --manifest data/inputs/authorized-game.json --season 2026-27 --output data/exports
```

That real input is not included. `data/exports/` requires authorized-source provenance
and a verification reference; synthetic/unreviewed bundles belong in `data/processed/`.
A reference is an operator attestation, not automatic source verification. Set
`--ncaa-season-id` only when the actual NCAA identifier has been verified.

## Build a real game from saved pages

```powershell
.\benv\Scripts\python.exe -m src.main build-ncaa-game `
  --scoreboard "ankit/d1_scoreboard.html" "https://stats.ncaa.org/season_divisions/18983/livestream_scoreboards?game_date=11%2F16%2F2026&conference_id=0" `
  --individual-stats "ankit/d1_individual_stats.html" https://stats.ncaa.org/contests/6643366/individual_stats `
  --box-score "ankit/d1_box_score.html" https://stats.ncaa.org/contests/6643366/box_score `
  --roster "ankit/d1_gw_roster.html" https://stats.ncaa.org/teams/627529/roster `
  --roster "ankit/d1_ferrum_roster.html" https://stats.ncaa.org/teams/627884/roster `
  --affiliations config/ncaa_team_affiliations.json `
  --output data/processed/ncaa-game-6643366
```

Files under `ankit/` are named by game (`d1_*`, `d2_*`, `d3_*`). This parses the real layouts and writes `manifest.json`, then runs the existing
`process-ncaa` pipeline on it.
- Individual stats: `src/ncaa/boxscore_parser.py:parse_individual_stats`.
- Rosters: `src/ncaa/roster_parser.py:parse_ncaa_roster`.
- Box-score Team Stats panel: `parse_team_stats`.

The scoreboard must list the contest as Final. `TEAM` and team-totals rows are kept in
the manifest but excluded from player rows. `quality_report.json` holds two kinds of check:
- `source_checks`: player sums plus the TEAM row vs. totals for every statistic,
  points vs. the header, totals and scoreboard, minutes vs. 200:00, and totals vs.
  the Team Stats panel.
- `team_score_checks`: the pipeline's own points check.

Without `--permission-reference` the bundle is labeled `local_validation_preview`.
The CSV is then named `..._LOCAL_PREVIEW.csv`, and the pipeline refuses `data/exports`.
Supply `--permission-reference` (and `--verification-reference` after review) only
when that attestation genuinely exists. Exit code 2 means rows carry warnings
(currently missing division/conference) or a source check failed.

To reproduce every saved game in one step, list each game's pages in
`config/ncaa_games.json` and run:

```powershell
.\benv\Scripts\python.exe -m src.main build-ncaa-games --plan config/ncaa_games.json
```

Each game writes its own output directory (CSV, JSON, `manifest.json`,
`quality_report.json`). A game whose pages are missing reports exit code 4 without
stopping the others; `--only GAME_ID ...` selects games.

### Combine games into one CSV

```powershell
.\benv\Scripts\python.exe -m src.main combine-ncaa-games --plan config/ncaa_games.json --output data/processed/ncaa-combined
```

This merges every game's CSV from the plan into one 39-column file, in plan order, and
writes `column_dictionary.csv` and `combine_report.json` (source hashes, rows by
division, validation counts) next to it. The current three games give 77 rows: 14
Division I (George Washington), 39 Division II (16 Ferrum in contest 6643366, 23 in
contest 6631754) and 24 Division III. A
source CSV whose content no longer matches its `quality_report.json` hash is refused,
as are duplicate player-game rows. The name keeps `_LOCAL_PREVIEW` unless every game is
`authorized_source`, and `data/exports` is refused for previews. Column meanings:
[docs/ncaa_column_dictionary.md](docs/ncaa_column_dictionary.md). Page types, URL
patterns, IDs and quirks: [docs/ncaa_site_structure.md](docs/ncaa_site_structure.md).

### Rebuild check (local and cloud)

`config/ncaa_games_fixtures.json` rebuilds the three validated games from the committed
sanitized fixtures, with each original `extracted_at`. It compares each CSV's content
hash with `expected_csv_sha256`, and a mismatch gives exit code 2 with
`rebuild_match: false`:

```powershell
.\benv\Scripts\python.exe -m src.main build-ncaa-games --plan config/ncaa_games_fixtures.json
.\benv\Scripts\python.exe -m src.main combine-ncaa-games --plan config/ncaa_games_fixtures.json --output data/processed/fixture-rebuild/combined --expect-sha256 402921c1ad1bf0a74590ca360a972dc36eaf0ddb630e4a44651b17e9ec6df03f
```

All outputs are written with LF line endings on every platform, so a rebuilt CSV's
on-disk SHA-256 equals the `csv_sha256` in its `quality_report.json`, locally and in the
cloud. The three validated CSVs were first written on Windows with CRLF line endings;
on 2026-11-18 they were deliberately converted to LF (line endings only, content
unchanged). Their baseline hashes are now `b964bb2d…`, `c4a24d7f…` and `525bfea2…`,
replacing `363d7b4e…`, `d27f590b…` and `a7f1b23b…`; the full table is in
[docs/process_writeup.md](docs/process_writeup.md) section 7. The manual
`.github/workflows/rebuild-check.yml` workflow runs the same check on an Ubuntu
runner, after a fresh install from `requirements-tested.txt`. It contacts no data
source.

`--affiliations` applies a season-specific division/conference mapping (also accepted
by `process-ncaa`). An entry applies only when its season, sport, team ID and team name
match. Each entry needs an NCAA Directory `orgDetail` page for that academic year, and
every evidence item must agree with the entry. Conflicting entries, evidence, or bundle
values stop processing. Division is never taken from a scoreboard filter or a ranking
URL parameter. The CSV `division` column is the
*sport-specific* division from the directory's sport row. The institution's own
membership (`institution_division`, `institution_status`, `reclass_division`) is kept
separately in the mapping and the quality report, because the two can differ. For
example, Ferrum is a Division III "Expedited Reclassifying Member" (Reclass Division II,
Year 2) whose men's basketball row is Division II. Reclassifying teams get a
`division_reclassifying` warning. A non-reclassifying sport division that differs from
the institution's needs an explicit `sport_division_note`. Raw evidence pages are kept in git-ignored `data/raw/affiliations/`, and their
SHA-256 hashes are recorded in the mapping.

Regression fixtures are sanitized copies made with `python -m src.main sanitize-html`.
Scripts, styles and CSRF/authenticity tokens are removed, and parsed values are
identical to the originals. Their original URLs and hashes are in
`tests/fixtures/ncaa_real_sources.json`.

## Discover games from a saved scoreboard

```powershell
.\benv\Scripts\python.exe -m src.main discover-ncaa-games --file ankit/ncca.html --source-url "https://stats.ncaa.org/season_divisions/18983/livestream_scoreboards?game_date=11%2F16%2F2026&conference_id=0" --output data/processed/ncaa-discovery
```

`src/ncaa/scoreboard_parser.py` reads the observed livestream-scoreboard layout: game
date and local start time, contest ID, `/contests/{id}/box_score` URL, team names, team
IDs from `/teams/{id}` links (null for opponents without an NCAA team page), records
shown, final and period scores, and attendance. Each game is checked for period scores
summing to the final score and a winner marker matching the higher score.
Postponed/canceled games are excluded and listed separately. Other non-final games
are kept with `completed: false`, and only completed games are passed on. In the
saved page the "Final" status sits inside an HTML comment; this is recorded as
`status_source: html_comment`, and an active status element takes precedence.

Outputs: `scoreboard_games.json` (provenance: source URL, SHA-256 of the saved file,
page season/division IDs, parse time), `scoreboard_games.csv`, and
`ncaa_bundle.discovered.json`. The last one is a `process-ncaa` manifest skeleton with
`player_statistics: pending_boxscore`. It cannot be processed until a
`permission_reference` and box-score players are added. Scoreboard team divisions are
left null because the listing filter is not each opponent's division. To compare a
manifest's game IDs, dates, teams and scores with the scoreboard, add
`--scoreboard FILE --scoreboard-url URL` to `process-ncaa`. Results go to
`quality_report.json` under `scoreboard_cross_check`. Any non-match returns exit code 2.

## Stage 3 access probe (local vs. cloud)

See `docs/stage3_access_test_plan.md`. `python -m src.main probe --environment <label>`
prints the planned requests without using the network. `--execute` sends them, but only
to robots-permitted URLs on the allowlist in `config/access_probe_urls.json`, with the
delay and stop rules described there. The cloud run is the manual
`.github/workflows/access-probe.yml` workflow; it sends requests only when its input is
`APPROVED`.

## Diagnostics and saved HTML

```powershell
.\benv\Scripts\python.exe -m src.main diagnose ncaa https://stats.ncaa.org/contests/5731445/box_score --environment local-windows --log logs/local.jsonl
.\benv\Scripts\python.exe -m src.main parse --help
.\benv\Scripts\python.exe -m src.main collect --help
.\benv\Scripts\python.exe -m src.main demo
```

The URL above is a historical diagnostic candidate, not a verified current-season game.
`diagnose` records policy/access outcomes, status, timing, redirects, content type,
response size, hashes, and environment labels. NCAA collection stops when robots
rules disallow access. Credentials, cookies, and sensitive query parameters are not
logged. The environment label alone does not prove a cloud test took place.

`parse` processes local HTML; `collect` fetches a permitted page before parsing.
Both use the provisional flat-table adapters. `demo` exercises those adapters offline
with fictional NCAA box-score and roster fixtures. These commands produce the simpler
parser schema; use `process-ncaa` for the enriched CSV contract. No source layout is
guessed when tables cannot be parsed safely.

Exit codes: **0** successful checks/output; **2** exported quality issues (also invalid
CLI syntax); **3** access/policy/network blocker; **4** input or processing failure.
Each output directory is replaced per run; use separate directories for separate bundles.

## Repository layout

```text
src/
  ncaa/        NCAA client, parsers, enrichment, and processing pipeline
  common/      HTTP diagnostics, schemas, validation, and exports
  main.py      Command-line entry point
tests/         Automated tests, synthetic fixtures, and sanitized real NCAA pages
config/        Input template and format documentation
data/
  source_inventory.csv   Discovered URLs and their verification/access status
  inputs/      Private input bundles
  raw/         Permitted source captures
  processed/   Review and synthetic outputs
  exports/     Reviewed real-source exports
logs/          Local diagnostic evidence and future run logs
```

The existing NCAA access evidence is retained locally in
`logs/ncaa-source-diagnostics.jsonl` and `logs/ncaa-policy-diagnostics.jsonl`.
Source links and their discovery provenance are in [the inventory](data/source_inventory.csv).
Generated data, private captures, logs, credentials, caches, and virtual environments
are excluded from Git. No database, deployment, or paid service is required.
