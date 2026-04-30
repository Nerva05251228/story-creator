# Story Creator Refactor Master Plan

Date: 2026-04-30

## Purpose

This document is the controlling plan for refactoring Story Creator from a large local backup-style codebase into a maintainable product codebase.

The target architecture is:

- FastAPI backend with clear router, service, repository, worker, and infrastructure boundaries.
- PostgreSQL as the source of truth.
- Redis for short-lived locks, queues, rate limits, notifications, and cache only.
- Vue 3 + Vite + TypeScript + shadcn-vue frontend, migrated incrementally from the current static frontend.
- Private configuration stored outside git in `.env`.
- Public repository readiness: no secrets, private endpoints, tokens, passwords, or production-only addresses committed.

## Working Rules

- Every implementation step starts with a written plan and requires explicit approval before edits.
- Parallel subagents should be used for independent analysis or implementation slices.
- Completed subagents are closed and never reused for new tasks.
- The lead agent reviews, integrates, and verifies all subagent output.
- No commit or push to `main` happens without first showing the exact change summary and receiving approval.
- Every commit-ready batch must update `CHANGELOG.md`.
- Large functions, large modules, and mixed-responsibility files must be split by responsibility.
- Reusable code is preferred when it reduces coupling or duplication, but not at the cost of premature abstraction.

## Current Baseline

Observed current shape:

- `backend/main.py` is over 21,000 lines and combines app creation, routes, schemas, runtime migration, task orchestration, file IO, external API calls, and startup behavior.
- `frontend/js/app.js` is over 16,000 lines and combines global state, DOM rendering, event handling, API calls, polling, and workflow logic.
- The frontend is static HTML/JS/CSS with inline scripts across management pages.
- Startup scripts hardcode database URLs and service tokens.
- Background work is handled by poller processes, database state, Python threads, and process-local locks.
- Tests exist, but API contracts, permission contracts, PostgreSQL integration, browser regression, and queue concurrency behavior are under-covered.

Current worktree notes:

- `requirements.txt` has local dependency additions made during setup so the project can run in the new virtual environment.
- `docs/simple-storyboard-params-guide.md` is currently deleted in the worktree. This plan does not restore or finalize that deletion.

## Non-Negotiable Security Direction

All private values must move to `.env` and must not be committed.

This includes:

- Database URLs and credentials.
- Redis URLs and credentials.
- Image generation service base URLs and tokens.
- LLM/text relay base URLs and API keys.
- Video generation service base URLs and tokens.
- CDN upload/read base URLs and tokens.
- TTS/voiceover service URLs and tokens.
- Admin secrets, JWT/session secrets, and master passwords.
- Any production hostnames, private relay addresses, or vendor-specific credentials.

The repository should only contain:

- `.env.example` with safe placeholder values.
- Documentation describing each variable.
- Startup scripts that read environment variables and fail fast when required secrets are missing.

Existing committed secrets should be treated as compromised and rotated before the repository is made public.

## Target Directory Shape

Backend target shape:

```text
backend/
  app/
    main.py
    settings.py
    logging.py
    security.py
  api/
    routers/
  schemas/
  domain/
    services/
  infra/
    db/
    clients/
    redis/
  workers/
  migrations/
```

Frontend target shape:

```text
frontend/
  legacy/
  web/
    package.json
    vite.config.ts
    src/
      app/
      pages/
      features/
      entities/
      shared/
```

Docs target shape:

```text
docs/
  refactor/
  dev-setup.md
  configuration.md
  runtime-architecture.md
  migrations.md
  security.md
  deployment.md
```

## Phases

### Phase 0: Baseline and Safety Net

Goal: freeze current behavior before moving code.

Deliverables:

- Route registry test that fails on duplicate method/path pairs.
- OpenAPI snapshot or route list snapshot.
- Admin/auth allowlist and permission test plan.
- Browser smoke test for current login and main page.
- Current API/localStorage/route/poller inventory.
- `CHANGELOG.md` kept current.

Exit criteria:

