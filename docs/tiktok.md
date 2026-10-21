# TikTok integration — mentor review

Status: DEVELOPMENT. The current end-to-end path collects public video comments associated with Overture place candidates. It reuses the existing UC browser, DOM collector and SQLite transactional outbox. It is not ready for an unattended large-scale crawl.

## Review order

1. `experiments/tiktok_access_benchmark/overture_study.py`: Overture plan, name/address search, observed video-card navigation and bounded sequential orchestration.
2. `experiments/tiktok_place_discovery/probe.py`: candidate discovery and matching evidence. Search relevance alone does not verify a place association.
3. `experiments/tiktok_access_benchmark/session_manager.py` and `auth_session.py`: existing UC lifecycle, optional private profile, explicit human READY gate and read-only authentication observations.
4. `experiments/tiktok_access_benchmark/ui_flow.py`, `access.py`, `network.py`: onboarding, Comments access and endpoint-aware safe stops.
5. `experiments/tiktok_comment_dom/probe.py`, `snapshot.js`, `extractor.py`: bounded DOM extraction and collector checkpoints.
6. `experiments/tiktok_comment_dom/persistence.py`: comment identities, transactional outbox and idempotent event publication.
7. `experiments/tiktok_access_benchmark/reliability.py` and corresponding tests: observation quality, timings, reconciliation and reports.

Keep current paths for this review. Moving packages or removing apparently old probes can break imports. `reviews/tiktok_source_review_manifest.json` lists the static Python/JavaScript import closure and tracked status. It includes imports in inactive branches and is not proof that every module runs in production. The manifest deliberately keeps support modules imported from `google_maps_review_extraction_benchmark`; their package name does not make them disposable.

## Data flow and contracts

```text
Local Overture canonical catalog
  -> place ID + business name/address
  -> TikTok search -> observed card -> candidate video
  -> ordinary onboarding -> accessible Comments
  -> CommentSession -> SQLite comments + checkpoint + outbox transaction
  -> atomic Parquet publication -> outbox acknowledgement
  -> exact reconciliation -> reliability report
```

The automatic search route labels query-based associations `QUERY_CANDIDATE_UNVERIFIED`. Do not present these as verified restaurant reviews until the location/video match is reviewed. Discovery is separate from comment extraction.

SQLite tables are `tiktok_comments`, `tiktok_comment_events` and `tiktok_comment_checkpoints`. Comment changes and their outbox/checkpoint commit together. Parquet is append-only revision history; its raw row count need not equal current logical comment count after updates. Current live layout is one event file under crawl-date/run partitions, not one file per video. A temporary file is renamed before acknowledgement; replay verifies an existing event file before acknowledging it. Publication assumes a single writer. This is process-crash recovery, not a claim of power-loss safety or exactly-once source collection.

`SESSION_FALLBACK` identities are run-scoped. Same-run replay is idempotent; cross-run source-comment deduplication is not guaranteed. Observed DOM IDs remain candidates, not validated canonical IDs. Top-level collection is bounded. Optional `--reply-limit N` (0-50, default0) captures visible level-2 replies with a separate budget. `--reply-expand-limit N` (0-5, default0, requires positive reply-limit) additionally permits bounded native clicks on observed View/See/Xem reply controls. It never promises deeper nested-thread coverage. `COMMENT_LIMIT`, `MAX_SCROLLS` and an unavailable safe scroll container do not establish full coverage.

JSON/CSV distinguish TOP_LEVEL and REPLY. SQLite/Parquet schema v1 remains unchanged: `provenance.comment_relationship` contains record_type, parent_comment_id, parent_session_key and parent_link_kind when reply mode is enabled. A DOM parent ID remains a candidate; an ID-free parent key is only a session observation link. Missing/ambiguous parents remain UNKNOWN/null, never inferred from text similarity. Legacy rows without relationship metadata come from the old top-level-only writer; do not reinterpret them as new reply observations. Existing schema-v1 event files are not overwritten. Enabling relationship metadata for a previously stored observed-ID record can create an UPDATE revision; count latest logical records rather than raw event rows.

Reply collection always reports PARTIAL_VISIBLE, even after an explicit top-level end marker. The current DOM does not prove that all replies have been expanded. Pending reply controls, expansion attempts/uncertain intents and missing parent IDs are measured separately in collector quality/checkpoints. A unique structural parent/control match is re-resolved before WebElement.click; index-only matching and JavaScript click are not used. Gates are checked before/after and stop without retries. Intent is persisted before the UI side effect; resume skips an uncertain intent instead of potentially repeating it. This can leave replies uncollected after a crash and is not browser/filesystem fencing. Orphan anonymous replies with identical observed content can still collide; this is not exactly-once source collection.

## Evidence and limitations

