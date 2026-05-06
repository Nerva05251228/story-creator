# Refactor Progress and Forward Plan

Date: 2026-05-05

## Purpose

This document is the current handoff for the Story Creator refactor.

It consolidates:

- what has already been completed on the current refactor branch
- what the codebase looks like right now
- what still remains across backend, runtime, validation, and frontend work
- what the next implementation batches should be
- what rules a future agent must follow

## Source Documents Reviewed

- `docs/refactor/2026-04-30-master-plan.md`
- `docs/refactor/2026-04-30-backend-architecture-plan.md`
- `docs/refactor/2026-04-30-validation-plan.md`
- `docs/refactor/2026-04-30-frontend-vue-shadcn-plan.md`
- `docs/refactor/2026-04-30-risk-register.md`
- `CHANGELOG.md`

No `docs/refactor/2026-05-01-refactor-handoff.md` file was present at the time of this update.

## Current Progress Snapshot

Current branch state:

- Branch: `codex/subject-card-router`
- `git status --short`: clean
- `origin/main...HEAD`: `0 0`
- Current `HEAD`: `2d964a7 refactor: extract storyboard2 video task helpers`

Current verified file sizes:

- `backend/main.py`: 11517 lines
- `backend/api/routers/episodes.py`: 2613 lines
- `backend/api/routers/storyboard2.py`: 1716 lines
- `backend/api/routers/voiceover.py`: 899 lines
- `backend/api/routers/simple_storyboard.py`: 458 lines
- `frontend/js/app.js`: 37271 lines
- `frontend/web/`: not present yet

Current `storyboard2` service modules:

- `storyboard2_board.py`: 461 lines
- `storyboard2_media.py`: 32 lines
- `storyboard2_permissions.py`: 44 lines
- `storyboard2_reference_images.py`: 131 lines
- `storyboard2_video_tasks.py`: 51 lines

Latest validation status from the refactor batches:

- focused service and import-contract tests were added for each extracted slice
- `python -m unittest discover -s tests -p "test_*.py"` passed with 491 tests
- `python -m unittest tests.test_route_registry` passed with 36 tests
- `python -m py_compile ...` passed for touched modules
- `git diff --check` and `git diff --cached --check` passed on each code batch
- `python backend/preflight.py check` passed

## Completed Refactor Work

### Baseline Completed Before This Handoff

The current branch already includes the following refactor batches in the recent history:

- `18f4172` refactor: extract storyboard reference asset helpers
- `b5a3695` refactor: extract voiceover data merge helpers
- `707e944` refactor: extract voiceover tts helpers
- `dc3e599` refactor: extract voiceover shared data helpers
- `3d16fde` refactor: extract voiceover router
- `9beac08` refactor: extract storyboard sync service
- `42c88ac` refactor: extract simple storyboard router
- `405cc82` refactor: extract storyboard2 router
- `527920a` refactor: extract storyboard excel router
- `9caafb9` refactor: extract managed generation and shot detail services
- `64b0d70` refactor: extract shot reference workflow service
- `bfa4048` refactor: extract storyboard video generation limit helpers
- `51a6963` refactor: extract storyboard prompt context helpers
- `cf4bcd0` refactor: extract storyboard sound card helpers
- `dc96268` refactor: extract storyboard2 reference image helpers
- `4d1aa48` refactor: centralize episode storyboard cleanup helpers
- `86fc6da` refactor: extract storyboard video prompt builder
- `f668263` refactor: centralize storyboard shot materialization
- `aeef3d8` refactor: extract storyboard2 board service
- `cc6e7ec` refactor: extract storyboard2 media helpers
- `c52e150` refactor: extract storyboard2 permission helpers
- `2d964a7` refactor: extract storyboard2 video task helpers

### What Those Batches Achieved

Routers already extracted from the old monolith:

- `auth`
- `admin_users`
- `billing`
- `card_media`
- `dashboard`
- `hit_dramas`
- `libraries`
- `managed_generation`
- `model_configs`
- `scripts`
- `settings`
- `shots`
- `simple_storyboard`
- `storyboard2`
- `storyboard_excel`
- `subject_cards`
- `templates`
- `video`
- `voiceover`

Service boundaries already in place:

- billing charge helpers
- card image generation helpers
- episode cleanup helpers
- managed generation helpers
- model config helpers
- shot image generation helpers
- shot reference workflow helpers
- simple storyboard batch helpers
- storyboard defaults
- storyboard prompt context
- storyboard reference assets
- storyboard sound cards
- storyboard sync
- storyboard video generation limits
- storyboard video payload
- storyboard video prompt builder
- storyboard video settings
- storyboard2 board
- storyboard2 media
- storyboard2 permissions
- storyboard2 reference images
- storyboard2 video tasks
- voiceover shared data helpers

