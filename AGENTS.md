# Agent Instructions

Read `AI_EXECUTION_BACKLOG_GIS_COLLABORATION.md` completely before making changes.
It is the authoritative mission, architecture, task backlog, and acceptance contract.

Every run must be resumable, idempotent, and auditable:

1. Inspect git status, the tree, this file, the backlog, `README.md`, and `.agent` state.
2. Preserve uncommitted user work and choose the highest-priority unblocked task.
3. Acquire `.agent/locks/run.lock` before any state or dataset write.
4. Never invent source identifiers or measured results and never persist an API key.
5. Write datasets and state atomically; validate before replacing final outputs.
6. Run relevant tests, Ruff checks, and formatting checks.
7. Update `.agent/state.json`, `.agent/backlog.json`, and `.agent/RUNLOG.md`.
8. Commit a completed atomic task locally only when unrelated user changes are absent.

Valid task states are `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `SKIPPED`, and `STALE`.
A task is `DONE` only after its acceptance checks pass.
