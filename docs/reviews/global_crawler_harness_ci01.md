# CI-01 — Global Crawler Harness Windows Quality Gate

Ngày: 2026-10-21. Phạm vi CI offline. **GitHub-hosted run chưa được thực hiện.**
Không commit/push, kích hoạt Actions, thay branch protection hoặc triển khai CD.

## Audit và quyết định

- Trước sửa đã đọc agent rules/current checkpoint và các báo cáo M3-A/B/C1/C2,
  C2a/b/c, kiểm tra Git status. Giữ nguyên uncommitted/untracked work.
- Chưa có `.github/` hoặc workflow cũ; chỉ có `requirements.txt` với 6 runtime
  ranges, không có CI lock/Python version declaration/pytest config riêng.
- Personal origin đúng repository role; `git ls-remote --symref origin HEAD`
  xác nhận default branch **main**. Current working branch vẫn
  `refactor/crawl-runner-persistence`; không đổi branch hoặc lịch sử.
- Local baseline interpreter là Anaconda CPython **3.12.7 x64**, Windows 11 build
  26300; pytest **7.4.4**. Metadata SeleniumBase **4.54.11** yêu cầu Selenium
  **4.49.0** và pytest **9.1.1** trên Python >=3.11. Sao chép pytest 7.4.4 vào
  clean install sẽ tạo dependency conflict; không dùng `--no-deps` để bỏ qua.
- CI dùng Python **3.12.7 x64**, `windows-latest`, cùng browser/runtime direct
  versions đã quan sát; pytest 9.1.1 đáp ứng metadata và được kiểm chứng riêng.
  Không đổi dependency hoặc hành vi của production environment.
- Một số Overture tests phụ thuộc private local canonical catalog. Test module
  nay tự persist **30 synthetic places** vào `tmp_path`, dùng nguyên catalog
  reader/writer/match validators. Chỉ monkeypatch catalog selection trong test;
  không sửa frozen catalog/source code hoặc publish dữ liệu Overture thật.

## Files trong scope CI-01

| Path | Vai trò |
|---|---|
| `.github/workflows/harness-windows.yml` | PR + push main, Windows quality gate duy nhất |
| `requirements/ci-windows.in` | Audited direct inputs, tham chiếu existing production manifest |
| `requirements/ci-windows.txt` | 86 distributions pinned + selected SHA256; Windows CPython 3.12 only |
| `scripts/ci/windows_quality_gate.py` | Existing 17-suite command, native profile preflight, fail-on-skip/missing-suite, sanitized reports |
| `scripts/ci/offline_guard/sitecustomize.py` | Test-only audit hook chặn Python INET connect/bind/DNS; child interpreters kế thừa |
| `tests/test_ci_windows_quality_gate.py` | Workflow/report projection/skip/failure/lock/network/preflight contracts |
| `experiments/tiktok_access_benchmark/test_overture_study.py` | Synthetic persisted catalog fixture thay local runtime catalog dependency |
| `docs/reviews/global_crawler_harness_ci01.md` | Audit, evidence, operations và limitations |
| `docs/agent_state/current_task.md`, `docs/work_status.md` | Handoff và checkpoint |

Không sửa crawler/harness runtime, Google production, source/outbox/Parquet schemas,
profiles, credentials, existing requirements.txt hoặc baseline checksums.

## Workflow contract

- Stable required check: **Windows offline regression (Python 3.12.7)**.
- All PR base branches được test; push chỉ `main`. Không path filter để tránh
  required check bị pending vì workflow không chạy; không crawler/dispatch/CD job.
- Checkout/setup-python/upload-artifact dùng full Git commit SHA đã đọc từ public
  action remotes; v7 refs tại audit. `contents: read`, credentials persistence off,
  checkout clean off, không secrets input, không write token hoặc privileged PR event.
- Timeout 20 phút; concurrency cancel superseded runs; chỉ pip package cache.
- Install hash-locked manifest rồi `pip check`; không tự nâng SeleniumBase hoặc
  chọn dependency fallback. Python/version/package install failure làm job đỏ.
- Disable third-party pytest plugin autoload, dùng existing import mode và cache
  option; không xdist, parallel browser, automatic test retry hoặc `continue-on-error`.
- Helper chạy nguyên 17 files của C2c, với Python/env/temp paths cho CI và bổ sung
  JUnit/timing options. Cần >=472 cases, đầy đủ 17 modules và **zero skipped**.
  Unsupported native API không được skip để xanh; failures giữ nguyên failed exit.
- C2a/C2b/C2c suites thực thi real Windows Job Object/process-tree/Named Pipe/
  profile mutex/subprocess fixtures, nhưng driver/browser là fake. Không real Chrome.
- SQLite, Parquet, synthetic catalog, raw XML và process/profile fixtures ở fresh
  OS temp directory. Chỉ safe reports ở report directory được truyền rõ ràng.