| Observation | Result | Limit |
|---|---|---|
| Verified initial UC milestone | 7 comments; SQLite7/Parquet7; delivered7; pending0 | COMMENT_LIMIT/PARTIAL; SESSION_FALLBACK |
| Latest operator-supplied signed-in trial | 1 accessible video; SQLite2/Parquet2; pending0; duplicates0 | NO_SAFE_COMMENT_SCROLL_CONTAINER; CAPTCHA UNKNOWN |
| Latest operator-supplied no-persistent-profile trial | 1 accessible video; SQLite2/Parquet2; pending0; duplicates0 | Operator observed no CAPTCHA; automated classification UNKNOWN; anonymous auth not independently validated |
| Captured network evidence in those two trials | No403/429 observed | Capture is not all-request coverage or a capacity guarantee |
| Latest executed offline regression | 322 passed,2 existing Pandas warnings,33.01s | Includes bounded native expansion, TikTok/persistence + Harness M1/M2 + Google; no browser/live test |

`TEST_SET_COMPLETED` means the bounded test list ended, not complete comment coverage. CAPTCHA rates exclude UNKNOWN observations; startup authentication evidence is reported separately from video trials. Current timings omit some session startup/discovery costs; the reported videos/hour must not be used as total operational capacity. Login does not guarantee CAPTCHA-free access.

The missing historical list-class bug is now fixed offline: snapshot and scroll action share a visible-comment ancestor resolver, exclude body/document/video surfaces and individual-comment overflow, and record scrollTop/scrollHeight/clientHeight, unique counts and no-progress. Reaching the bottom/no-growth remains PARTIAL. Explicit list-scoped end/empty UI marker contracts are fixture-tested but not verified against a new live layout. The fix does not establish that every TikTok layout is supported.

Remaining issues for review: live scrolling/reply DOM behavior unverified; incomplete fresh access observations producing UNKNOWN; a reported WebDriver observation failure whose underlying cause is not yet established. Optional human CAPTCHA verification uses the same owner and requires fresh revalidation; genuine denial/login restrictions/429 retain safe-stop behavior. No automated solving, proxy rotation or automatic restriction retries.

## Reproduce offline checks

Use the existing Python environment with the repository dependencies and pytest available. Do not change frozen shared dependencies merely to silence the existing numexpr/bottleneck warnings. From the repository root:

```powershell
$testRoot = 'output/tiktok-review-tests-' + [guid]::NewGuid().ToString('N').Substring(0,8)
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  experiments/tiktok_access_benchmark/test_auth_session.py `
  experiments/tiktok_access_benchmark/test_overture_study.py `
  experiments/tiktok_access_benchmark/test_reliability.py `
  experiments/tiktok_access_benchmark/test_crawler.py `
  experiments/tiktok_access_benchmark/test_session_manager.py `
  experiments/tiktok_access_benchmark/test_network.py `
  experiments/tiktok_comment_dom/test_dom.py `
  experiments/tiktok_comment_dom/test_normalization.py `
  experiments/tiktok_comment_dom/test_replies_scroll.py `
  experiments/tiktok_comment_dom/test_persistence.py `
  experiments/tiktok_place_intelligence/test_live_dom_persistence.py `
  tests/test_google_baseline.py
