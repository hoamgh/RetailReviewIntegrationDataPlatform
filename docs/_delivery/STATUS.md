# Delivery status

Updated: 2026-10-28. Branch: feature/fnb-data-platform.
Current task: [CURRENT_TASK.md](CURRENT_TASK.md).

## VERIFIED
- M0/M0.1 completed in chat; existing dirty/untracked/deleted assets retained.
- M0.1: Bronze 378,036 rows; catalog 682 unique non-null keys; counts, hashes,
  schema, boundary and source metadata agree. This is not coverage evidence.
- Baseline committed locally: 53f72e0b7776792e7d67c363391f24cfc64a05aa.
- M0.2B: runtime ignore protections, pinned offline dependency closure, focused
  network-blocked runner, canonical checkpoints and staging whitelist added.
- 31 tests passed, 2 optional pandas dependency warnings, 0.82s on CPython 3.12.7.
- Executed with existing packages, no installation (PowerShell):
```powershell
& C:/Users/MSI/anaconda3/python.exe -B -c "import sys,runpy; sys.path.insert(0,r'D:\Projects\integration-recomentdation\output\ci01-2ac84b90\venv\Lib\site-packages'); runpy.run_path('scripts/run_offline_tests.py',run_name='__main__')"
```
- Archive branch preserved: archive/legacy-crawlers-20261028 at 2f0f8ec.
- M0.6A: .github/workflows/fnb-offline-tests.yml added; Windows/Python 3.11,
  push/PR/manual triggers, contents: read, 15-minute timeout, install .[test].
- Workflow YAML parsed and settings checked against pyproject.toml; hosted run
  and fresh Python 3.11 dependency installation remain UNVERIFIED.
- M0.6A implementation: no crawler edits, push, installation or live access.
  Only CI and canonical checkpoints are approved for the local CI commit.

## UNVERIFIED / BLOCKED
- Full private backup skipped by owner; only destination ACL was restricted.
  Ignored data, profiles, SQLite/WAL and uncommitted legacy edits have no verified
  backup from this session. Do not clean/reset/delete them.
- Clean-environment installation and package artifact hashes not verified.
  Optional download dependencies are not transitively locked; downloads mocked.
- 107 existing deletions, crawler changes and six uncertain additions remain
  outside the staging whitelist. The worktree is not clean.
- Entrypoint/test origins remain unverified in REUSE_MANIFEST.json.
- AGENTS.md remains locally ignored under existing publication policy.
- M1 not started; source rights/live gates remain unverified.

## Next exact action
Resume M0.6 retirement checks separately after the approved local CI commit.
GitHub-hosted execution still needs evidence.
Do not stage deletions or start M1 without the relevant task approval.