- Python network audit guard áp dụng cả child interpreters. Đây là cooperative
  Python guard, **không phải OS firewall** chống native library/hostile test code.
  Actual test allowlist/reviewed fake providers vẫn là boundary quan trọng.

## Confirmed native compatibility limitation

Clean venv install + pip check thành công, nhưng lần regression đầu từ Windows
venv redirector cho **420 passed / 52 failed / 0 skipped**, 182.09s pytest time;
quality gate trả FAIL, không bị biến thành success.

Probe riêng giữ C2a process handle và đọc PID từ fake Python child: retained worker
PID khác actual interpreter PID; actual interpreter có parent là redirector PID.
Điều này giải thích `Worker initialization unavailable`, identity/quiescence và
breakaway mismatches trong profile đó. Không nới exact creation/pipe-owner checks.

CI helper nay từ chối `sys.prefix != sys.base_prefix` bằng failed preflight
`UNSUPPORTED_REDIRECTING_VENV`, không skip cases. GitHub workflow dùng trực tiếp
interpreter từ setup-python, không tạo venv. Direct CPython/native ownership vẫn
cần được chạy thật trên hosted runner trước khi xác nhận compatibility.

Local verification dùng direct MSI CPython 3.12.7 với **cùng hash-locked packages**
qua explicit `--dependency-root <isolated-venv>/Lib/site-packages`. Flag chỉ thay
Python package search path trong CI helper/test subprocesses; không thay launcher,
process handles, claims hoặc source code. Venv chỉ là nơi chứa isolated packages;
không dùng redirector làm managed worker. Đây không phải proof cho mọi Windows
Python distribution hoặc hosted image.

## Local evidence và failures đã điều tra

Evidence root: `output/ci01-2ac84b90/` (local/ignored; không publish directory này).

- Resolver dựa trên `.in` thành công; 86 distribution hashes ghi vào CI lock.
- Fresh venv install từ hash lock thành công; `pip check`: no broken requirements.
- `actionlint` **1.7.7**, Windows binary kiểm SHA256 từ release checksum: workflow
  lint pass. Không có actionlint trên PATH ban đầu; binary chỉ giữ trong output.
- Synthetic catalog fixture focused: **36 passed**, 2 existing Pandas warnings,
  6.54s trên original environment. Lần đầu 3 fail do nested validator còn dùng
  default catalog; đã sửa fixture để patch đúng storage DEFAULT_CATALOG, không
  sửa validator hoặc nới matching criteria.
- CI contracts: **10 passed**, 0.74s với pytest 9.1.1; báo cáo `contracts-final.xml`.
- Failed venv run giữ tại `local-gate/`; native identity probe tại
  `launcher-probe-2/result.json`. First rejected cross-executable probe cũng giữ
  nguyên; C2a từ chối đúng khi launcher được cấp một executable khác chính nó.
- Final direct-interpreter regression/evidence: xem phần kết quả cuối bên dưới.
- Frozen Google manifest **47/47 unchanged**. Whole-tree diff check vẫn có lỗi
  whitespace cũ tại `docs/component_registry.md:14`; không sửa unrelated work.

### Final local result

| Validation | Actual result |
|---|---|
| Actionlint 1.7.7, checksum-verified | PASS, exit 0 |
| CI contract tests, pytest 9.1.1 | **10 passed**, 0.74s |
| Required 17-suite regression, direct interpreter + locked packages | **472 passed**, **0 failed / 0 errors / 0 skipped**, pytest **168.51s** |
| Quality gate including projection | **PASS**, **169.984s**, no missing suites |
| Fresh hash-locked dependency install / pip check | PASS / no broken requirements |
| GitHub-hosted execution | **NOT RUN**, no run URL/ID |

Final sanitized files: `output/ci01-2ac84b90/direct-gate/{junit.xml,summary.json,summary.md}`.
Required dependency versions trong summary đúng CI direct pins, kể cả pytest 9.1.1.
Hai Pandas warnings cũ còn xuất hiện ở local direct profile vì optional numexpr/
bottleneck của Anaconda vẫn có thể được import; hosted image sạch không được coi
là đã kiểm chứng bởi local run này. Test allowlist dùng fake drivers và INET audit
guard trong cả parent/child; không có live crawl hoặc browser launch.

`verification.json` và `source_hashes.json` trong evidence root ghi nhận final local
outcome/CI files, M3-C2c runtime hash comparison và frozen manifest comparison.
Không upload các evidence-root files này tự động; workflow chỉ upload ba safe
report files của chính hosted job khi regression failure.

## Reports và privacy

GitHub failure artifact chỉ gồm ba allowlisted files:

