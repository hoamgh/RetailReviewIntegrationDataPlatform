# Google Maps browser framework comparison

Date: 2026-10-28. Recommendation: **Need More Evidence**.
Status: offline PoC and actual headed preflight verified; live run authorized.
No performance winner claimed before measured results.

## Existing architecture and baseline

`crawl_experiment/browser/seleniumbase_session.py` owns SeleniumBase session creation,
proxy configuration and lifecycle. Its current default is UC mode. Browser factory
and session manager isolate that lifecycle from source-specific code.
`sources/google_maps/navigator.py` resolves search results into verified store pages;
`review_surface.py` opens/classifies the review surface; `paginator.py` controls DOM
scrolling; `extractor.py` constructs the existing `Review` dataclass. Optional review
timestamps depend on browser network events. Discovery/result selectors and Google
identity parsing live in `selectors.py` and `place_resolution.py`.

The crawler coordinator uses existing review/checkpoint repositories, incremental
windows and reconciliation. Existing benchmark runner adds store scheduling,
recovery and metrics. None is changed or replaced by this experiment.

The experiment reuses selectors, `google_identity`, `ReviewExtractor`, `Review`, and
the existing read-only process-tree `ResourceSampler`. No compatible stored DOM test
fixtures were found in the current focused test suite; a synthetic fixture is added.
The numerical SeleniumBase live baseline is **not yet measured**.

## Environment and verified evidence

- Windows; CPython 3.12.7; SeleniumBase 4.54.11; Playwright 1.42.0; psutil 7.2.2.
- Installed Chrome 156.0.8078.73; production ChromeDriver 155.0.8059.39 retained.
  Official matching ChromeDriver 156.0.8078.73 downloaded separately to
  output/browser-framework-env/chromedriver-156.0.8078.73/ (11,479,722-byte ZIP).
  ZIP SHA-256: f782a94fc2a16e7dc6be48c6a18516dc708de8e86ff4a736ddc3ff62ff207ebf.
  HTTPS official source and archive CRC checked; this hash is local, not independently
  supplied by the provider. No production dependency, Chrome downgrade or global PATH change.
- Actual local headed preflight PASS for both frameworks: same Chrome executable and
  version, verified 1280×900 CSS viewport. SeleniumBase uses process-local NEW_DRIVER_DIR
  and verifies its ChromeService path. CSS viewport uses standard CDP emulation,
  matching Playwright's viewport configuration; no fingerprint/anti-bot evasion.
- Sixteen offline tests PASS, including real Playwright/local synthetic DOM extraction
  through the production extractor, deduplication, reference comparison, challenge/
  consent stops, workload validation and framework/source failure separation.
- Command: `C:/Users/MSI/anaconda3/python.exe -B -m unittest experiments.browser_framework.test_benchmark -v`.
- No production schema changes or migration. Live execution results appear in the
  latest controlled execution section after the self-contained job terminates.

## Methodology prepared for manual execution

`experiments/browser_framework/` is an isolated transport/workload experiment, not a
replacement production adapter. Both transports execute the same small direct-place
review and search-feed discovery algorithm. They do not invoke or modify production
checkpoint, recovery, publication or shared benchmark scheduling logic.

Frozen input: `experiments/browser_framework/historical_center_framework_benchmark.json`.
D01 quán ăn, D02 nhà hàng, D03 restaurant, D04 cafe, D05 quán cơm; all zoom 15,
max_places 40, same historical anchor. Optional independent review content hashes
support accuracy checks; their absence means accuracy UNKNOWN.

| Review case                   | Overture ID                          | Artifact Google identity              | Historical extracted | Current total |
| ----------------------------- | ------------------------------------ | ------------------------------------- | -------------------- | ------------- |
| Trà Sữa Leetea                | 59be7510-fc32-43ed-bfa2-4fcb8578b2c0 | 0x31752f6e6fa35ed1:0xb49c8856b0f005df | 22 COMPLETE          | UNKNOWN       |
| Quán Ốc Sóc                   | fcbc7db8-b1ec-44f7-8800-5e758f90b788 | 0x31752f6b824e2ef3:0xfa9a938cd038522f | 60 PARTIAL\_LIMIT    | UNKNOWN       |
| Sugar Town Bakery - Khánh Hội | cf219626-f56d-46dc-abb5-46cb29c164ba | 0x31752f3334901efd:0x5d0e37f25bc7d867 | 60 PARTIAL\_LIMIT    | UNKNOWN       |
|                               |                                      |                                       |                      |               |

Historical counts are owner-supplied, never treated as current totals. Verbatim URLs
come from the original repository's `output/crawler_benchmark/runs/20261001T075700.323641Z-4f458d89/events.jsonl`.
Fixture evidence includes artifact SHA-256 and exact line numbers. Selection requires
the exact Overture ID, RESOLVED status and matching Google/Overture names. Ambiguous
events are excluded; no URL or same-name store was invented.

