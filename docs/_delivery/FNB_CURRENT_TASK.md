> Historical planning baseline retained; authoritative checkpoint: [CURRENT_TASK.md](CURRENT_TASK.md).

# Active Task — M0-AUDIT

State: NOT_STARTED
Mode: READ_ONLY
Outcome: Verify current feature branch code, tracked/untracked/ignored assets and how transferred crawler can be reused safely.

## Scope
- `README.md`, `REUSE_MANIFEST.json`, `pyproject.toml`, `src/`, `pipelines/`, `crawl_experiment/`, `scripts/`, `config/`, `tests/` and Git inventory.
- Read `docs/19_REPOSITORY_INTEGRATION.md` and `docs/09_ROADMAP.md` only as needed.

## Done when
- Accurate repo inventory and code reuse map; unknowns explicitly marked.
- At-risk state/data identified with backup recommendations.
- Narrow M1 first implementation task proposed with required tests.

## Explicit exclusions
No file modifications (including this task file), no installs, no live crawl, no branch or worktree mutation, no data deletion.

## On completion
Return audit in chat for owner approval. The **next approved write task** may update STATUS and replace this file with the next task card.

## Task size rule for future work
Each task has one outcome, 2–5 acceptance criteria, relevant paths, and exact exclusions. Prefer <= 1 work session; split tasks that expand into separate concerns.
