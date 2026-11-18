# More games: pages to save by hand

Aim: 15 more games, for 18 in total. All of them are final games on the three
scoreboards already saved (`ankit/d1_scoreboard.html`, `d2_…`, `d3_…`), listed by
`discover-ncaa-games`. Each game listed here has NCAA team pages for both teams and no
scoreboard check issues.

## Games

Team names, IDs and scores are as listed on the scoreboard, in the scoreboard's order.

| Scoreboard | Contest | Teams (team ID) and score |
|---|---|---|
| D-I, 2026-11-16 | 6657429 | Florida A&M (627521) 46 – UTEP (627325) 79 |
| D-I, 2026-11-16 | 6639787 | Iona (627234) 67 – UMBC (627379) 75 |
| D-I, 2026-11-16 | 6635360 | Alcorn (627277) 57 – Maryland (627380) 117 |
| D-I, 2026-11-16 | 6660632 | Prairie View (627555) 59 – Ohio St. (627540) 86 |
| D-I, 2026-11-16 | 6639445 | The Citadel (627472) 51 – Clemson (627473) 87 |
| D-II, 2026-11-15 | 6651879 | Dominican (NY) (627626) 62 – Southern N.H. (627873) 80 |
| D-II, 2026-11-15 | 6646226 | Jefferson (627876) 84 – St. Thomas Aquinas (627877) 99 |
| D-II, 2026-11-15 | 6621018 | Shippensburg (627788) 61 – Charleston (WV) (627881) 76 |
| D-II, 2026-11-15 | 6642528 | Miles (627763) 62 – Lee (627692) 75 |
| D-II, 2026-11-15 | 6616334 | Caldwell (627843) 66 – Bentley (627729) 63 |
| D-III, 2026-11-14 | 6638007 | Randolph (627957) 56 – N.C. Wesleyan (627934) 79 |
| D-III, 2026-11-14 | 6629858 | Asbury (627959) 74 – Dominican (IL) (628069) 69 |
| D-III, 2026-11-14 | 6659522 | Misericordia (628105) 68 – Penn College (628146) 71 |
| D-III, 2026-11-14 | 6660271 | Edgewood (628046) 80 – Calvin (628182) 91 |
| D-III, 2026-11-14 | 6659192 | Manchester (628058) 62 – Ohio Northern (627940) 68 |

## For each game, save 4 pages in the browser

Use "Save page as → Webpage, HTML only" into `ankit/`, named by contest so files
cannot be mixed up:

| Page | URL | File name |
|---|---|---|
| Individual stats | `https://stats.ncaa.org/contests/{contest}/individual_stats` | `{contest}_individual_stats.html` |
| Box score | `https://stats.ncaa.org/contests/{contest}/box_score` | `{contest}_box_score.html` |
| Roster, first team | `https://stats.ncaa.org/teams/{team_id}/roster` | `roster_{team_id}.html` |
| Roster, second team | `https://stats.ncaa.org/teams/{team_id}/roster` | `roster_{team_id}.html` |

Check that each roster file shows the player table, not the team overview page (the
UChicago mistake). That makes 60 page saves in all, at a normal browsing pace.

## Then add each game to `config/ncaa_games.json`

```json
{
  "game_id": "6657429",
  "label": "D-I scoreboard: Florida A&M - UTEP, 2026-11-16",
  "output": "data/processed/ncaa-game-6657429",
  "scoreboard": ["ankit/d1_scoreboard.html", "https://stats.ncaa.org/season_divisions/18983/livestream_scoreboards?game_date=11%2F16%2F2026&conference_id=0"],
  "individual_stats": ["ankit/6657429_individual_stats.html", "https://stats.ncaa.org/contests/6657429/individual_stats"],
  "box_score": ["ankit/6657429_box_score.html", "https://stats.ncaa.org/contests/6657429/box_score"],
  "rosters": [
    ["ankit/roster_627521.html", "https://stats.ncaa.org/teams/627521/roster"],
    ["ankit/roster_627325.html", "https://stats.ncaa.org/teams/627325/roster"]
  ]
}
```

Then run `build-ncaa-games --plan config/ncaa_games.json` followed by
`combine-ncaa-games`.

## Division and conference for the 30 new teams

The pipeline gives no team a division or conference without a source-backed entry
in `config/ncaa_team_affiliations.json`. Until entries exist, the new rows have blank
`division` and `conference`, with `missing_division` / `missing_conference` warnings
(exit code 2). The statistics and the 57 checks are unaffected.

Each entry needs that team's NCAA Directory `orgDetail` page for 2026-2027, plus one
other official page. To find the directory ID, save the team page
`https://stats.ncaa.org/teams/{team_id}` as well; it links `org_id={id}`. robots.txt allows the `/directory/` pages on `web3.ncaa.org`, but
fetching them is a network request and needs separate approval; they can also be
saved by hand like the stats pages.