**Historical center approved by owner.** The historical five-query benchmark's
`anchor_live_summary.json` records center latitude 10.776223449846349, longitude
106.69714944271055. Current Khánh Hội polygon bounds are longitude
106.6969214–106.7111531, latitude 10.7524189–10.7653499: this anchor is outside.
Fixture preserves exact coordinates and source hash with `anchor_resolution=CONFIRMED`.
This is a browser framework benchmark, not a Khánh Hội coverage benchmark. Discovery
does **not** filter results against the Khánh Hội polygon. Both frameworks consume
the same five frozen query URLs, zoom, coordinate, caps and configuration. Polygon
metadata is retained only as historical context and is not a runtime dependency.
Historical results remain reference evidence, never current-run ground truth.

Each of eight cases runs three times per framework: **48 sequential trials**.
Alternate framework-first order by case and repetition. Use the same installed
Chrome executable, visible 1280×900 window, en-US locale, fresh temporary sessions,
machine and network. No reused user profiles, credentials, proxy rotation, UC mode,
stealth modifications, challenge solving or access bypass. Keep other machine load
stable; record concurrent-load observations when interpreting resource measurements.

Discovery deduplicates stable Google identities and separately counts unidentifiable
links. Reviews verify the target Google identity after navigation, open the review
entry, and reuse the exact production DOM extractor through a small read-only
Playwright element wrapper. No timestamp network enrichment is enabled for either
framework. Default review order is retained in both; neither claims newest ordering.

Each trial allows at most twelve scroll cycles, stops after three unchanged cycles,
enforces at most forty discovery IDs or twenty unique reviews, and has
a 75-second workflow deadline plus a 120-second worker watchdog. DOM batch iteration
stops at the hard item cap. Navigation timeout is thirty seconds; inter-trial cooldown is
ten seconds. These caps intentionally differ from production's large crawl limits.

The runner stops the whole experiment on a detected challenge, rate limit, sign-in,
consent or restricted-view page. It neither dismisses restrictions nor retries failed
trials. Source restrictions are separate from framework errors/timeouts. A missing
review entry is a workflow error of uncertain attribution, not automatically a
framework defect. Selenium navigation does not expose HTTP status; Playwright checks
401/403/429 when available. Both additionally reuse page classification and explicit
consent/auth checks. Undetectable restrictions remain a limitation.

Startup timing surrounds browser/context/page initialization; trial total includes
startup, navigation, extraction and cleanup. Worker wall time also includes Python
imports/process overhead. CPU is sampled host-normalized process-tree utilization;
peak memory is the maximum sampled sum of crawler/browser RSS, not physical unique
memory or an exact operating-system peak. One-second sampling may miss short peaks
or short-lived subprocesses. Store per-trial sample errors and raw resource samples.

Artifacts remain under ignored `output/browser-framework-benchmark/`: environment,
input/code hashes, trial results, hashed review fields, resource samples, progress and
summary. No raw reviewer names, text, cookies or profiles are exported. Content hashes
are still local research artifacts. Completed trials resume only for identical inputs,
code and environment; incomplete results require explicit review. A stale run lock
requires confirming its process has stopped; never remove an active run's lock.

## Results

| Metric | SeleniumBase | Playwright |
| --- | --- | --- |
| Live trials completed / planned | 0 / 24 | 0 / 24 |
| Total runtime / browser startup | Unmeasured | Unmeasured |
| Unique POIs / reviews extracted | Unmeasured | Unmeasured |
| Review completeness / independent accuracy | Unmeasured | Unmeasured |
| Framework errors / timeouts / source restrictions | Unmeasured | Unmeasured |
| CPU / sampled peak process-tree RSS | Unmeasured | Unmeasured |
| Local synthetic review DOM integration | Driver mismatch; unverified | PASS |

Report results per case/repetition before aggregating. Review completeness is unique
extracted IDs divided by independently known current count, only if available; a ratio above one flags count
drift, not extra completeness. Accuracy compares normalized rating/text/displayed-date
hashes only for independently referenced IDs; missing references mean unknown accuracy.
Framework identity overlap/Jaccard is agreement, never accuracy or source coverage.
Discovery has no independent completeness denominator unless one is supplied later.
Historical 22/60/60 never enter completeness. Twenty-review target attainment is a
separate metric. Success means reaching twenty reviews for review cases; discovery
success means nonempty extraction, not full coverage. Resolution errors are separate.

## Limitations and engineering trade-offs

- Three store identities/URLs and five query configurations are recovered and frozen;
  anchor scope is approved; matching isolated driver preflight PASS; current review totals UNKNOWN.
- Search ordering, source changes, review counts, default sorting, language and network
  can affect results independently of frameworks. Three repeats give limited evidence.
