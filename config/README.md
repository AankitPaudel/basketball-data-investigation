# Offline NCAA input contract

This is our normalized file format, **not an NCAA API format**. Start from
`ncaa_bundle.template.json`; the complete fictional example is
`tests/fixtures/ncaa_enrichment_synthetic.json`. No real sample is bundled.

- Top level: `schema_version: 1`, `season` (YYYY-YY), optional observed
  `ncaa_season_id`, `sport` (MBB/WBB), timezone-aware `extracted_at`, and
  `provenance` (synthetic/authorized_source).
- Real inputs require `permission_reference`. Reviewed real exports additionally
  require `verification_reference` describing the manual/source comparison.
  These are operator attestations; the code cannot establish permission or truth.
- Each game: source `game_id`, `status: final`, ISO `game_date`,
  `boxscore_source_url`, exactly two `teams`, and `players`.
- Each team: `team_name`, source `team_id` if available, `division` (I/II/III/null),
  and `final_score` if available. Optional `team_points_adjustment` requires a
  `team_points_adjustment_reason`; this is only for a documented source exception.
- Each player: matching `team_id` (or `team_name` when both records lack an ID),
  `player_id` if available, `player_name`, optional `jersey_number`, `minutes`,
  `participation` (did_not_play/unknown), and a `stats` object.
- Statistics use the existing parser's keys: `pts`, `fgm`, `fga`, `fg3m`, `fg3a`,
  `ftm`, `fta`, `oreb`, `dreb`, `reb`, `ast`, `stl`, `blk`, `tov`, `pf`.
  Values must be integers or null. No unverified automatic string coercion occurs.
- Each roster: `season`, `sport`, `team_id` (or `team_name`), `source_url`, and
  `players` containing `player_id`, `player_name`, `jersey_number`, `position`,
  `height`, `class_year`, and `hometown` where available. All biodata is optional.
- Each conference record: `season`, `sport`, `team_id` (or `team_name`),
  `conference`, and `source_url`. Missing/ambiguous sources remain flagged.

Height accepts explicit formats such as `6-2`, `6'2"`, `198 cm`, or `1.98 m`;
unitless or malformed input is flagged and left null. JSON output retains the
original height and player fields. Matching preserves suffixes and diacritics;
there is no fuzzy match or silent fallback between conflicting source IDs.

For authorized saved HTML, replace a game's `players` with `boxscore_file`, or a
roster's `players` with `file`, relative to the manifest. This reuses the existing
provisional flat-table parsers; it does not implement a newly verified NCAA layout.
Game team captions must exactly match manifest team names. The HTML roster path
currently extracts only name/ID/jersey/position; additional biodata requires
verified normalized records until a real source adapter can be tested.

CSV order is defined by `src/ncaa/pipeline.py:COLUMNS`. Source metadata such as
season, teams, IDs, scores, and conference evidence must be obtained and reviewed
externally; the workflow does not invent them or discover them from blocked pages.
Keep real inputs in ignored `data/inputs/` and permitted captures in `data/raw/`.
Run with a single sport/season per bundle; multiple games are consolidated in that
bundle. Each output directory is replaced per run, so use separate directories
for separate bundles. Per-file writes are atomic; simultaneous writers to one
directory are unsupported.
