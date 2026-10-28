# Proposed baseline staging plan — approval required

Nothing staged or committed. This additive baseline leaves legacy changes and
all deletions outside the index. It does not produce a clean working tree.

## Exact whitelist

```powershell
git add -- `
  ".gitignore" `
  "README.md" `
  "NOTICE.md" `
  "REUSE_MANIFEST.json" `
  "pyproject.toml" `
  "requirements/offline-baseline.txt" `
  "scripts/run_offline_tests.py" `
  "config/admin_areas/phuong_khanh_hoi.geojson" `
  "config/admin_areas/phuong_khanh_hoi_metadata.json" `
  "src/fnb_platform/__init__.py" `
  "src/fnb_platform/catalog.py" `
  "src/fnb_platform/categories.py" `
  "src/fnb_platform/models.py" `
  "src/fnb_platform/prepare_catalog.py" `
  "tests/test_catalog.py" `
  "tests/test_categories.py" `
  "tests/test_prepare_catalog.py" `
  "docs/00_DOCS_INDEX.md" `
  "docs/01_BRD.md" `
  "docs/02_PRD.md" `
  "docs/03_SRS.md" `
  "docs/04_USE_CASES.md" `
  "docs/05_SYSTEM_ARCHITECTURE.md" `
  "docs/06_DATA_MODEL.md" `
  "docs/07_PIPELINE_DESIGN.md" `
  "docs/08_TEST_PLAN.md" `
  "docs/09_ROADMAP.md" `
  "docs/10_DATA_SOURCES.md" `
  "docs/11_ENTITY_RESOLUTION.md" `
  "docs/12_ORCHESTRATION_OPERATIONS.md" `
  "docs/13_MONITORING_OBSERVABILITY.md" `
  "docs/14_PERFORMANCE_BENCHMARK.md" `
  "docs/15_ADR_TECHNOLOGY_DECISIONS.md" `
  "docs/16_GOVERNANCE_SECURITY.md" `
  "docs/17_REVIEWS_RECOMMENDATION.md" `
  "docs/18_DATA_PRODUCTS_CONSUMPTION.md" `
  "docs/19_REPOSITORY_INTEGRATION.md" `
  "docs/20_CODEX_WORKFLOW.md" `
  "docs/_delivery/STATUS.md" `
  "docs/_delivery/CURRENT_TASK.md" `
  "docs/_delivery/FNB_STATUS.md" `
  "docs/_delivery/FNB_CURRENT_TASK.md" `
  "docs/_delivery/BASELINE_STAGING.md" `
  "pipelines/bronze/.gitkeep" `
  "pipelines/entity_resolution/.gitkeep" `
  "pipelines/gold/.gitkeep" `
  "pipelines/recommendation/.gitkeep" `
  "pipelines/silver/.gitkeep" `
  "pipelines/sources/.gitkeep"
```

After approval, inspect `git diff --cached --name-status` and
`git diff --cached --check` against this whitelist. Stop if unrelated entries
are already staged; never unstage/reset them automatically. Review staged content
locally for sensitive data. Suggested commit title (separate approval required):
`chore: establish minimal F&B project baseline`

## Preserve outside this commit

- All 107 pre-existing deletions; no deletion approval is implied.
- Every crawl_experiment/ change, including cli.py, nine normalization-only
  paths, and three untracked crawler additions.
- scripts/benchmark_google_workers.ps1, scripts/google_live_workers.py,
  scripts/google_overture_workers.py.
- Ignored data/, output/, runtime state, profiles, checkpoints and local AGENTS.md.
- archive/legacy-crawlers-20261028 and every existing worktree.

## Reproducibility limits

Core/test dependency versions and build backend are pinned. The focused runner
blocks socket access. No installation, artifact hash lock, clean clone validation,
or full backup was performed. A fresh environment must supply the pinned packages;
downloading them is not authorized here. The optional Overture extra has only a
direct pin, outside the offline closure. M1/cleanup require separate approval.
