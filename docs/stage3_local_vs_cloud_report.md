# Stage 3 — Local vs. cloud accessibility report

Season in scope: 2026-27 men's basketball. Both runs took place on 2026-11-17 and used
the same probe configuration and code (`python -m src.main probe --execute`; plan in
`docs/stage3_access_test_plan.md`).

## Summary

- **Every permitted page loaded in both environments.** These 30 pages were *not* NCAA
  statistics pages: they were 5 NCAA Directory pages (`web3.ncaa.org`) and 5 school or
  conference schedule pages, each requested in 3 rounds. 30 of 30 returned HTTP 200
  locally and 30 of 30 in the cloud. Neither run had a timeout, connection error,
  redirect, rate limit or CAPTCHA page on these sites.
- **stats.ncaa.org behaved differently.**
  - *Local Windows:* robots.txt was readable (HTTP 200, plain text, 202 bytes) and
    disallows the two stats URLs for automated clients. They were recorded as
    `robots_disallowed`.
  - *GitHub Actions:* the request for robots.txt itself was refused with HTTP 403. The
    reply was a 382-byte HTML page that the probe identified as a challenge page. The
    probe treated the policy as unreadable, stopped the host, and recorded the two
    stats pages as `skipped_host_stopped`.
- **No NCAA statistics page was requested in either run.** The two stats.ncaa.org URLs
  are also marked "policy check only", so they could never have been requested, even if
  robots.txt had allowed them.
- **The cloud runner was faster:** median page time 0.33 s against 0.44 s locally, and
  median robots.txt time 0.17 s against 0.98 s. The runs were eight hours apart,
  though, so time of day may account for part of the difference.
- The three validated CSVs were not touched and remain `LOCAL_PREVIEW`.

## Environments

| | Local | Cloud |
|---|---|---|
| Label | `local-windows` | `cloud-github-actions-ubuntu` |
| Machine | Windows 11 laptop | GitHub-hosted runner: Linux X64, image `ubuntu26` version `20261111.196.1`, kernel `7.0.0-1019-azure` |
| Region | `unknown` (not recorded) | `unknown`; GitHub does not publish the runner's region |
| Run ID | `20261117T091002Z-139ccdba` | `20261117T170914Z-caec4a2d` (GitHub run `44133732954`, attempt 1) |
| Time (UTC) | 09:10:02 – 09:32:14 | 17:09:14 – 17:31:25 |
| Python / requests | 3.13.5 / 2.34.2 | 3.13.18 / 2.34.2 |
| Config SHA-256 | `a551f0cb…` (CRLF line endings) | `df345497…` (LF line endings) |

The two config hashes differ only because of line endings. Converting the local file to
LF gives `df345497…`, which matches the committed copy that the runner checked out. The
configuration content was identical.

## Accessibility, side by side

| Site and pages | Local outcome | Local status codes | Cloud outcome | Cloud status codes |
|---|---|---|---|---|
| stats.ncaa.org — `robots.txt` | readable; disallows the probe URLs | 200 (text/plain, 202 B) | **refused, challenge page** | **403** (text/html, 382 B) |
| stats.ncaa.org — D-I scoreboard, contest 6643366 individual stats | `robots_disallowed`, not requested (×3 rounds) | – | `skipped_host_stopped`, not requested (×3 rounds) | – |
| web3.ncaa.org — NCAA Directory, 5 `orgDetail` pages | accessible | 200 ×15 | accessible | 200 ×15 |
| ferrumpanthers.com — Ferrum schedule | accessible | 200 ×3 | accessible | 200 ×3 |
| fightingfalcons.com — Fairmont State schedule | accessible | 200 ×3 | accessible | 200 ×3 |
| athletics.bowdoin.edu — Bowdoin schedule | accessible | 200 ×3 | accessible | 200 ×3 |
| athletics.uchicago.edu — UChicago schedule | accessible | 200 ×3 | accessible | 200 ×3 |
| atlantic10.com — A-10 schedule release | accessible | 200 ×3 | accessible | 200 ×3 |
| **Totals** | 37 requests (7 robots + 30 pages); 0 errors; no host stopped | | 37 requests (7 robots + 30 pages); 0 errors; stats.ncaa.org stopped after its robots 403 | |

**robots.txt files:** the six other hosts returned identical robots.txt files in both
environments (the SHA-256 hashes match). The three Sidearm-hosted school sites share one
file (`454315be…`).

## Response times (seconds)

Each time covers the whole request, including downloading the full page; schedule pages
are 0.27–0.85 MB.

| Site | Local median | Local min–max | Cloud median | Cloud min–max |
|---|---|---|---|---|
| web3.ncaa.org (15 requests) | 0.248 | 0.206–1.254 | 0.211 | 0.126–0.447 |
| ferrumpanthers.com | 1.301 | 0.439–1.607 | 0.556 | 0.494–0.713 |
| fightingfalcons.com | 1.272 | 0.520–1.492 | 0.317 | 0.316–1.250 |
| athletics.bowdoin.edu | 1.084 | 0.283–1.342 | 0.475 | 0.123–0.526 |
| athletics.uchicago.edu | 1.334 | 0.433–1.888 | 0.359 | 0.161–0.507 |
| atlantic10.com | 0.937 | 0.199–0.989 | 0.342 | 0.227–0.800 |
| **All 30 pages** | **0.436** (mean 0.701) | 0.199–1.888 | **0.330** (mean 0.371) | 0.123–1.250 |
| robots.txt (7) | 0.984 | 0.917–1.072 | 0.165 | 0.102–0.256 |

Median by round:

