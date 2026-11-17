# Stage 3 — Local vs. cloud accessibility test plan

Status: **plan approved in principle. The probe and GitHub Actions workflow are built
and tested offline. No Stage 3 network request has been sent; each run needs separate
approval.**
Prepared 2026-11-17. Season in scope: 2026-27 men's basketball.

## 1. What has already been tested

All earlier tests ran from one environment, this Windows laptop (`local-windows`).
They were logged with the project's `diagnose` command.

| When (UTC) | Request | Result | Time | Log |
|---|---|---|---|---|
| 2026-11-17 04:30 | `stats.ncaa.org/robots.txt` | HTTP 200, text/plain, 202 bytes | 0.93 s | `logs/ncaa-source-diagnostics.jsonl` |
| 2026-11-17 04:30 | `stats.ncaa.org/contests/5731445/box_score` | **HTTP 403** (406-byte HTML) | 0.85 s | same |
| 2026-11-17 04:30 | `stats.ncaa.org/contests/scoreboards?division=1&sport_code=MBB` | **HTTP 403** (397-byte HTML) | 0.85 s | same |
| 2026-11-17 04:39 | same box score, after robots-first checking was added | not requested: robots disallow | – | `logs/ncaa-policy-diagnostics.jsonl` |
| 2026-11-17 07:53 | D-II and D-III scoreboards (18984, 18985) | not requested: robots disallow (robots.txt 200, 1.04 s / 0.82 s) | – | `logs/ncaa-d2-d3-access-2026-11-17.jsonl` |
| 2026-11-17 07:38–08:40 | `web3.ncaa.org` robots.txt and 5 NCAA Directory `orgDetail` pages | robots allows `/directory/`; all HTTP 200 (0.5–0.8 s) | – | files and SHA-256 in `data/raw/affiliations/`, `config/ncaa_team_affiliations.json` |
| 2026-11-17 07:40–08:40 | 5 school athletics schedule pages (robots checked) | all HTTP 200 (1.4–2.4 s) | – | same |

What these results mean:

- **stats.ncaa.org disallows automated access** in its robots.txt (`User-agent: *`).
  The client now stops before requesting any disallowed page. The two 403s above came
  from requests made before that check existed; they will not be repeated.
- Every real stats page used in Stages 1–2 was **saved by a person in Chrome**. That is
  ordinary browsing, not automated collection.
- **No cloud test has been run.** The `--environment` label is only a label and does
  not prove where a request came from.

The three validated CSVs, which must not change, have these baseline SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `data/processed/ncaa-game-6643366/…_LOCAL_PREVIEW.csv` | `363d7b4e…1149f72` |
| `data/processed/ncaa-game-6631754/…_LOCAL_PREVIEW.csv` | `d27f590b…f80f6c4f` |
| `data/processed/ncaa-game-6617400/…_LOCAL_PREVIEW.csv` | `a7f1b23b…c69fac178` |

## 2. The key constraint

A cloud server does not get permission that a laptop lacks. stats.ncaa.org's robots.txt
disallows automated access for **every** user agent, so a script in the cloud must stop
at the same point as the local one. "Local vs. cloud" therefore cannot mean "which one
can download NCAA stats pages". It can only mean:

1. **Is the policy the same from both places?** Fetch robots.txt from each environment
   and compare its status, size and SHA-256 hash.
2. **Are both environments treated the same on pages we are allowed to request?** Cloud
   IP addresses are often handled differently by bot protection (for example, HTTP 403
   or a challenge page from the cloud but 200 from home).
3. **How do speed and reliability differ?** Compare response times, timeouts,
   connection, TLS and DNS errors, and redirects between the two.

## 3. Rules for every test (safety)

- Check robots.txt first for **each host**, and request only paths it allows. If
  robots.txt can't be read, treat the host as not permitted.
- **One attempt** per request. At most one retry, and only for 502/503/504 responses.
  Stop at once on 401, 403 or 429, and never retry them.
- If a server sends `Retry-After`, honour it by stopping. Wait **at least 10 seconds**
  between requests to the same host.
- Never solve, skip or work around CAPTCHAs, "verify you are human" pages, logins or
  bot protection. If one appears, record `access_challenge` or `login_required` and stop.
- No cookies, accounts, proxies or VPNs, and no rotating IPs or user agents. Requests
  identify themselves honestly as `BasketballDataResearch/0.1`.
- Do not log credentials, cookies or private query values. Do not record IP addresses;
  use an operator label such as `cloud-<provider>-<region>` instead.
- Testing only reads and logs. It never writes to `data/processed/` or `data/exports/`,
  and the three CSVs keep their `LOCAL_PREVIEW` status.

## 4. What can be tested, and where

| Test | Local Windows | Cloud | Notes |
|---|---|---|---|
| `stats.ncaa.org/robots.txt` (status, hash, time) | Yes | Yes | robots.txt itself may always be read |
| stats.ncaa.org stats pages (scoreboards, box scores, rosters) | **No** — logged as "not requested: robots disallow" | **No** — same | Policy-level result in both places |
| `web3.ncaa.org/robots.txt` and the 5 known `orgDetail` pages | Yes | Yes | `/directory/` is allowed by its robots.txt |
| The 5 school athletics schedule pages already used as evidence | Yes | Yes | Only after a per-host robots check |
| Manual browser saving of stats pages | Done (Stages 1–2) | Not planned | A person browsing, not an automated test |

