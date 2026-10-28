# Current task — M0.6A F&B Offline CI

State: IMPLEMENTED_AND_LOCALLY_VERIFIED; local CI/checkpoint commit approved.

## VERIFIED
- Workflow: .github/workflows/fnb-offline-tests.yml; push, pull_request, manual.
- Windows latest, Python 3.11, contents: read, 15-minute timeout, no services.
- Installs package + declared test extra via python -m pip install ".[test]".
- Runs scripts/run_offline_tests.py; no download extra or external data required.
- YAML parsed with PyYAML; workflow settings/test extra checked against TOML.
- Local existing CPython 3.12.7 environment: 31 passed, 2 warnings, 0.82s.
- All 10 modified and 6 untracked crawler/script files retain their byte hashes.

## UNVERIFIED
Fresh Python 3.11 install and GitHub-hosted workflow execution; no packages installed.
Ignored production datasets untouched; tests create isolated ignored fixture evidence.

## Next action
Resume M0.6 legacy retirement gates in a separate task. Hosted execution still
needs verification. This commit covers only CI and the two canonical checkpoints;
no legacy deletions, crawler changes, push or M1 work are authorized here.
