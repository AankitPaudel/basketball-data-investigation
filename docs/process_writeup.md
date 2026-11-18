# How this project got here: what was tried, what failed, and why

**Goal:** player-level 2026-27 men's basketball box scores from all three NCAA
divisions, with player biodata, division and conference, in one validated CSV format,
plus a test of whether collection works the same locally and in the cloud.

## 1. Direct requests to stats.ncaa.org: stopped

- **Tried:** an early diagnostic on the laptop (2026-11-17, 04:30 UTC) requested a
  historical contest box score and a scoreboard from stats.ncaa.org. It fetched
  robots.txt but did not yet enforce it.
- **Result:** both pages returned HTTP 403. robots.txt disallows these pages for
  automated clients.
- **Decision:** the HTTP client now reads robots.txt first and stops before any
  disallowed page. Those two requests were the last made to an NCAA stats page.
  Headless browsers, rotating IPs, other user agents and anything else that gets
  around the block were ruled out.

## 2. Saving pages by hand, parsing offline: adopted

- **Approach:** a person opens each page in a browser and saves it into `ankit/`. The
  parsers read only those files.
- **Why:** this is ordinary browsing, it respects the robots request, and it gives
  exact, repeatable inputs. Each saved file is hashed, and sanitized copies (scripts
  and tokens removed) are committed as test fixtures.
- **Built for the real layouts:** the parsers check the exact table headers, so a
  layout change stops processing instead of producing wrong numbers.

## 3. Validation: 57 checks per game

- **Problem:** a parser can be wrong without crashing.
- **Approach:** every game is cross-checked between pages:
  - player sums plus the TEAM row against the totals row, for every statistic;
  - totals against the box-score Team Stats panel;
  - points against the contest header and the scoreboard;
  - team minutes against 200:00.
- **Result:** all three games pass 57 of 57. All 77 players matched their roster by
  NCAA player ID.

## 4. Division and conference: not taken from stats.ncaa.org

- **Considered:** using the scoreboard's division filter.
- **Why it was rejected:** Ferrum (Division II) appears on the Division I scoreboard
  because it played George Washington. The filter describes the list, not the teams.
- **Adopted:** a mapping file backed by the NCAA Directory sport row, plus a second
  official page for each team.
- **Ferrum quirk:** the institution is Division III, reclassifying to II, while its
  basketball team is listed as II. Both values are kept, and the rows get a warning.
- **On real data:** contest 6643366 is on the D-I scoreboard, but its 30 rows split
  into 14 Division I (George Washington) and 16 Division II (Ferrum, reclassifying).

## 5. Mistakes caught by the checks

- **Wrong UChicago page:** the first "UChicago roster" file was the team overview page.
  The roster parser found no roster table, so the page was re-saved.
- **Hidden status:** the scoreboard's "Final" sat inside an HTML comment. The parser
  accepts it but records where the value came from.

## 6. Local vs. cloud test (Stage 3)

- **Design:** one probe run in both environments: an allowlist of 7 hosts, robots.txt
  first, 10 s between requests to the same host, 3 rounds 10 minutes apart, stop on
  any block. The two stats.ncaa.org URLs are marked "policy check only" and are never
  requested.
- **Result:**
  - All 30 permitted pages (NCAA Directory and school or conference sites) loaded in
    both environments.
  - stats.ncaa.org served robots.txt to the laptop but answered the cloud runner with
    a 403 challenge page.
  - Neither run requested a stats page.
- **Conclusion:** the cloud offers no extra access, so hand-saved pages stay the only
  source for NCAA statistics.

## 7. Repeatability

- **One command:** `build-ncaa-games --plan` rebuilds every game.
- **Same output across platforms:** rebuilding from the sanitized fixtures, with each
  original timestamp, gives the same CSV content hashes as the validated outputs. The
  rebuild-check workflow repeats this on a GitHub Ubuntu runner.
- **Line-ending finding:** on Windows the CSV files were written with CRLF line endings,
  so their on-disk hash differed from Linux. The writer now uses LF on every platform,
  so rebuilt files match byte for byte. The three validated CSVs predate the fix and
  are kept as they are (CRLF), so the comparison uses the LF content hash recorded in
  `quality_report.json`, which is unchanged.
- **Combined output:** `combine-ncaa-games` merges the per-game CSVs into one file
  with a column dictionary. It refuses any CSV whose content no longer matches its
  quality report.

## 8. What is still open

- **No permission reference:** every output is a `LOCAL_PREVIEW` and cannot go to
  `data/exports/`.
- **Three games only:** more need saving by hand (see `docs/more_games_save_list.md`).
- **Fresh install not run:** a clean install from `requirements-tested.txt` needs PyPI
  access, which has not yet been approved locally. The rebuild-check workflow performs
  one in the cloud.