| Round | Local | Cloud |
|---|---|---|
| 1 | 0.297 | 0.207 |
| 2 | 1.010 | 0.355 |
| 3 | 1.121 | 0.375 |

Locally, rounds 2 and 3 were about three times slower than round 1. The cloud runner
stayed more consistent.

## Why stats.ncaa.org differed

- **Locally,** NCAA served its robots.txt, and the file disallows the probe URLs. That
  is a published policy decision, and the probe followed it.
- **From GitHub Actions,** NCAA's edge refused even the robots.txt request with HTTP 403
  and an HTML challenge page. The cloud request never reached the policy file, so the
  block happened before robots.txt could be read.
- **Cause:** the log does not show why the runner was blocked. The response is
  consistent with bot protection treating cloud-hosted IP addresses differently from a
  home connection. The earlier local tests (2026-11-17 04:30 UTC) had also received
  403s, for two stats pages requested before robots-first checking existed. The 403 body
  was not saved; only its SHA-256 hash (`70d355a6…`), size and challenge flag were.
- **Probe behavior:** it did what the plan requires. An unreadable robots.txt counts as
  "do not crawl", the 403 stopped the host for the rest of the run, and nothing was
  retried or worked around.

What this means for the project:

1. robots.txt is a published request to automated clients, not a technical lock.
   stats.ncaa.org's file (202 bytes, SHA-256 `bbc40843…`) disallowed both probe URLs
   for this project's user agent, under the `User-agent: *` rule recorded in the test
   plan. The project follows that request in both environments. (Only the file's hash
   was kept, not its text.) From the cloud runner, stats.ncaa.org also
   answered with HTTP 403, so moving the pipeline to GitHub Actions or a similar cloud
   host would not change that.
2. The NCAA pages used in Stages 1–2 were saved by a person in a browser, and that
   remains the only way they are obtained. The pipeline processes saved files offline.
3. The NCAA Directory (`web3.ncaa.org`) and the school and conference sites, which
   supply the affiliation evidence, are reachable and permitted from both environments.

## Safety controls, checked in the logs

| Control | Evidence |
|---|---|
| robots.txt read first for every host | 7 `robots` records before any page request, in both runs |
| No stats.ncaa.org page requested | 0 page attempts on stats.ncaa.org in both logs; both URLs are marked `policy_only` |
| Unreadable robots.txt means do not crawl | Cloud: `unavailable_fail_closed` after the 403 |
| Stop a host on 401/403/429/challenge | Cloud: `stopped_hosts: {"stats.ncaa.org": "forbidden"}`; the 6 later URLs were skipped without being sent |
| One attempt per request, no retries | Every one of the 37 requests in each run has exactly 1 attempt |
| ≥ 10 s between requests to the same host | Smallest gap is 10.0 s in both runs (local paused 18 times, cloud 13) |
| ~10 min between rounds | Local rounds at 09:10, 09:20, 09:31; cloud rounds at 17:09, 17:20, 17:30 |
| Honest identity, no cookies, no credentials | User-Agent `BasketballDataResearch/0.1`; cookies cleared after each request; no token, cookie or authorization strings in either log |
| Only harmless runner details recorded | Cloud log records only the allowlisted `RUNNER_*`, `ImageOS`/`ImageVersion` and `GITHUB_*` run identifiers; no IP addresses |
| Cloud run only with approval | Workflow is manual (`workflow_dispatch`) and sends requests only when its input is `APPROVED` |
| Validated CSVs untouched | SHA-256 before and after: `363d7b4e…`, `d27f590b…`, `a7f1b23b…` (unchanged) |

## Limitations

- **One run per environment, at different times of day** (09:10 vs. 17:09 UTC). The
  speed comparison is indicative, not a benchmark.
- **Runner region unknown.** GitHub does not publish it, and the probe does not look up
  IP addresses or geolocation. Only one GitHub-hosted runner was sampled, so other
  runners or cloud providers may be treated differently.
- **Timing is one total per request.** The logs do not split it into DNS, connection,
  TLS and download time.
- **The NCAA 403 page was not saved,** so which bot-protection product served it, and
  why, is not confirmed.
- **Some pages change slightly between requests.** Sizes differed by a few bytes
  between rounds and environments (for example, NCAA Directory pages by 1–2 bytes and
  the A-10 page by about 45 bytes), so page hashes are not compared.
- **The CI runner's own traffic was not logged.** Fetching the code and installing
  packages from PyPI are not part of the probe log.
- **Probe logs stay local.** `logs/` is git-ignored. The workflow's GitHub actions are
  referenced by version tag (`@v4`, `@v5`), not pinned to commit hashes.
- **Permission is unchanged.** These results say nothing about permission to *use* NCAA
  data. All CSVs remain local validation previews with no `permission_reference`.

## Source files

| File (in `logs/probe/`) | SHA-256 |
|---|---|
| `access-probe-local-windows-20261117T091002Z-139ccdba.jsonl` | `8e91f7612cca905baa0beab309c8c0980d4cfafdd0cc642c5a8bfde0d37086d7` |
| `access-probe-local-windows-20261117T091002Z-139ccdba-summary.csv` | `1c84a3501b868aa7e6a13762b1e9a53ddd3e9f040f6d902bceaaec217abd6ada` |
| `access-probe-cloud-github-actions-ubuntu-20261117T170914Z-caec4a2d.jsonl` | `97c859fdd6f5842e6149cb97b7b83daa020cf17e3c3cd5e92f15b2e5a141730e` |
| `access-probe-cloud-github-actions-ubuntu-20261117T170914Z-caec4a2d-summary.csv` | `922ad2f7b95b9bf3284d71b7bed8a91239f57208e54f376033b7b882fd69c276` |