- Existing Python and JS tests run from documented commands.
- New route duplication test detects current duplicate routes.
- No external network calls are required for baseline tests.

### Phase 1: Security and Configuration Stopgap

Goal: remove public-repository blockers and immediate security risks.

Deliverables:

- `.gitignore` ignores `.env`, `.env.*`, except `.env.example`.
- `.env.example` documents safe placeholders.
- Startup scripts read from `.env` or environment variables.
- Hardcoded secrets and private URLs removed from source and scripts.
- Admin endpoints require server-side admin authorization.
- User password plaintext is no longer returned.
- File serving validates canonical paths under allowed directories.
- CORS and host binding are configurable.

Exit criteria:

- `git grep` finds no real `sk-` tokens, production passwords, private relay URLs, or admin passwords.
- `/api/admin/*` is protected by tests.
- Path traversal requests return 403 or 404.

### Phase 2: Backend Boundary Refactor

Goal: split `backend/main.py` without changing behavior.

Deliverables:

- `create_app()` entrypoint with router registration.
- Domain routers moved into `backend/api/routers/`.
- Pydantic request and response models moved into `backend/schemas/`.
- Business logic moved into focused services.
- Database access moved behind repositories or transaction helpers where useful.
- Importing the app does not run migration, network prewarm, or pollers.

Exit criteria:

- `backend/main.py` is reduced to app assembly and compatibility glue.
- All routes still exist unless explicitly removed by an approved plan.
- API contract tests pass.

### Phase 3: Migrations and Database Discipline

Goal: replace runtime DDL with explicit versioned migrations.

Deliverables:

- Alembic or equivalent migration runner.
- Baseline migration for the current PostgreSQL schema.
- Runtime `ALTER TABLE` logic removed from app import/startup.
- Seed data separated from schema migration.
- PostgreSQL integration test database workflow.

Exit criteria:

- Fresh PostgreSQL database can migrate to head.
- Existing database can upgrade idempotently.
- `preflight.py` checks migration status and runs approved seed operations only.

### Phase 4: Worker and Redis Architecture

Goal: make long-running jobs robust and horizontally safe.

Deliverables:

- Unified task contract: create, claim, heartbeat, retry, cancel, complete, fail.
- PostgreSQL remains the durable state store.
- Redis is introduced for queue wakeups, leases, locks, short-term cache, and rate limits.
- Workers are separated from web process lifecycle.
- Idempotency keys prevent duplicate external submissions and duplicate billing.

Exit criteria:

- Two pollers/workers can run without duplicate task claims.
- Worker crash recovery is tested.
- Redis outage has defined behavior.

### Phase 5: Vue/shadcn-vue Frontend Migration

Goal: migrate incrementally while preserving current workflows.

Deliverables:

- Vite/Vue/TypeScript app under `frontend/web`.
- shadcn-vue, Tailwind, Vue Router, Pinia, and API client foundation.
- `/app-v2` runs alongside legacy `/app`.
- Login and low-risk list pages migrate first.
- Core creation workflows migrate by feature slice.
- Admin pages migrate after server-side admin authorization is fixed.

Exit criteria:

- Legacy `/app` remains available until `/app-v2` passes user acceptance.
- No route leaks duplicate polling after navigation.
- Browser regression tests cover critical workflows.

### Phase 6: Cleanup and Public Repository Readiness

Goal: remove legacy exposure and prepare for public hosting.

Deliverables:

- Remove tracked backup files and exposed legacy artifacts after migration.
- Move legacy assets to `frontend/legacy` or delete by approved plan.
- README becomes quickstart only.
- Configuration, security, deployment, and migration docs are complete.
- Changelog reflects every user-visible or operational change.

Exit criteria:

- Public repository scan finds no private data.
- Fresh setup from README and `.env.example` succeeds.
- Legacy static pages are either removed or intentionally isolated.

## Review Gates

Each phase must have:

- A specific implementation plan.
- One or more fresh subagents for independent implementation slices when useful.
- Lead review of every patch.
- Tests or manual verification commands listed before execution.
- `CHANGELOG.md` update.
- User approval before commit or push.