That makes about 12 URLs per environment. Each will be tried **3 times, at least 10
minutes apart**, for about 36 requests per environment.

### What needs a cloud environment

You need to provide or approve one, for example a small cloud VM, GitHub Codespaces or
a GitHub Actions runner. It needs:

- Python 3.10+, `pip install -r requirements.txt`, and a copy of this repository.
- Outbound HTTPS only. Nothing to deploy, no Docker, no inbound ports and no secrets.
- An environment label you choose, for example `cloud-github-actions-us-east`. The
  real provider and region are written down by you, not guessed.

## 5. How results are recorded

Each request appends one line to a JSONL log. The existing `diagnose` format already
contains these fields:

| Field | Meaning |
|---|---|
| `environment` | Operator label (`local-windows`, `cloud-…`) |
| `checked_at` | UTC timestamp |
| `requested_url`, `final_url`, `redirect_history` | What was asked for and where it ended up (private query values redacted) |
| `outcome` | `html_available`, `robots_disallowed`, `forbidden`, `rate_limited`, `access_challenge`, `login_required`, `timeout`, `connection_error`, `tls_error`, `not_found`, … |
| `status_code`, `content_type`, `response_bytes`, `body_sha256` | What came back |
| `attempts[].elapsed_seconds`, `elapsed_seconds` | Response time per attempt and in total |
| `error_category`, `error_type` | For network failures; the exception's text is not logged |
| `retry_after_present`, `target_requested` | Backoff signals, and whether robots stopped the request |

Before running, two small additions are proposed. Both would be reviewed with you first.

1. **Wider host allowlist.** `diagnose` only allows `stats.ncaa.org` today. It needs a
   robots-checked allowlist that adds `web3.ncaa.org` and the 5 school athletics hosts.
2. **A `probe` command.** It reads a fixed URL list (`config/access_probe_urls.json`),
   applies the spacing and stop rules above, and writes
   `logs/access-probe-<environment>-<date>.jsonl`. It also writes a summary CSV per run:
   environment, host, URL, outcome, status, median and worst response time, and the
   robots.txt hash.

**Comparison report:** a side-by-side table of local vs. cloud for each URL, covering
outcome, status, median response time, error counts and whether the robots.txt hashes
are identical. Anything the cloud is treated differently on (for example 403 in the
cloud but 200 locally) is reported as an observation. It is not something to work
around.

## 6. Order of work (after your approval)

1. Fetch robots.txt for each host from local Windows: one request per host.
2. Review the allowlist and `probe` command changes with you, then add tests (offline,
   using a mocked network).
3. Run the local probe: 3 rounds, about 36 requests.
4. You set up the cloud environment, and the same command runs there with a cloud label.
5. Write the comparison report, and confirm the three CSV hashes are unchanged.

## 7. What will not be done

- No attempt to download stats.ncaa.org stats pages from any environment.
- No CAPTCHA solving, header spoofing, proxy or IP rotation, or headless-browser
  automation of NCAA pages.
- No change to the validated CSVs, no conversion to authorized exports, and no invented
  `permission_reference`.

## 8. Implementation (offline-tested; not yet run)

- `config/access_probe_urls.json`: the explicit allowlist (7 hosts, 12 URLs, 3 rounds,
  10 s per-host spacing, 600 s between rounds). It allows at most 43 requests, against a
  hard cap of 60.
- `src/common/probe.py` and `python -m src.main probe`:
  - **Dry run by default.** It sends requests only with `--execute`.
  - Reads robots.txt per host first. A file that can't be read means the host is not
    crawled; a 404 (no robots file) means no restrictions.
  - Makes exactly one attempt per request, and stops a host on 401, 403, 429,
    `Retry-After`, a challenge/CAPTCHA page or a login page.
  - Clears cookies after every request, and records only allowlisted runner variables.
  - Refuses to write anywhere inside `data/`.
- Output: `<output>/access-probe-<environment>-<run_id>.jsonl` (run start, robots
  results, one line per URL per round, run end) plus `…-summary.csv`.
- `.github/workflows/access-probe.yml`:
  - Manual `workflow_dispatch` only, with `contents: read` permissions.
  - GitHub-hosted `ubuntu-latest`, region recorded as `unknown`.
  - Runs the offline tests and a dry run, then runs the real probe only if the input is
    exactly `APPROVED`.
  - Uploads the logs as a run artifact.

Commands, to be run only after approval:

```powershell
# Local, dry run (no network):
.\benv\Scripts\python.exe -m src.main probe --environment local-windows --output logs/probe
# Local, real run (after approval):
.\benv\Scripts\python.exe -m src.main probe --execute --environment local-windows --region unknown --output logs/probe
```

Cloud: push the repository to GitHub. Then go to **Actions → Stage 3 access probe
(manual) → Run workflow** and type `APPROVED` only when that run has been approved.