## Current Architecture State

### `backend/main.py`

Positive state:

- no direct `@app.get`, `@app.post`, `@app.patch`, or `@app.delete` route decorators remain
- many route implementations have been moved out to routers and services
- compatibility exports exist so older direct callers still resolve

Why it is still large:

- `create_app()` is still not introduced
- startup/bootstrap logic still lives here
- runtime DDL and `ensure_*` schema mutation helpers still live here
- many compatibility aliases still live here
- old transaction/runtime helpers still live here
- `@app.on_event("startup")` and `@app.on_event("shutdown")` lifecycle code still lives here

### `backend/api/routers/episodes.py`

Current state:

- it is no longer the 8k-line risk it once was
- it still owns several mixed workflows in one router

Largest remaining responsibility clusters:

- narration and opening generation
- detailed storyboard generation and analysis
- storyboard table update and create-from-storyboard flows
- batch sora prompt generation
- batch sora video generation
- managed generation start and refresh
- export helpers
- duplicated SQLite commit retry helpers

### `backend/api/routers/storyboard2.py`

Current state:

- board state, media normalization, permissions, reference image selection, and video task naming/CDN handling have already been split out
- the router is much smaller than before, but still not yet "finished"

Largest remaining responsibility clusters:

- prompt task submission and prompt batch state refresh
- active image task tracking and orphan recovery
- image generation orchestration
- video polling, recovery, and processing sync
- debug payload persistence

### `backend/api/routers/voiceover.py`

Current state:

- voiceover is now a dedicated router instead of living in `main.py`
- it is still large enough to need a second-stage split

Largest remaining responsibility clusters:

- shared data update/read
- voice reference CRUD and preview
- vector preset CRUD
- emotion audio preset CRUD
- setting template CRUD
- line/all generation queueing
- TTS status reporting

### Frontend

Current state:

- frontend is still the legacy static HTML/JS/CSS stack
- `frontend/js/app.js` is 37271 lines
- the Vue 3 + Vite + TypeScript + shadcn-vue migration has not started in code
- `frontend/web/` does not exist yet

## Remaining Work By Phase

### Phase 0: Safety Net

Status: partial

Already improved:

- route registry coverage exists
- many focused service/import-contract tests exist

Still missing or underpowered:

- OpenAPI snapshot or route snapshot
- browser regression coverage
- full permission matrix coverage
- repeatable secret scan in validation flow
- stronger PostgreSQL integration coverage

### Phase 1: Security and Configuration

Status: partial

Still needed:

- typed settings module
- `.env.example` audit and cleanup
- removal of remaining hardcoded private defaults and private topology assumptions
- stronger admin/secret/public-repo readiness checks

### Phase 2: Backend Boundary Refactor

Status: active and materially advanced

Already achieved:

- direct route code is largely out of `main.py`
- many duplicate helper families have been moved into services
- router ownership is much clearer

Still needed:

- finish splitting `episodes.py`
- finish splitting `storyboard2.py`
- split `voiceover.py` further
- extract shared infra helpers like `db_commit_retry`
- introduce `create_app()`
- reduce `main.py` to app assembly, startup glue, and compatibility surface only

### Phase 3: Migrations and Database Discipline

Status: not complete

Still needed:

- formal migration runner
- baseline migration set
- removal of runtime DDL from import/startup path
- preflight/migration separation

### Phase 4: Worker and Redis Architecture

Status: not complete

Still needed:

- durable task contract
- atomic claim/heartbeat/retry/cancel semantics
- idempotency keys for external submissions and billing
- worker/web lifecycle separation
- Redis used only for queue/lock/cache/rate-limit roles

### Phase 5: Frontend Migration

Status: planned only

Still needed:

- scaffold `frontend/web`
- Vite/Vue/TypeScript setup
- auth shell and shared API client
- incremental route migration under `/app-v2`
- Playwright regression coverage
- cutover plan from legacy `/app` to Vue frontend

## Next Recommended Implementation Batches

### Immediate Backend Batches

1. `db_commit_retry` service

Why:

- duplicated in `main.py`, `episodes.py`, and `simple_storyboard.py`
- small, low-risk, clear ownership

Expected files:

- new `backend/api/services/db_commit_retry.py`
- updates to `backend/main.py`
- updates to `backend/api/routers/episodes.py`
- updates to `backend/api/routers/simple_storyboard.py`
- focused service + import-contract tests

