# Player-game CSV column dictionary

The 39 columns of every per-game CSV and of the combined CSV (`combine-ncaa-games`), in order. The same table is written as `column_dictionary.csv` next to the combined CSV; both come from `src/ncaa/combine.py:COLUMN_DICTIONARY`.

Blank means unknown: values are never guessed. Files whose name ends in `_LOCAL_PREVIEW` are validation previews without a permission reference.

| # | Column | Type | Format / unit | Meaning and source |
|---|---|---|---|---|
| 1 | `season` | text | YYYY-YY | Season label of the game bundle, e.g. 2026-27. |
| 2 | `sport` | text | code | MBB = men's basketball. |
| 3 | `division` | text | I / II / III | Sport-specific NCAA division of the player's team, from the NCAA Directory sport row via config/ncaa_team_affiliations.json. Can differ from the institution's division (Ferrum: institution III, men's basketball II while reclassifying). Never taken from the scoreboard filter. |
| 4 | `game_id` | text | integer ID | stats.ncaa.org contest ID, as in /contests/{game_id}/individual_stats. |
| 5 | `game_date` | date | YYYY-MM-DD | Game date from the contest page header, checked against the scoreboard. |
| 6 | `team_id` | text | integer ID | stats.ncaa.org team-season ID of the player's team, as in /teams/{team_id}/roster. A new ID each season. |
| 7 | `team_name` | text |  | Team name as shown on the contest page. |
| 8 | `opponent_team_id` | text | integer ID | stats.ncaa.org team-season ID of the opponent. |
| 9 | `opponent_name` | text |  | Opponent name as shown on the contest page. |
| 10 | `conference` | text |  | Conference of the player's team for this season, from config/ncaa_team_affiliations.json. Blank if no source-backed entry exists. |
| 11 | `player_id` | text | integer ID | stats.ncaa.org player ID from the /players/{id} link on the individual-stats page; used to match the roster. |
| 12 | `player_name` | text |  | Player name as shown on the individual-stats page. |
| 13 | `jersey_number` | text |  | Jersey number from the individual-stats page (roster value if missing). |
| 14 | `position` | text | G / F / C | Roster position, normalized; blank when the roster leaves it empty. |
| 15 | `height` | number | centimeters | Roster height converted from feet-inches, 2 decimals; blank if not listed. |
| 16 | `class_year` | text | Freshman / Sophomore / Junior / Senior / Graduate | Roster class, normalized. |
| 17 | `hometown` | text |  | Roster hometown as listed. |
| 18 | `minutes` | text | MM:SS | Minutes played, as shown on the individual-stats page. |
| 19 | `fgm` | integer | count | Field goals made. |
| 20 | `fga` | integer | count | Field goals attempted. |
| 21 | `three_pm` | integer | count | Three-point field goals made. |
| 22 | `three_pa` | integer | count | Three-point field goals attempted. |
| 23 | `ftm` | integer | count | Free throws made. |
| 24 | `fta` | integer | count | Free throws attempted. |
| 25 | `points` | integer | count | Points scored. |
| 26 | `offensive_rebounds` | integer | count | Offensive rebounds. |
| 27 | `defensive_rebounds` | integer | count | Defensive rebounds. |
| 28 | `total_rebounds` | integer | count | Total rebounds. |
| 29 | `assists` | integer | count | Assists. |
| 30 | `steals` | integer | count | Steals. |
| 31 | `blocks` | integer | count | Blocked shots. |
| 32 | `turnovers` | integer | count | Turnovers. |
| 33 | `fouls` | integer | count | Personal fouls. |
| 34 | `boxscore_source_url` | url |  | stats.ncaa.org individual-stats page the statistics were read from. |
| 35 | `roster_source_url` | url |  | stats.ncaa.org roster page the biodata were read from. |
| 36 | `conference_source_url` | url |  | NCAA Directory orgDetail page cited for division and conference. |
| 37 | `extracted_at` | timestamp | ISO 8601, UTC | When the game was built from the saved pages. |
| 38 | `player_match_status` | text | matched_id / matched_name / ambiguous_id / ambiguous_name / jersey_conflict / unmatched | How the box-score player was matched to the roster. matched_id = same NCAA player ID. |
| 39 | `validation_status` | text | PASS / WARNING / FAIL | Row-level result. WARNING reasons (e.g. division_reclassifying, missing_position, missing_height) are listed per row in the game's JSON output and counted in its quality_report.json. |