```

These selected tests use fixtures and mocks; do not set live/local-browser test opt-ins. Node is required to execute the JavaScript synthetic-DOM regression without a browser; that test explicitly skips if Node is absent. The original three JSON fixtures were reviewed: author identifiers are fixture aliases or absent, attribute maps contain no credentials, URLs use fixture handles, and retained snippets contain no identifying contacts. A broad phone-pattern scan matched public video IDs/dates, not phone contacts. The new replies fixture is entirely synthetic. This review covers these fixture contents, not arbitrary future screenshots/logs. Offline persistence/debug commands are in `runbooks/tiktok_comments.md`. Historical backend sections there describe past experiments; the current source factory selects SeleniumBase UC (`uc=True`, visible Chrome).

## Optional operator-run smoke

A clean clone does not contain the private Overture catalog, saved plan, cookies or authenticated profile. Provision the existing canonical catalog locally; do not upload it as part of this source review. Use the existing prepare command to create a local plan:

```powershell
$planDir = 'output/tiktok-review-plan-' + [guid]::NewGuid().ToString('N').Substring(0,8)
python -B -m experiments.tiktok_access_benchmark.overture_study --mode prepare --output $planDir
```

Review that generated plan before a separate authorized live run. Start with one video, max7top-level comments, one visible session and fresh output. The prepare command is local planning, not discovery/crawling. Do not run a live benchmark as a prerequisite for code review. Authenticated use additionally requires a local profile and explicit READY; do not distribute that profile. A clean clone without catalog inputs cannot reproduce the local live milestone from source alone.

## Git review scope

Include reviewed TikTok source/tests/JavaScript, required untracked support modules from the manifest, sanitized fixtures, this document and an accurate package README. Existing shared/Google files are dependencies, not changes for this TikTok review. Harness M1/M2 is a separate review; do not bundle it.

Exclude `output/`, `data/`, SQLite/Parquet, browser profiles, cookies, credentials, `.env*`, screenshots/raw DOM/network captures, local `AGENTS.md`, agent state/snapshots and historical runtime reports. Preserve these locally; source cleanup does not mean deleting evidence. Current `.gitignore` rules do not replace per-file review, and already tracked files are not protected by ignore rules.

Before staging, inspect each manifest candidate and fixture. Do not use `git add .` or add entire `experiments/`/`docs/` directories. Review the exact staged diff and secret/content exposure before any commit. No staging, commit, branch or push has been performed by this documentation task.

The personal `origin` and shared `upstream` histories must remain independent. A mentor-facing review can be shared through the personal repository after ancestry verification; publishing to the shared repository requires explicit destination approval. Never merge/rebase upstream history into the personal branch to prepare this review.

Suggested review title: **TikTok Overture discovery, bounded DOM collection and transactional persistence**. Reviewer focus: access classification, candidate matching, safe scrolling, fallback identity/replay and truthful completeness/reliability metrics. Recommend a development review, not a scale-ready release.

## UI-aligned follow-up (2026-10-15)

The existing study supports `--comment-limit 50 --comment-scrolls 10 --reply-limit 20 --reply-expand-limit 5` for a bounded completeness smoke; defaults remain7top-level/3scrolls/no replies. It uses the saved plan, not a fixed video URL. Replies load asynchronously; the collector waits for a new reply, retains records across snapshots, and resolves parents without requiring historical thread classes. Missing or ambiguous roots still stop or leave parent unknown. Max5expansion clicks cannot establish full reply coverage on arbitrary videos. Exact UI heading counts now accept Vietnamese and English. Bottom/no-growth/count agreement remains PARTIAL without authoritative end evidence.

Final offline regression:336passed,2existing Pandas warnings,32.51s (TikTok, persistence, compaction, HarnessM1/M2 and Google baseline). Use a short fresh `--basetemp output/t<5hex>` on Windows: overly long fixture paths can fail in PyArrow with WinError3. Interactive observation showed8parents+3replies matching11for one video, but patched collector live scrolling/expansion remains unverified. No live crawl was run for these code changes.

Observed reply pagination follow-up: supports `View N more` after an already visible child. Offline synthetic2parents+4replies reconcile6SQLite/6Parquet,pending0;95focused tests passed. Live confirmation remains pending.

## Grouped analysis output (2026-10-21)

### Comment readiness follow-up

The DOM collector now waits on an already visible comment panel instead of
clicking its icon again while cards load. Visible list-scoped skeleton/busy
markers are diagnostic evidence; valid rendered comments are retained even when
a loader coexists. The existing extractor rejects empty/non-comment cards.
Waits remain bounded,
and timeout remains partial, not proof of an empty or complete list. Native
navigation snapshots must match the expected video ID before a wait succeeds;
genuine access gates still stop immediately. A changed video at collection is
reported as `VIDEO_CONTEXT_CHANGED` and a target wait timeout as
`VIDEO_TARGET_TIMEOUT`, without accepting comments from the wrong video.

Per-video failures now save sanitized stack frame file/line/function identifiers
in `error.json`, without exception messages or locals. Dialog diagnostics also
record panel/loading/card-count/wait-timeout observations. These changes are
offline-tested; no live repair claim is made. A UI heading count alone still
does not establish complete comment coverage.

The Overture `--mode auto-crawl` runner now invokes the existing offline compactor
after closing browser and per-video persistence owners. It writes
`analysis_lake/run_id=.../video_id=.../group-<digest>.parquet`: one file per
data-run/place/video, including retained event revisions. This is event history,
not a deduplicated current-comments table. Partial runs stay partial.

Consume this lake only when `analysis_output.json` says `status=VERIFIED` and
`readable=true`. The sealed manifest records exact event membership and payload
hashes. Publication uses temporary files, file fsync and atomic rename; retry
verifies existing output before accepting it. A changed source requires a fresh
destination. Process-crash recovery is tested; power-loss durability is not certified.

The raw `lake/` remains append-only evidence for existing outbox acknowledgements;
the live one-event-per-file writer is unchanged. This reduces analysis file count,
not raw write overhead or total disk usage. Never union raw and compacted lakes.
Compaction errors are recorded separately without replacing the crawler stop
reason or claiming collection success. Pending/unreconciled data is rejected;
no collection means `NOT_CREATED`, not successful reconciliation.

For an interrupted compaction, stop all writers and rerun the existing CLI with
the same destination and unchanged source. Do not rerun live crawling for this:

```powershell
python -B -m experiments.tiktok_comment_dom.compaction `
  --database 'output/<run-id>/comments.sqlite3' `
  --source 'output/<run-id>/lake' `
  --output 'output/<run-id>/analysis_lake'
```

Check the destination `summary.json` for `VERIFIED` after a manual CLI retry.
An earlier runner `analysis_output.json` is not updated by that standalone CLI.
Stable source-comment deduplication across SESSION_FALLBACK runs remains unproven.