2. `storyboard2_image_task_state` service

Why:

- `storyboard2.py` still owns process-local active image task tracking and orphan recovery
- this is a natural follow-on after `storyboard2_permissions` and `storyboard2_video_tasks`

Expected scope:

- `_mark_storyboard2_image_task_active`
- `_mark_storyboard2_image_task_inactive`
- `_is_storyboard2_image_task_active`
- `_recover_orphan_storyboard2_image_tasks`

3. `storyboard2_video_polling` service

Why:

- the largest remaining complexity in `storyboard2.py` is video polling/sync state
- moving it after `storyboard2_video_tasks` keeps the split incremental

Expected scope:

- `_poll_storyboard2_sub_shot_video_status`
- `_recover_storyboard2_video_polling`
- `_sync_storyboard2_processing_videos`

4. `episodes_text_generation` or equivalent service split

Why:

- `episodes.py` still mixes episode CRUD with narration/opening/storyboard text generation orchestration

Candidate scope:

- `_submit_episode_text_relay_task`
- `_submit_detailed_storyboard_stage1_task`
- narration/opening prompt helpers

5. `episodes_storyboard_runtime` or equivalent service split

Why:

- prompt batch state, stale task repair, and runtime flag reconciliation are still grouped in the router

Candidate scope:

- `_refresh_episode_batch_sora_prompt_state`
- `_repair_stale_storyboard_prompt_generation`
- `_reconcile_episode_runtime_flags`

### Near-Term Structural Batches

6. `voiceover.py` second-stage split

Recommended split directions:

- reference/preset/template CRUD service/router helpers
- generation queueing helpers
- TTS status/reporting helpers

7. `create_app()` and typed settings

Why:

- this is the main remaining reason `main.py` still behaves like an assembly/runtime hybrid

Expected result:

- `backend/app/create_app()` or equivalent assembly entrypoint
- `main.py` becomes a compatibility shim plus minimal startup glue

### Medium-Term Architecture Batches

8. Runtime DDL to formal migrations

Why:

- current `ensure_*` bootstrap path is still a major architectural smell and deployment risk

9. Worker/task contract redesign

Why:

- long-running media/generation flows still rely on app-process lifecycle and mixed polling semantics

10. Frontend Vue scaffold

Why:

- frontend remains the largest single-file risk in the repo
- until `frontend/web` exists, frontend refactor is still only a plan

## Verification Commands

Use this environment for current backend validation:

```powershell
$env:PYTHONUTF8='1'
$env:DATABASE_URL='sqlite:///D:/Software/0_Others/desktop/story-creator/backend/story_creator.db'
```

Focused batch validation:

```powershell
python -m unittest <focused test modules>
python -m unittest tests.test_route_registry
python -m py_compile <touched python files>
git diff --check
```

Full validation for commit-ready backend batches:

```powershell
python -m unittest discover -s tests -p "test_*.py"
$env:APP_ROLE='preflight'
$env:ENABLE_BACKGROUND_POLLER='0'
python backend/preflight.py check
git diff --cached --check
```

If a batch is docs-only, at minimum run:

```powershell
git diff --check
```

## Changelog Expectations

Every commit-ready batch updates `CHANGELOG.md`.

Each batch report should state:

- files changed
- tests run
- failures or skipped checks
- behavior changed
- security impact
- migration impact
- changelog entry

## Constraints To Preserve

These rules must continue to hold for future batches:

- Do not inspect `.env` or expose private config.
- Real URLs, tokens, passwords, and private topology must live only in local `.env`.
- `.env.example` may contain placeholders only.
- Preserve API paths, HTTP methods, response shapes, and authorization behavior unless an explicitly approved bug fix requires a change.
- Preserve compatibility aliases while old direct-call surfaces still exist.
- Before each modification, state the plan, files involved, and acceptance criteria.
- Use multiple fresh subagents for independent read-only analysis or implementation tasks.
- Close completed subagents and never reuse them for new tasks.
- Subagents must not write the same files in the same batch.
- Lead agent must review, integrate, and verify all subagent output.
- Keep commit batches small and reversible.
- Delete redundant code only after confirming it is no longer referenced.
- Do not replace one giant file with a different giant file.
- Prefer services with clear single-responsibility boundaries over dumping helpers into a catch-all module.

## New Thread Prompt

