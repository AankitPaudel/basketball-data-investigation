# Stage 3 — Local vs. cloud accessibility report

The [Phase 2 review](#phase-2--http-403-root-cause-investigation) rechecks the archived
evidence and documents the unresolved trigger.

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
    reply was a 382-byte HTML page that the legacy blocker detector flagged. The
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
| stats.ncaa.org — `robots.txt` | readable; disallows the probe URLs | 200 (text/plain, 202 B) | **refused, HTML blocker flagged** | **403** (text/html, 382 B) |
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
- **From GitHub Actions,** the robots.txt request returned HTTP 403 and a small HTML
  response. The runner could not read the policy file. The response could have been
  generated at the edge or elsewhere in the request path.
- **Cause:** the log does not show why the runner was blocked. The earlier local tests
  (2026-11-17 04:30 UTC) had also received
  403s, for two stats pages requested before robots-first checking existed. The 403 body
  from the cloud run was not saved. The log preserves its SHA-256 hash (`70d355a6…`),
  size and legacy blocker flag.
- **Probe behavior:** it did what the plan requires. An unreadable robots.txt counts as
  "do not crawl", the 403 stopped the host for the rest of the run, and nothing was
  retried or worked around.

What this means for the project:

1. robots.txt is a published request to automated clients, not a technical lock.
   stats.ncaa.org's file (202 bytes, SHA-256 `bbc40843…`) disallowed both probe URLs
   for this project's user agent, under the `User-agent: *` rule recorded in the test
   plan. The project follows that request in both environments. (Only the file's hash
   was kept, not its text.) From the cloud runner, stats.ncaa.org also
   answered with HTTP 403 on this run. The published policy applies when moving the
   pipeline to a cloud environment.
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
| Validated CSVs untouched | SHA-256 before and after both probe runs: `363d7b4e…`, `d27f590b…`, `a7f1b23b…` (unchanged during Stage 3; see the note below on the later line-ending fix) |

**Later change, made on purpose (2026-11-18, after this report):** the pipeline used to
write CSVs in text mode, so on Windows its LF line endings became CRLF on disk. It now
writes bytes, so files are LF on every platform. The three validated CSVs were converted
from CRLF to LF. Only the line endings changed: the content, and the `csv_sha256` in each
`quality_report.json`, are the same. Their on-disk hashes therefore changed:

| Game | Rows | Old on-disk SHA-256 (CRLF, before 2026-11-18) | New on-disk SHA-256 (LF) = `csv_sha256` |
|---|---|---|---|
| 6643366 | 30 | `363d7b4ef70f67241e33081c2647eb724389b4e2807a985f750b5d5af1149f72` | `b964bb2dc4f6f8b6c72ea3d86ccc7963fa7778b2f1e2321e0ff770731028e4e9` |
| 6631754 | 23 | `d27f590b1ce3913feb9e752d60c815587adc418deeb50e0859a95270f80f6c4f` | `c4a24d7f36d4cebaaab83f2ec7686b536e3f0ef4ebcf351d883ff166941b2f3d` |
| 6617400 | 24 | `a7f1b23b26d4587592cb8839a53c86b707d30804b1e448b8321c1e5c69fac178` | `525bfea27869ea3e2588eaf4bed020073e07ecf5e565caffb509802c48b5999f` |

The new hashes are also the ones the GitHub Actions rebuild check produced (run
44234640697).

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

## Phase 2 — HTTP 403 root-cause investigation

The archived runs establish an environment-associated difference on `GET /robots.txt`
at `stats.ncaa.org`. The local client received the policy as HTTP 200. The GitHub-hosted
runner received HTTP 403 and a small HTML denial response. The legacy challenge flag
could also be set by ordinary “Access Denied” wording or a login form. The exact trigger,
the presence of a verification challenge and the identity of the responding service
remain unknown. The investigation concerns this one endpoint and pair of observations.

### Evidence and controls

The two JSONL files and both summary CSVs listed above matched their documented
SHA-256 values. The [machine-readable comparison](stage3_probe_comparison.json)
records both file hashes, run IDs, line numbers, field differences and gaps. Each
of the 37 requests per run has one recorded attempt and no redirect. Both environments
completed 30 HTTP 200 target requests across ten allowed Directory, school and conference
URLs. All six control-host policy hashes matched. The local `robots_allows: false` records
verify the parser's decision for the two stats URLs. The cloud policy read failed closed
and the host was stopped. Each run logged six skipped stats target records with no HTTP
attempts. The original policy text was not saved for independent review of its directives.

| Baseline field | Local Windows | GitHub Actions |
|---|---|---|
| Endpoint | `https://stats.ncaa.org/robots.txt` | Same |
| Configured request | Requests GET, `BasketballDataResearch/0.1`, `Accept: text/html`, 20-second timeout, no retry. Recovered from the probe-era code | Same |
| UTC request timestamp | `2026-11-17T09:10:02.811988Z` | `2026-11-17T17:09:14.891970Z` |
| OS and runtime | Windows 11, Python 3.13.5, Requests 2.34.2 | GitHub-hosted Ubuntu 26 image, Python 3.13.18, Requests 2.34.2 |
| Status, media type, length | 200, `text/plain`, 202 bytes | 403, `text/html`, 382 bytes |
| Response hash prefix | `bbc40843` | `70d355a6` |
| Total request time and redirects | 0.984 s, none | 0.256 s, none |
| Backoff and body evidence | No `Retry-After`, body unavailable | No `Retry-After`, body unavailable, legacy blocker flag `true` |
| Historical fields missing from both | Prepared request headers, response headers, HTTP and TLS protocol details, DNS answers, connected peer, egress provider and ASN, proxy settings, exact response markers, correlation IDs and runner region | Same |

The logged request plans agree. Normalizing the current Stage 3 config to CRLF and LF
reproduced the local and cloud hashes, respectively. The OS, Python patch version and
request times differed, with the requests about eight hours apart. Any difference in
transitive client dependencies, DNS path, server routing, proxy handling or policy state
cannot be reconstructed from the response hashes.

`logs/ncaa-source-diagnostics.jsonl` separately records two earlier local 403s on
different stats URLs at 04:30 UTC, before robots-first checking. Their 406-byte and
397-byte bodies have distinct hashes, also different from the cloud robots response.
`logs/ncaa-policy-diagnostics.jsonl` and `logs/ncaa-d2-d3-access-2026-11-17.jsonl`
show subsequent local policy reads and disallowed targets skipped before sending.
The manually saved pages and their sanitized fixtures establish that operators obtained
stats pages on separate visits. They provide no matched browser capture of `/robots.txt`.

The archived cloud JSONL identifies GitHub Actions run `44133732954` and attempt `1`.
The earlier report attributes the successful fixture rebuild to run `44234640697`.
Fresh GitHub run details were unavailable during this review. The cloud access findings
come from the downloaded JSONL and CSV, and the old cloud rebuild result comes from the
repository's written record. [Access probe run](https://github.com/AankitPaudel/basketball-data-investigation/actions/runs/44133732954) · [Rebuild run](https://github.com/AankitPaudel/basketball-data-investigation/actions/runs/44234640697)

### Hypotheses

Confidence refers to the conclusion about the hypothesized trigger itself. A supported
hypothesis fits the evidence but still has uncontrolled alternatives. The experiment IDs
refer to the [Phase 2 test plan](stage3_access_test_plan.md#phase-2--diagnostic-experiments).

| ID | Hypothesis and existing support | Counterevidence or limitation | Missing evidence | Method and expected observation | Actual observation | Confidence and conclusion |
|---|---|---|---|---|---|---|
| H01 | Source network or IP policy. Local 200 and runner 403 on the same URL with the same configured client identity. | OS, runtime, routing and time also varied. Earlier local stats-path denials concern different URLs. | Egress classification and matching rule. | E06 compare authorized captures and E07 ask whether the event matched a source criterion. | Environment-associated difference in one pair. | Low, supported as a candidate. |
| H02 | Cloud hosting or ASN classification. The denied observation came from a GitHub-hosted runner. | One provider and one runner. A runner designation gives no egress ASN or server-side category. | Attributed ASN and policy match. | E05 review existing provider records, then E07 ask for the rule's source category. | Runner provenance present and ASN absent. | Low, inconclusive. |
| H03 | Client or request characteristics. OS and Python patch versions differed. | Same Requests version and configured GET, UA and Accept. No same-endpoint browser pair. | Prepared headers, other dependencies, protocol and server-side client classification. | E04 review existing approved browser evidence, E06 capture the same pinned client and E07 correlate. | Matching configured client with other transport inputs unknown. | Low, inconclusive. |
| H04 | DNS or different routing. The two environments could use different resolvers or paths. | Both received HTTP responses, ruling out total DNS or connectivity failure for the observed transactions. | DNS answers, connected peer and edge attribution. | E05 compare existing DNS records or E06 capture authorized resolver observations. Different answers or peers establish a path difference only. | No historical resolver or peer data. | Undetermined, not tested. |
| H05 | CDN or WAF policy. Small HTML 403 and a blocker marker are compatible with an intermediary policy response. | The same observable response could be generated by the origin or an intermediate proxy. | Response headers, event reference and policy or rule ID. | E03 inspect archive, E06 capture allowlisted indicators and E07 seek event attribution. | HTML denial established. Edge service and rule unidentified. | Low, inconclusive. |
| H06 | Browser verification or session requirement. Some stats pages were saved manually and the legacy response flag was true. | A local unattended GET received robots.txt. Saved browser pages are different URLs and the legacy flag is ambiguous. | Challenge-specific text or header and same-endpoint session observations. | E04 review existing permitted browser evidence and E06 look for verification markers without interacting with them. | The cloud log lacks specific verification evidence. | Low, inconclusive. |
| H07 | Rate limit or temporary restriction. Single sample and an eight-hour gap leave time-dependent state possible. | The 403 was the first probe request to the host. No 429 or Retry-After was logged; controls succeeded. | Quota event or pre-run traffic, policy window and comparable UTC captures. | E07 ask for the event category. E06 can test whether the historical split recurs with nearby timestamps. | No evidence of probe-induced throttling. An upstream limit remains possible. | Low, inconclusive for a time-dependent restriction. |
| H08 | Proxy or environment configuration. Requests can honor environment proxy settings. | Other hosts responded successfully; that only argues against a general connectivity failure. | Historical proxy presence, bypass behavior and peer data. | E05 ask operators to check historical configuration and E06 record booleans and public peer metadata. | No historical proxy metadata. | Undetermined, not tested. |
| H09 | Other server-side access policy, such as an endpoint-specific rule or temporary configuration. | Local policy was readable repeatedly and the earlier stats URLs are different resources. | Origin or edge event, policy version and route handling. | E07 ask for handling tier, disposition and matched policy. | No server-side records available. | Low, inconclusive. |

### Interpreting a fresh pair

Apply this matrix after verifying that both requests used the agreed endpoint,
configuration, revision and UTC window. “Contradicts” refers to the recorded event.
Configuration and policy can change over time. A fresh result carries its own
timestamp and cannot settle the reason for the archived denial by itself.

| ID | New evidence that would support the hypothesis | New evidence that would contradict it | Evidence still insufficient | Administrator confirmation |
|---|---|---|---|---|
| H01 | Repeat 200/403 split with matching prepared request settings and nearby timestamps supports environment association. An event matched to an IP/network criterion supports source policy. | The traced denial matches a condition independent of source and event logs show no source-network match. | Two status codes alone leave egress, intermediaries, TLS and policy state confounded. | Required to confirm the source criterion for the event. |
| H02 | Authorized inventory identifies the runner's actual egress provider or ASN, and the event records a match to that hosting classification. | Event records show the relevant cloud/ASN classification did not match and another rule produced the response. | A GitHub runner label, public provider address range or a single cloud denial does not identify egress or a classification decision. | Required for cloud/ASN rule attribution. |
| H03 | A comparable, separately approved client-class observation on the same network changes the result while other controls match, or the event names a client/request rule. | The administrator confirms identical relevant client classifications and a different condition decided the event. | Matching Requests, UA and Accept still leaves prepared headers, TLS and platform dependencies to check. | Required for the exact rule. The comparison can establish an association. |
| H04 | Different resolver answers plus observed different peers or administrator edge mapping support a route difference. A failure before HTTP can establish a resolver or connection issue for that attempt. | Same actual peer and administrator-confirmed request path and policy context weigh against this explanation for a status split. | A/AAAA differences alone may reflect caching or load balancing. A single public peer is not a route trace. | Required to link the destination or route to a denial. |
| H05 | A recognized response header or reference gives a candidate handling service. Correlation to a security event identifies the action and rule. | The site's logs show a different layer generated the response and the candidate edge only forwarded it. | A CDN header, reference pattern or small HTML 403 cannot name the matched rule. | Required for exact responding layer and policy attribution. |
| H06 | Specific verification text, a documented challenge header or an explicit challenge action recorded for the event. | The traced event was an outright deny with no challenge action. | Legacy blocker flags, generic interstitial wording, a plain 403, and successful manual saves of different URLs. | Client evidence may identify a challenge. The administrator is needed to establish the reason or a session requirement. |
| H07 | 429 with a backoff signal supports an observed rate limit. Event records identifying a quota, time window or prior counted traffic support it as the historical mechanism. | The traced denial has no rate-limit match and a different rule generated it. | An immediate 403 or absence of 429/Retry-After cannot settle whether shared traffic or a prior quota contributed. | Required to attribute the archived 403 or name the rate rule. |
| H08 | Authorized egress records show a proxy applied to the request and identify which layer returned or relayed the denial. | Verified direct egress and event correlation rule out an intervening proxy as the source. | Proxy variables, `NO_PROXY` presence or a peer mismatch provide clues with several possible explanations. | Operator network records can establish use. Correlation is needed to attribute a denial generated or relayed through it. |
| H09 | Origin or edge logs identify another policy, route behavior, temporary rule or policy version. | Logs account for the response under one of the specific alternatives and exclude other relevant matches. | A one-off 403 or subsequent 200 cannot identify a policy change. | Required for the policy and disposition. |

### Conclusion and next evidence

The same configured client and endpoint produced different results in two environments.
The probe-specific throttling hypothesis has little support because the cloud denial was
its first request to that host. A source-network policy remains a candidate. Provider
classification, verification, proxy involvement and routing remain unresolved. The archive
does not identify Akamai or any other responding edge vendor. The new diagnostic record
can carry a Cloudflare challenge header or a recognized edge reference if one is observed.
Provider indicators can help direct a lookup. The administrator's event record is needed
to establish the matched rule or reason. [Cloudflare challenge header](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/) · [Akamai error-reference lookup](https://techdocs.akamai.com/edge-diagnostics/docs/translate-error-string)

Start with the administrator inquiry. If the archived event has expired, request an
approved capture window so the administrator can correlate one request from each agreed
environment. The [experiment matrix](stage3_access_test_plan.md#phase-2--diagnostic-experiments)
records the scope and approval needed. No new live NCAA request, DNS lookup or GitHub
Actions dispatch was performed for Phase 2.

### Draft inquiry to the NCAA website administrator

> Hello, we are investigating an HTTP 403 response to the public robots policy at
> `https://stats.ncaa.org/robots.txt`. The laptop received HTTP 200 at
> 2026-11-17 09:10:02.811988 UTC and GitHub Actions run `44133732954`, attempt 1,
> received HTTP 403 at 2026-11-17 17:09:14.891970 UTC. Both probes used GET and the
> configured `BasketballDataResearch/0.1` user agent. The 403 was the runner's first
> request to the host. We retained a body hash and metadata, and have no correlation ID.
>
> Could you correlate the 403 with your origin or edge security logs and confirm the
> responding service, whether it denied or challenged the request, and the policy or
> rule category that matched? Please also confirm whether the public robots endpoint
> has restrictions on requests from GitHub-hosted or other cloud runners.
>
> If the event is outside your retention window, could you authorize one GET of
> `/robots.txt` from the laptop and one from a GitHub-hosted runner in a shared
> two-minute UTC window? We can provide sanitized headers, exact request times and
> any response reference IDs for correlation. An approved test environment would
> also work if you prefer it. The proposed capture is limited to the policy endpoint.

### Engineering update for Omar and John

> I verified the November access logs and their hashes. The local client received HTTP
> 200 for the robots policy and the GitHub-hosted runner received an HTML HTTP 403.
> Both runs completed all 30 control requests and sent zero NCAA statistics requests.
> The old flag is insufficient to distinguish a verification challenge from a denial.
> The exact rule remains unknown because the response headers, body, DNS and peer
> information were absent from the archive. The probe now has a one-request diagnostics
> scope, sanitized response and destination metadata, and an offline comparison command.
> All 198 local tests passed. The fixture rebuild reproduced all three game hashes and
> the combined 77-row hash. Next, ask the NCAA administrator
> to correlate the archived event or agree on a scope and time for a single fresh capture
> per approved environment.

The fixture rebuild used a temporary output directory. Each game passed all 57
source checks and matched the LF baseline hash listed above. The build returned exit
code 2 for the known Ferrum reclassification warnings. The combine command returned
0 and matched SHA-256 `402921c1ad1bf0a74590ca360a972dc36eaf0ddb630e4a44651b17e9ec6df03f`.
Hashes of the existing validated and combined CSV files matched before and after the
temporary rebuild.

### Task 1B readiness assessment

The Task 1 worktree contains 14 relevant files. The audit reviewed the existing
client and probe (`src/common/http_client.py`, `src/common/probe.py`,
`src/common/probe_comparison.py`, `src/ncaa/client.py` and `src/main.py`), scoped
configuration, workflow and diagnostic tests, and six documentation or evidence
files (`README.md`, `docs/ncaa_site_structure.md`, `docs/process_writeup.md`,
this report, the test plan and `docs/stage3_probe_comparison.json`). The comparison
JSON is generated from the archived JSONL via `compare-probes`. It is present in the
working tree, untracked and outside Git's ignore rules. The archived inputs are
git-ignored. Task 1B checked reproducibility using a temporary output.

The audit found that the previous workflow still allowed the original broad control
scope, the old timestamp preceded DNS, and delayed runner setup could send a request
outside the planned comparison window. The manual workflow now has a single live
scope, first-attempt approval and a UTC gate shared with the local CLI. The probe
records the configured method, headers, timeout, redirect policy and exact HTTP
attempt start. It retains a sanitized redirect Location without following it.
The previous NCAA client policy logic and CSV pipeline were left as found.

Mocked transports exercised the capture path from Requests through the JSONL artifact.
They supplied a real prepared request, synthetic headers and body evidence, a mock
resolver and peer, status, protocol, timing, body hash and an event reference. The
tests check that cookie, authentication, proxy and response-body fixture values are
absent from shared diagnostic fields. The local dry run recorded Windows 11, Python
3.13.5, Requests 2.34.2, urllib3 2.8.0 and OpenSSL 3.0.16. IPv6 socket support was
true and proxy environment flags were false in that process. The dry run used no
protected-service HTTP or DNS traffic. Windows denied the CIM interface query in
this session. The .NET interface API reported three interfaces up with support for
both address families. External IPv4 and IPv6 reachability remain unverified.

Best-effort fields can still be unavailable on the real transport. A dry run cannot
establish resolver answers, peer address, egress ASN, the negotiated TLS session, actual
proxy use or the presence of a vendor reference. The body markers are a finite set
and unusual interstitials may remain unclassified.

All 204 tests passed. The archived comparison regenerated byte for byte and all 16
existing processed CSV hashes matched before and after the audit. The workflow YAML
and both Bash steps passed local checks. A local rehearsal of the workflow's dry-run
step produced `plan.json` and a step summary. GitHub execution on the updated revision
is pending publication approval. The operator-run [execution runbook](stage3_access_test_plan.md#task-1b-execution-runbook)
covers the GitHub dry run and the separate authorization needed for the pair.