- The experiment disables production UC mode to comply with the no-bypass constraint;
  it cannot establish production UC performance or justify migration by itself.
- Playwright locator waits and browser contexts simplify isolated experiments. Its
  1.42.0 installation is old relative to Chrome 156; a local fixture pass does not
  establish live compatibility. SeleniumBase retains the already integrated navigation,
  review reconciliation, timestamps and recovery behavior without migration work.
- The minimal PoC omits production recovery, timestamp interception, sort verification,
  full-text expansion and detailed media workflows. Truncated text is not full review
  accuracy. Changing production transports would require separate integration tests.
- Driver/browser version pairing, lifecycle semantics, selectors and browser upgrades
  remain maintenance responsibilities for either framework. Playwright would introduce
  a second lifecycle/transport contract until a separately approved migration.

The official [Playwright browser documentation](https://playwright.dev/python/docs/browsers)
and [SeleniumBase driver documentation](https://seleniumbase.io/help_docs/webdriver_installation/)
describe browser selection and driver management. No claim of comparative speed is
derived from those documents.

## Recommendation and exact next step

**Need More Evidence.** Retain the existing implementation while collecting a fair
live baseline; do not migrate from fixture timing or tool preferences.

The three canonical Google URLs are verified offline against their exact historical
RESOLVED events and Overture IDs. No store substitution or current availability claim
is made. Historical center is now approved; no URL or fixture-data blocker was found.
Select an installed
Chrome/cached driver with matching major versions, or separately prepare a compatible
driver. Do not let a benchmark silently download or upgrade production dependencies.
After browser/driver compatibility preflight passes, run manually:

```powershell
& C:/Users/MSI/anaconda3/python.exe -B experiments/browser_framework/run.py --workload experiments/browser_framework/historical_center_framework_benchmark.json --check
& C:/Users/MSI/anaconda3/python.exe -B experiments/browser_framework/run.py --workload experiments/browser_framework/historical_center_framework_benchmark.json
```

For a different compatible installed Chrome, add `--chrome <absolute-path>` to both
commands. The preflight visits local content only. The current isolated environment
passes version and actual-viewport checks. The owner explicitly authorized live execution.
Self-contained `experiments/browser_framework/execute.py` writes progress, final
summary, protected-file/production-driver integrity checks and the final report section.
It stops the entire run on a challenge/restriction and never retries automatically.
The preliminary table above is superseded by the latest controlled execution below.

The final comparison must report throughput, reliability, completeness (UNKNOWN
where no current denominator exists), CPU/sampled peak RSS and migration cost.
Assess migration cost separately for navigator/surface/paginator compatibility,
timestamp interception, lifecycle/recovery integration and regression validation;
the minimal PoC does not measure implementation effort or establish a migration estimate.

## Latest controlled execution

Recorded UTC: 2026-10-28T12:58:55.608633+00:00. Status: **STOPPED**.
Completed 1/48 planned trials. Artifact directory: `output\browser-framework-benchmark-156-v1`.
Both frameworks use Chrome 156.0.8078.73, headed, 1280×900 actual viewport; concurrency 1.

| Framework / workload | Trials | Runtime median / p95 (s) | Item observations / unique IDs | Success rate | CPU mean % / sampled peak RSS MiB |
| --- | --- | --- | --- | --- | --- |
| seleniumbase / discovery | 1 | 5.828 / 5.828 | 0 / 0 | 0/1 | 2.15 / 542.6 |
| seleniumbase / reviews | 0 | UNKNOWN | 0 / 0 | UNRUN | UNKNOWN / UNKNOWN |
| playwright / discovery | 0 | UNKNOWN | 0 / 0 | UNRUN | UNKNOWN / UNKNOWN |
| playwright / reviews | 0 | UNKNOWN | 0 / 0 | UNRUN | UNKNOWN / UNKNOWN |

Runtime here includes failed trials; p95 uses nearest-rank, with very small samples interpreted cautiously.
CPU is the mean of sampled trial means, normalized to host CPUs; RSS is sampled summed process-tree RSS.
Throughput and successful-only runtime distributions are retained in `summary.json`.

| Framework | Case | Status | Items | Reviews target attainment | Cause / error type |
| --- | --- | --- | --- | --- | --- |
| seleniumbase | D01 | source_blocked | 0 | UNKNOWN | consent_auth_or_limited |

Completeness and independent extraction accuracy: **UNKNOWN** (current source totals and independent review references absent).
Historical 22/60/60 counts are not denominators or ground truth. Discovery uses no polygon filter.
A source challenge/restriction stops the entire experiment without retry; unrun trials are not failures.

Migration cost is unmeasured. A production migration would require porting navigation/surface/pagination,
timestamp interception, session/recovery integration and regression validation; maintaining two transports adds upgrade/selector work.
Recommendation: **Need More Evidence**. This bounded PoC does not authorize migration.