```text
You are a senior engineering-focused coding agent working in:
D:\Software\0_Others\desktop\story-creator

Use karpathy-guidelines.

First steps:
- Confirm branch and worktree with `git status --short --branch`.
- Do not inspect `.env` or print private config values.
- Real URLs, tokens, passwords, database credentials, Redis credentials, CDN credentials, LLM/video/image/TTS credentials, and admin secrets must stay only in local `.env`.

Read these documents first:
- docs/refactor/2026-04-30-master-plan.md
- docs/refactor/2026-04-30-backend-architecture-plan.md
- docs/refactor/2026-04-30-validation-plan.md
- docs/refactor/2026-04-30-frontend-vue-shadcn-plan.md
- docs/refactor/2026-04-30-risk-register.md
- docs/refactor/2026-05-05-refactor-progress-forward-plan.md
- CHANGELOG.md
- If it exists, also read docs/refactor/2026-05-01-refactor-handoff.md

Current branch baseline:
- Branch should be `codex/subject-card-router`
- Latest pushed commit: `2d964a7 refactor: extract storyboard2 video task helpers`
- `origin/main` and the branch were aligned at handoff time

Current verified sizes at handoff:
- backend/main.py: 11517 lines
- backend/api/routers/episodes.py: 2613 lines
- backend/api/routers/storyboard2.py: 1716 lines
- backend/api/routers/voiceover.py: 899 lines
- backend/api/routers/simple_storyboard.py: 458 lines
- frontend/js/app.js: 37271 lines
- frontend/web does not exist yet

Important current architecture facts:
- `backend/main.py` no longer has direct `@app.get/post/patch/delete` route decorators.
- `create_app()` is still not done.
- `backend/main.py` is still large because it still contains runtime DDL/bootstrap `ensure_*` logic, startup/shutdown behavior, and compatibility exports.
- `episodes.py` remains the main backend router risk.
- `storyboard2.py` is much smaller than before but still contains polling/image orchestration state.
- `voiceover.py` is already extracted as a router but still large enough for a second-stage split.

Recently completed extractions include:
- voiceover shared helpers and voiceover router
- simple storyboard router
- storyboard2 router
- storyboard excel router
- managed generation and shot detail services
- shot reference workflow service
- storyboard video generation limit helpers
- storyboard prompt context helpers
- storyboard sound card helpers
- storyboard2 reference images
- episode cleanup helpers
- storyboard video prompt builder
- storyboard shot materialization
- storyboard2 board
- storyboard2 media
- storyboard2 permissions
- storyboard2 video tasks

Next batch selection guidance:
- Prefer a small, complete, testable, reversible slice.
- Recommended next candidates:
  1. `db_commit_retry` shared service from main.py + episodes.py + simple_storyboard.py
  2. `storyboard2_image_task_state` helpers
  3. `storyboard2_video_polling` helpers
  4. `episodes` text/storyboard runtime helpers
  5. `voiceover.py` second-stage split
- Do not start with frontend migration unless specifically asked.

Hard rules for working style:
- Before any edit, explain:
  - the plan
  - files involved
  - acceptance criteria
- Use multiple fresh subagents for independent read-only analysis or implementation tasks.
- Completed subagents must be closed and never reused.
- Subagents must not write the same files in the same batch.
- Main agent must do final review, integration, and verification.
- Keep batches small. Do not do a giant rewrite.
- Every commit-ready batch must update CHANGELOG.md.
- Preserve API paths, methods, response structures, and auth behavior unless there is an approved bug fix.
- Confirm there are no remaining references before deleting redundant code.
- Do not move code into a new giant catch-all file.

Validation expectations for each commit-ready backend batch:
- Set:
  $env:PYTHONUTF8='1'
  $env:DATABASE_URL='sqlite:///D:/Software/0_Others/desktop/story-creator/backend/story_creator.db'
- Run focused unittest modules first
- Run:
  python -m unittest tests.test_route_registry
  python -m py_compile <touched files>
  git diff --check
  python -m unittest discover -s tests -p "test_*.py"
  $env:APP_ROLE='preflight'
  $env:ENABLE_BACKGROUND_POLLER='0'
  python backend/preflight.py check
  git diff --cached --check

Git workflow expectations:
- Work on `codex/subject-card-router`
- Make small commits
- Push branch after a validated batch
- If `git rev-list --left-right --count origin/main...HEAD` is `0 1`, also fast-forward sync `origin/main` with `git push origin HEAD:main`
- Never force-push `main`

Interpretation rule:
- When the user says "continue", finish the current batch end-to-end: implement, validate, commit, push branch, and if fast-forward-safe sync `main`, then move to the next batch unless the user pauses or redirects.
```
