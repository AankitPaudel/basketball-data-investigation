# Stage 3 — Local vs. cloud accessibility test plan

For the current one-request diagnostics commands, see the [Task 1B execution runbook](#task-1b-execution-runbook).

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

The current Actions workflow has a narrower scope. Use the [Task 1B execution runbook](#task-1b-execution-runbook).

## Phase 2 — Diagnostic experiments

The preceding sections preserve the original Stage 3 plan. Phase 2 uses archived
records first and scopes any approved live run to one GET of
`https://stats.ncaa.org/robots.txt` per approved environment. Agree on the comparison
pair before any HTTP request, use the same revision and pinned dependencies, and
schedule the captures close together in UTC. Use the ordinary network configuration
and the existing research identity. Each environment stops after its first response or
error. If a denial, challenge or rate limit arrives before the second capture has
started, leave that capture pending until the administrator advises on the comparison.

| ID | Objective and environment | Controls | Variable under review | Authorization | Expected observation | Actual observation and evidence | Status |
|---|---|---|---|---|---|---|---|
| E01 | Reconstruct the archived local and GitHub Actions baseline. | Same endpoint, configured GET/UA/Accept, Requests version and equivalent plan. | Environment, timing, status, type, redirect, body hash and duration. | Existing local files. | Establish whether status or response shape differs. | 200 local and 403 cloud, one attempt each, no redirects. Stage 3 probe JSONL, line 2 in both. | Complete, offline. |
| E02 | Verify the existing local and cloud control requests and policy stops. | Same control URLs and rounds. | Whether the difference extends to other hosts or was followed by extra stats requests. | Existing local files. | Controls succeed and policy exclusions stay unsent. | 30 HTTP 200 target requests per environment, six matching control robots hashes, zero NCAA stats targets sent. Stage 3 JSONL and summary CSVs. | Complete, offline. |
| E03 | Inspect available evidence for verification and provider indicators. | Same archived pair, legacy detector reviewed at the probe-era revision. | Definitive denial, verification clues and provider clues. | Existing local files. | A specific header, reference or response excerpt may distinguish the cases. | Legacy flag is broad. Body hashes and sizes survive; headers, bodies and references are absent. | Complete, offline. |
| E04 | Compare any already-captured permitted browser observation with the same endpoint and network. | Endpoint, nearby UTC time and authorized route. | Client class. | Review existing sanitized captures only. A new browser request would need separate approval and a comparison purpose. | Same-endpoint evidence may identify client-associated behavior. | Existing operator saves are stats pages, with no comparable robots response. | Archive checked, comparison unavailable. |
| E05 | Compare existing resolver and operator network records for both environments. | Same hostname, timestamps and matching evidence scope. | A/AAAA answers, routing, proxy applicability, provider or ASN. | Operator-provided records. A new DNS lookup requires approval under this plan. | Identify network differences with attributed evidence. | No historical resolver, peer, proxy or ASN records found. GitHub runner identity is recorded. | Archive checked, comparison unavailable. |
| E06 | Capture one policy response per explicitly approved local and GitHub-hosted environment. | Same URL, GET, code, dependency versions, honest headers and close UTC timing. | Environment, prepared headers, DNS/peer and response signatures. | Explicit approval for endpoints, environments and time window. | A repeated status split and additional metadata can narrow the candidates. | No new capture. | Proposed, approval required. |
| E07 | Request event attribution from the NCAA site administrator. | Archived UTC time, URL, UA, status and runner ID. | Handling tier, action, exact rule and policy. | Approval before sending an external inquiry. | Administrator can confirm the trigger if event data survive or sponsor a new capture. | Inquiry drafted in the report. | Proposed, unsent. |

### Offline reproduction

Run from the repository root with the existing environment. The 2026-11-17 JSONL
archives under `logs/probe/` are git-ignored. Use the original logs or copies with the
hashes in the [accessibility report](stage3_local_vs_cloud_report.md#source-files). The
[generated comparison](stage3_probe_comparison.json) is available on its own when those
archives are unavailable.

```powershell
$localProbe = 'logs/probe/access-probe-local-windows-20261117T091002Z-139ccdba.jsonl'
$cloudProbe = 'logs/probe/access-probe-cloud-github-actions-ubuntu-20261117T170914Z-caec4a2d.jsonl'
.\benv\Scripts\python.exe -m src.main compare-probes $localProbe $cloudProbe --output docs/stage3_probe_comparison.json
.\benv\Scripts\python.exe -m src.main probe --config config/ncaa_robots_probe.json --capture-diagnostics --environment local-windows
.\benv\Scripts\python.exe -m unittest discover -s tests -v
.\benv\Scripts\python.exe -m src.main build-ncaa-games --plan config/ncaa_games_fixtures.json
.\benv\Scripts\python.exe -m src.main combine-ncaa-games --plan config/ncaa_games_fixtures.json --output data/processed/fixture-rebuild/combined --expect-sha256 402921c1ad1bf0a74590ca360a972dc36eaf0ddb630e4a44651b17e9ec6df03f
```

`compare-probes LEFT RIGHT` reads one JSONL run per input and prints JSON to stdout
unless `--output` is supplied. Older logs are supported. `--url` selects which URL gets
attempt-level detail. Status/classification counts and source line references are
included for every endpoint. A `null` means the value is unavailable, and an empty
array can indicate an observed absence, such as a request without redirects. The
legacy `challenge_detected` flag is preserved as `legacy_challenge_flag`. It has no
effect on verification classification. The fixture rebuild normally exits 2 for the
known Ferrum warnings. Check that all three games give `rebuild_match: true` before
using the combined hash.

## Task 1B execution runbook

### 1. Prerequisites and authorization

Use the Windows laptop and one GitHub-hosted Ubuntu runner from the same approved
revision. Locally, use `git status --short` to check the probe code and config for
local edits, verify `git rev-parse HEAD`, and use the dependencies in
`requirements-tested.txt`. On GitHub the runner records `GITHUB_SHA`. The updated
workflow must be available on GitHub before its dry run can be verified there. The
workflow definition must exist on the default branch to receive manual dispatches.
[GitHub manual dispatch documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

Before executing, obtain explicit approval for both environments, one GET of
`https://stats.ncaa.org/robots.txt` per environment, the accompanying resolver snapshot,
the recorded metadata and the same two-minute UTC window. Ask the administrator to
agree to that window if the archived event cannot be correlated. Confirm the ordinary
network settings and that Requests has no site credentials configured for the endpoint.
Coordinate both captures as one preapproved pair before either starts. Confirm both
clocks are synchronized. A runner or laptop that misses the window needs a newly
agreed window. A successful dry run is preparation for that approval.

The scoped config fixes the URL, GET method, `BasketballDataResearch/0.1` user agent,
`Accept: text/html`, 20-second Requests timeout, zero retries, no redirects and a shared
JSONL schema. Match Requests and urllib3 versions in the two dry plans and verify the
configured request fields. The Python patch, OS, OpenSSL build, hosted runner image,
egress, resolver, edge route and proxy applicability may differ. Keep the usual network
settings in both environments. A driver or runner change introduces another variable.

### 2. Local dry run and passive checks

From the Windows repository root, use the existing virtual environment.

```powershell
.\benv\Scripts\python.exe -m src.main probe --config config/ncaa_robots_probe.json --environment local-windows --capture-diagnostics
$ipv4 = [System.Net.NetworkInformation.NetworkInterfaceComponent]::IPv4
$ipv6 = [System.Net.NetworkInformation.NetworkInterfaceComponent]::IPv6
[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
    ForEach-Object { [pscustomobject]@{ State = $_.OperationalStatus; IPv4 = $_.Supports($ipv4); IPv6 = $_.Supports($ipv6) } } |
    Group-Object State,IPv4,IPv6 | Select-Object Name,Count
[DateTimeOffset]::UtcNow.ToString('o')
```

The dry run prints the OS, Python, OpenSSL, Requests and urllib3 versions, IPv6 socket
support, proxy environment flags and the request plan. It performs no DNS or HTTP I/O.
The interface summary shows local interface state. IPv6 socket support and interface
state cannot establish external IPv6 reachability. Proxy flags report environment
presence only. They do not identify which proxy, whether it was used, or whether Windows
or the hosting platform supplies an additional proxy. OpenSSL version identifies the
local library, while handshake details require separate evidence.

For a cache-only Windows view, use the following commands. A cache miss only shows that
the answer is unavailable from this cache. Review output privately before sharing.
[Microsoft Resolve-DnsName documentation](https://learn.microsoft.com/en-us/powershell/module/dnsclient/resolve-dnsname?view=win10-ps)

```powershell
Resolve-DnsName -Name stats.ncaa.org -Type A -CacheOnly
Resolve-DnsName -Name stats.ncaa.org -Type AAAA -CacheOnly
```

If a standalone resolver observation is authorized on either environment, use its
Python interpreter from the repository root. This command may query the environment's
resolver. It sends no HTTP request. The authorized cloud probe collects the same
snapshot immediately before GET. The GitHub dry run omits the lookup.

```text
python -c "import json; from src.common.http_client import resolve_destination; print(json.dumps(resolve_destination('stats.ncaa.org'), indent=2))"
```

The resolver snapshot distinguishes IPv4 and IPv6 answers, redacts private addresses
and reports resolver failures. Cached or different answers can reflect time, resolver
or edge selection. A result alone cannot establish the selected socket peer, the
actual routing path or a security decision. A connected peer may be a proxy or edge.
Use existing authorized network inventory, provider-console data or an operator
attestation for provider and ASN. If a value is verified, pass `--network-provider`
or `--network-asn` with `--network-source` as listed by `probe --help`. Runner identity
confirms GitHub hosting, and public address ranges cannot assign the specific runner's
egress ASN. Leave unknown values unset. Keep public egress IP details in approved
private channels if the administrator needs them for correlation.

### 3. GitHub Actions dry run

1. Open the repository's **Actions** tab and select **Stage 3 access probe (manual)**.
2. Select **Run workflow** and choose the authorized revision. Leave
   `approve_network_requests` at `no` and `window_start_utc` empty. Select **Run workflow**.
3. Confirm the offline tests and the **Access probe plan** in the Summary. The plan
   must show the single robots URL, `max_requests: 1` and `follow_redirects: false`.
   The live step should be skipped. The dry run uploads `plan.json`. Checkout and
   dependency installation contact GitHub and PyPI.

### 4. Run the authorized pair

After both approvals and a successful rehearsal, agree on one UTC start in
`YYYY-MM-DDTHH:MM:SSZ` format. Dispatch the cloud workflow early enough to cover its
observed dry-run setup time. Use `APPROVED` and that exact value for `window_start_utc`.
The live step waits until the start if ready early. The exclusive end is 120 seconds
later. GitHub re-runs skip the live step. Confirm the first run is approaching the
live step before starting the local command. Cancel any pending counterpart if one
environment has already reported a denial, rate limit or verification challenge and
the counterpart has yet to send. Do not start a replacement run in that window.

On the laptop, enter the same UTC start. The local command waits if invoked early.

```powershell
$windowStart = Read-Host 'Enter the approved UTC start (YYYY-MM-DDTHH:MM:SSZ)'
.\benv\Scripts\python.exe -m src.main probe --config config/ncaa_robots_probe.json --environment local-windows --capture-diagnostics --window-start-utc $windowStart --output logs/phase2 --execute
```

The first time gate runs before the resolver snapshot. A second gate runs just
before HTTP to cover DNS delays. A missed window returns exit 3 and records
`window_closed` with zero HTTP attempts. The request-start timestamp is the time just
before calling Requests and is the field to compare. A connection may complete after
the window. The one-request run ends on any result, including 403, 429, challenge,
redirect, backoff or connection failure. A 3xx is recorded with a redacted Location
when usable and without following it. Keep the evidence and stop. Any follow-up needs
fresh authorization. Use the configured identity and route throughout.

### 5. Collect and compare the evidence

The local command prints output paths under `logs/phase2/` for
`access-probe-local-windows-<probe-id>.jsonl` and its `-summary.csv`. The CSV is
header-only for the robots-only scope. JSONL is the primary evidence. GitHub writes
`plan.json`, `result.json` if the live step ran, and
`access-probe-cloud-github-actions-ubuntu-<probe-id>.jsonl` with its `-summary.csv`
if the probe ran. On the completed run's **Summary** page, download
`access-probe-logs-<github-run-id>-1` under **Artifacts**. Extract it under
`logs/phase2/cloud/` and compare before the 30-day retention period ends.
[GitHub artifact download documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)

```powershell
$localJsonl = Read-Host 'Enter the local probe JSONL path printed by the CLI'
$cloudJsonl = Read-Host 'Enter the extracted cloud probe JSONL path'
.\benv\Scripts\python.exe -m src.main compare-probes $localJsonl $cloudJsonl --output logs/phase2/comparison.json
```

1. Confirm both artifact revisions, configured request fields, window bounds and
   one attempt on the same URL. Compare `request_start_gap_seconds` and both
   `request_started_at` fields. Treat a missed window, stopped counterpart or absent
   attempt as an incomplete pair. Use prepared headers to check observable request
   differences. Raw config file hashes can differ because of line endings.
2. Compare status, classification, redacted headers, DNS families, peer and body
   size/hash. Body evidence is the allowlisted denial or verification markers and
   recognized references. The hash covers the Requests-decoded body. Raw bodies,
   cookies, credentials and proxy URLs are excluded. Header indicators and the
   socket peer may be missing. A timeout or body-read failure can leave some fields
   absent even if a status was received. `requests_sent` counts calls into the
   HTTP client. A DNS, connection or TLS failure can prevent a call from reaching
   NCAA.
3. Apply the [H01–H09 interpretation matrix](stage3_local_vs_cloud_report.md#interpreting-a-fresh-pair).
   Record the observed difference and the alternatives that still fit. A successful
   reproduction supports environment association. Only correlated administrator
   evidence can identify the specific rule.
4. Send the [administrator inquiry](stage3_local_vs_cloud_report.md#draft-inquiry-to-the-ncaa-website-administrator)
   only with authorization. Include the UTC attempt timestamp, endpoint, method, UA,
   status, reference IDs, GitHub run and attempt, and sanitizer-approved headers.
   Request the responding layer, event disposition and rule category. If logs are
   unavailable, ask for an authorized capture window or authorized test environment.
