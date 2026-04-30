# Refactor Validation Plan

Date: 2026-04-30

## Goal

Create a verification path that makes large backend and frontend refactors safe.

## Current Test Baseline

Observed coverage:

- Python tests use `unittest` and focus on services, migrations, helpers, billing, dashboard, storyboard, video, and startup behavior.
- JavaScript tests mainly use Node, `assert`, source-file reads, and VM execution.
- HTTP API contract coverage is limited.
- Real PostgreSQL integration coverage is limited.
- Browser regression coverage is not configured.
- CI configuration is not present.

## Required Safety Net Before Major Refactor

### Route and API Contracts

Add tests for:

- Duplicate method/path route detection.
- OpenAPI or route list snapshot.
- Status codes and response fields for critical endpoints.
- Error response shape.

Critical API areas:

- auth
- admin
- scripts
- episodes
- library/cards
- storyboard
- storyboard2
- media files
- image generation
- video generation
- voiceover
- billing
- dashboard
- model config
- hit dramas

### Permission Contracts

Add tests for:

- unauthenticated request
- authenticated owner
- authenticated non-owner
- admin user
- invalid admin secret

Sensitive areas:

- `/api/admin/*`
- model configuration
- billing rules
- file and media access
- export endpoints
- generation task endpoints

### PostgreSQL Integration

Add tests for:

- fresh migration to head
- repeated migration idempotency
- preflight check
- schema migration records
- key indexes and constraints
- delete cascade behavior
- transaction rollback behavior

### Worker and Queue Behavior

Add tests for:

- atomic task claim
- lease timeout
- heartbeat update
- retry count
- cancellation
- idempotency key
- duplicate worker race
- crash and recovery path

### Frontend Browser Regression

Add Playwright tests for:

- login page loads
- login failure and success
- main app shell loads
- script and episode list navigation
- subject card flow smoke test
- simple storyboard smoke test
- video task status polling smoke test
- billing page smoke test
- dashboard page smoke test

### Secret and Public Repo Scan

Add checks for:

- real tokens
- private relay URLs
- database credentials
- Redis credentials
- admin password strings
- `.env` tracked by mistake
- backup files exposed under static routes

## Verification Commands

These commands may evolve as tooling is added.

### Environment

```powershell
.\venv\Scripts\python.exe -m pip check
node --version
git status --short
```

### Current Python Tests

```powershell
$env:PYTHONUTF8='1'
$env:DATABASE_URL='sqlite:///' + ((Join-Path $env:TEMP 'story_creator_unittest.sqlite') -replace '\\','/')
.\venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py"
```

### Current JavaScript Tests

```powershell
Get-ChildItem .\tests -Filter "*.js" | Sort-Object Name | ForEach-Object {
  node $_.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

### Startup Check

```powershell
$env:DATABASE_URL='postgresql://story_creator_user@127.0.0.1:5432/story_creator_20260310'
$env:PYTHONUTF8='1'
$env:APP_ROLE='preflight'
$env:ENABLE_BACKGROUND_POLLER='0'
.\venv\Scripts\python.exe .\backend\preflight.py check
```

### Future Secret Scan

```powershell
$secretPattern = 'api[_-]?key|token|password|secret|postgresql://[^''"\s]+:[^@''"\s]+@|Bearer\s+[A-Za-z0-9._-]{20,}'
git grep -n -I -E $secretPattern -- backend frontend start_*.ps1 start_*.cmd . ':!*.md'
git status --ignored --short
```

The goal is for real secrets and plaintext credentials to disappear from tracked files. Placeholder references in `.env.example` and docs are allowed only when clearly marked as placeholders.

## Phase Gates

### Phase 0 Gate

- Existing Python tests pass or failures are documented as baseline.
- Existing JS tests pass or failures are documented as baseline.
- Route duplicate test exists.
- Browser smoke test exists.

### Phase 1 Gate

- Secret scan finds no real private values in tracked files.
- `.env` is ignored.
- `.env.example` exists with placeholders.
- Admin endpoints are protected by tests.
- Path traversal test passes.

### Phase 2 Gate

- App import side-effect test passes.
- Route snapshot still matches approved contract.
- API contract tests pass.
- `backend/main.py` only assembles app and compatibility exports.

### Phase 3 Gate

- Fresh PostgreSQL migration passes.
- Upgrade migration passes.
- Runtime DDL is removed from app startup.
- Seed operations are idempotent.

### Phase 4 Gate

- Duplicate worker claim test passes.
- Worker crash recovery test passes.
- Redis outage behavior is documented and tested where possible.
- Billing idempotency tests pass.

### Phase 5 Gate

- Playwright critical flows pass.
- Vue app has no unexpected console errors.
- Legacy and Vue behavior are equivalent for migrated features.
- User acceptance confirms `/app` cutover.

## Reporting Format

Each implementation batch should report:

- files changed
- tests run
- failures or skipped checks
- behavior changed
- security impact
- migration impact
- changelog entry