1. `junit.xml`: module/base test name/case ordinal/time/status; generic failure text.
2. `summary.json`: counts, missing suites, gate status, timings và dependency/OS versions.
3. `summary.md`: human-readable outcome/timing.

Không upload raw JUnit/traceback/captured output/properties/test parameter values,
fixture dirs, screenshots, DOM, SQLite/Parquet, browser profiles, cookies, credential
files, pip logs, local agent snapshots hoặc runtime crawl evidence. Retention 7 ngày.
Step summary có timing kể cả success. Setup/install failures trước regression chỉ
có Actions step logs; không tạo crawler diagnostics giả để upload.

## Reproduction

Fresh **direct** CPython 3.12.7 x64 trên Windows, không dùng venv redirector:

```powershell
Set-Location '<repository-root>'
python -m pip install --require-hashes -r requirements/ci-windows.txt
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency install failed' }
python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflict' }
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$testRoot = Join-Path $env:TEMP ('ci-contract-' + [guid]::NewGuid().ToString('N'))
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp $testRoot tests/test_ci_windows_quality_gate.py
if ($LASTEXITCODE -ne 0) { throw 'CI contracts failed' }
$reports = Join-Path $env:TEMP ('harness-reports-' + [guid]::NewGuid().ToString('N'))
python -B scripts/ci/windows_quality_gate.py --reports $reports
```

Không chạy install command này trong production MSI environment để thay packages.
Dùng fresh standalone interpreter cho full CI profile. Local verification đã làm:

```powershell
& 'python' -B scripts/ci/windows_quality_gate.py `
  --dependency-root 'output/ci01-2ac84b90/venv/Lib/site-packages' `
  --reports 'output/ci01-2ac84b90/direct-gate'
```

Reports path phải fresh; runner từ chối overwrite. Lệnh reproduction cần đổi
reports path khi chạy lại. Raw failure XML chỉ còn local temp để debug, không nằm
trong upload allowlist. Existing suite list/command nằm trong `SUITES` của helper
và báo cáo C2c; không tự chạy `pytest` toàn repo vì có các browser/live opt-in tests.

Để cập nhật lock sau review: chạy pip dry-run `--ignore-installed --report` trên
Windows CPython 3.12.7 với `.in`, review distribution versions/hashes, regenerate
exact lock, clean install/pip check và chạy toàn bộ gate. Không refresh từ full
Anaconda freeze hoặc sửa frozen production requirements để giải quyết CI.

## Enable branch protection sau approval

1. Review/publish source và workflow trong personal repository theo ancestry
   policy riêng. Hiện nhiều harness/experiment/test files còn untracked; push
   workflow riêng sẽ không làm source đó xuất hiện trên hosted checkout.
2. Sau approval và user publish PR, đợi **một Actions run thật** trên Windows.
   Xác nhận không skip native cases, kiểm report/timing/OS; không tự bypass đỏ.
3. Settings → Rules / Rulesets hoặc Branch protection → target `main`.
   Require pull request và required status check
   **Windows offline regression (Python 3.12.7)** từ GitHub Actions.
4. Có thể require branch up-to-date trước merge. Không bật merge queue khi chưa
   thêm/review `merge_group` trigger. Không configure automatic bypass cho admin.
5. Không bật auto deployment, environment secrets, paid fallback hoặc crawler
   schedule trong CI-01. Quyền/quota Actions cần user/repository admin xác nhận.

Tham chiếu [GitHub branch protection/status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

## Limitations và CD prerequisites

- Hosted Actions **NOT RUN / UNVERIFIED**: không commit/push/dispatch được thực
  hiện; local success không thay hosted acceptance. Actions permissions/settings
  chưa được xác minh hoặc thay đổi qua authenticated API.
- `windows-latest` là moving image. OS build và action runner khác MSI; nếu
  Job Object/Global mutex/native identity không được hỗ trợ, fail và review blocker,
  không fallback Linux, skip tests, relax ownership hoặc tự sửa crawler.
- Dependency lock là riêng Windows CPython 3.12; direct runtime pins giữ nguyên,
  transitive pins và pytest CI profile khác original Anaconda. Chưa claim live
  browser compatibility hoặc cross-platform dependency compatibility.
- CI chỉ chứng minh offline data/control contracts. Không proof cho CAPTCHA-free
  rate, source coverage, live UC profile/driver, actual operator verification hoặc
  production scale. M3-C2d vẫn chưa triển khai.
- CD cần separate approval, reproducible packaging/config, reviewed M3-C2d/live
  safety/verification/rollback contracts và first hosted green run. Không triển
  khai CD hoặc crawl tự động từ quality gate này.

Primary references: [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[hosted Windows runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[setup-python](https://github.com/actions/setup-python),
[artifact inputs/retention](https://github.com/actions/upload-artifact),
[actionlint](https://github.com/rhysd/actionlint).
