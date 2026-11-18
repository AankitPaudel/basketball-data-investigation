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
- **Line-ending finding and fix:** on Windows, `write_atomic` wrote in text mode, so the
  CSV writer's LF line endings became CRLF on disk, and file hashes differed from
  Linux. It now encodes the text itself and writes bytes, as `combine-ncaa-games`
  already did, so files are LF everywhere and rebuilt files match byte for byte. On
  2026-11-18 the three validated CSVs were deliberately converted from CRLF to LF.
  Only line endings changed: the content and each `quality_report.json` `csv_sha256`
  (always the LF content hash) are the same, and the new on-disk hashes equal it.
  New baseline:

  | Game | Rows | Old on-disk SHA-256 (CRLF, before 2026-11-18) | New on-disk SHA-256 (LF) = `csv_sha256` |
  |---|---|---|---|
  | 6643366 | 30 | `363d7b4ef70f67241e33081c2647eb724389b4e2807a985f750b5d5af1149f72` | `b964bb2dc4f6f8b6c72ea3d86ccc7963fa7778b2f1e2321e0ff770731028e4e9` |
  | 6631754 | 23 | `d27f590b1ce3913feb9e752d60c815587adc418deeb50e0859a95270f80f6c4f` | `c4a24d7f36d4cebaaab83f2ec7686b536e3f0ef4ebcf351d883ff166941b2f3d` |
  | 6617400 | 24 | `a7f1b23b26d4587592cb8839a53c86b707d30804b1e448b8321c1e5c69fac178` | `525bfea27869ea3e2588eaf4bed020073e07ecf5e565caffb509802c48b5999f` |
- **Combined output:** `combine-ncaa-games` merges the per-game CSVs into one file
  with a column dictionary. It refuses any CSV whose content no longer matches its
  quality report.

## 8. What is still open

- **No permission reference:** every output is a `LOCAL_PREVIEW` and cannot go to
  `data/exports/`.
- **Three games only:** more need saving by hand (see `docs/more_games_save_list.md`).
- **Fresh install:** verified only in the cloud. GitHub Actions run 44234640697 installed
  `requirements-tested.txt` into a new environment and passed every test. A local
  fresh install still needs PyPI access, which has not been approved.

## 9. Automated collection from permitted school sites (approach 2)

**Why:** stats.ncaa.org disallows automated access, and from the cloud it answered
with a 403 challenge page (sections 1 and 6). The schools' own athletics sites publish
box scores too, and Stage 3 showed they answer normal requests from both environments.
So the second approach collects from those sites, but only where their robots.txt
allows it.

**robots.txt check (2026-11-18, one request per host):**

| Host | Box score and stats pages | Crawl-delay | Notes |
|---|---|---|---|
| athletics.bowdoin.edu | allowed | 30 s | – |
| athletics.uchicago.edu | allowed | 30 s | same file as Bowdoin |
| fightingfalcons.com (Fairmont State) | allowed | 30 s | same file as Bowdoin |
| ferrumpanthers.com | allowed | 30 s | nearly the same rules |
| atlantic10.com | allowed | 5 s | `Visit-time: 0100-0645`, read as UTC |

- **Disallowed on every host:** `/services/`, `/admin/`, `/images/`, scripts,
  stylesheets and print views (`*print=true*`). None of these is a box score address.
- **Stage 3 broke two of these rules without noticing:** it waited only 10 s on the
  four 30 s hosts, and both of its runs (09:10 and 17:09 UTC) visited atlantic10.com
  outside that site's window. The new fetcher honors both rules.

**Crawl-delay bug, found and fixed.** The first Bowdoin fetch was split across two
runs, and each run started its own timer. The second run's first request went out
28.1 s after the previous run's last one, about 2 s short of the 30 s delay.

- **Fix:** `src/school/pacing.py` keeps each host's last request time in
  `logs/host-pacing-state.json`. Every run now waits for whatever remains of
  max(10 s, Crawl-delay).
- **Test:** `tests/test_school_pacing.py` reproduces the case: a new run started 2 s
  after a request waits the remaining 28 s.

**Fetcher and command.** `fetch-school-boxscore`:

- **Order:** robots.txt, then the schedule page, then the one box score that matches
  the opponent and date.
- **Checks:** every address is checked against the wildcard rules and the Visit-time
  window before it is requested. A disallowed page is refused and logged as never
  requested.
- **Requests:** each is tried once. The fetcher stops on 401, 403, 429, challenge pages
  or network errors.
- **Default is a dry run:** without `--execute`, it only prints the plan. With
  `--execute`, it parses the page and writes the same four comparison files.

**Parser approach.** The school site (Sidearm platform) has no NCAA player ID.

- **Rows:** each row keeps the jersey as shown (`05`), the name normalized to
  "First Last" (including `Lapsiwala,Arhan` with no space, and `Baylor, Jr., Corey`
  with a suffix), whole minutes, and the 15 counting stats.
- **No NCAA ID:** `player_id` is always empty and `player_match_status` is
  `school_site_no_ncaa_id`.
- **Matching:** rows are matched to NCAA rows by team + jersey + normalized name, with
  no fuzzy matching.
- **Checks:** the TEAM and Totals rows are kept for the same kind of cross-check as the
  NCAA `source_checks`. All 34 pass on the Bowdoin page.

**Result: Bowdoin at UChicago, 2026-11-14 (contest 6617400), from Bowdoin's site.**

- 23 of the 24 NCAA players matched.
- 345 of 345 stat values in the matched rows agree exactly.
- Both TEAM rows and both final scores (63 and 87) are identical.
- No player appears only on the school site.

**One unresolved identity: Bowdoin #17.** NCAA spells the name "Bendek Maly" on all
three of its pages; the school site has "Maly, Benedek". If he is the same player, all
15 of his stats agree as well. He is not merged, and is reported as an open question,
because nothing here shows which spelling is correct.

**Matched despite looking different:**

- **Jerseys:** 7 differ only by a leading zero (NCAA `5`, school `05`). Matthew Sherf is
  `0` in NCAA and `00` on the school site. In principle those are different numbers,
  so his row is flagged separately; his name and stats agree.
- **Minutes:** the school site shows whole minutes, NCAA shows MM:SS. The school value
  equals the rounded NCAA time 19 times, the rounded-down time once and the rounded-up
  time 3 times. That is not one consistent rule, probably because each team's minutes
  must total 200. So minutes are reported but not counted as a stat difference.

**What this does and doesn't prove.**

- **Proven:** the fetcher, parser and comparison work for one Bowdoin page on the
  Sidearm layout.
- **Not yet tested:** Fairmont State, Ferrum and UChicago's own site. Their robots.txt
  rules match Bowdoin's (Ferrum's file differs slightly), but none of their box score
  pages has been fetched or parsed, so it is unconfirmed whether the same parser works
  on them.
- **Not a permission:** school-site rows are a cross-check source, not an authorized
  export.
