# Refactor Risk Register

Date: 2026-04-30

This register consolidates the first parallel read-only review. It is intentionally focused on risks that affect refactor order.

## P0 Risks

### Hardcoded Secrets and Private Endpoints

Evidence:

- Startup scripts hardcode `DATABASE_URL` and image API token.
- Backend config files contain default service tokens and service URLs.
- README documents a real local database password pattern.

Impact:

- Public repository publication would leak credentials and private service topology.
- Rotating one token is not enough because several service categories are affected.

Required direction:

- Move all private values to `.env`.
- Commit only `.env.example` with placeholders.
- Rotate existing exposed secrets before public release.

### Admin and Password Exposure

Evidence:

- Admin password is present in frontend pages and backend constants.
- User model stores plaintext password.
- Admin user listing can expose `password_plain`.

Impact:

- Any user with source or browser access can discover admin credentials.
- User credentials can be leaked through API responses.

Required direction:

- Replace client-side admin password checks with server-side authorization.
- Remove plaintext password storage and response fields.
- Add permission tests before admin page migration.

### Path Traversal in File Serving

Evidence:

- File-serving route accepts `{filename:path}` and joins it with a base directory.
- No canonical path boundary check is currently enforced.

Impact:

- Requests may read files outside intended upload/media directories.

Required direction:

- Canonicalize requested path.
- Reject any path outside approved roots.
- Add traversal tests.

## P1 Risks

### Backend Monolith

Evidence:

- `backend/main.py` is over 21,000 lines.
- It contains routes, schemas, migrations, startup behavior, background task helpers, and business orchestration.

Impact:

- Refactoring one behavior risks changing unrelated behavior.
- Import side effects make tests and worker separation fragile.

Required direction:

- Introduce `create_app()`.
- Move routes to `APIRouter` modules.
- Move schemas and services out by domain.

### Frontend Monolith

Evidence:

- `frontend/js/app.js` is over 16,000 lines.
- It contains global state, API calls, rendering, dialogs, polling, and workflow logic.

Impact:

- Vue migration cannot safely be a direct rewrite.
- Lifecycle bugs and duplicated polling are likely if moved wholesale.

Required direction:

- Migrate by feature slice behind `/app-v2`.
- Build shared API client and route lifecycle first.
- Keep legacy `/app` until acceptance.

### Runtime DDL and Migration Drift

Evidence:

- `preflight.py` runs `create_all`.
- `main.py` contains runtime `ALTER TABLE` and `ensure_*` routines.
- Historical migration scripts are not under a single versioned runner.

Impact:

- Fresh and existing databases can drift.
- Startup can lock or mutate production schema unexpectedly.

Required direction:

- Introduce Alembic or equivalent versioned migration system.
- Convert runtime DDL into migration files.
- Keep seed operations separate and idempotent.

### Queue and Worker Race Conditions

Evidence:

- PostgreSQL status rows act as queue state.
- Workers use process-local locks, semaphores, threads, and polling.
- Multiple workers may claim or update the same task.

Impact:

- Duplicate external submissions, duplicate billing, and corrupted task state.

Required direction:

- Define a durable task contract.
- Use atomic claim and lease semantics.
- Introduce Redis only behind clear worker abstractions.

### Missing API and Permission Contract Tests

Evidence:

- Existing tests focus heavily on services and helper behavior.
- There is limited FastAPI HTTP contract coverage.
- Duplicate routes already exist.

Impact:

- Router splits can silently change behavior.
- Permission regressions are likely.

Required direction:

- Add route registry, OpenAPI, auth, and permission tests before large moves.

## P2 Risks

### Exposed Backup and Legacy Files

Evidence:

- `/static` exposes the whole `frontend/` tree.
- `frontend/js/app.js.backup` is tracked.

Impact:

- Old code remains public and increases audit surface.

Required direction:

- Move legacy assets under explicit legacy routing or remove them after cutover.

### Dependency and Environment Reproducibility

Evidence:

- There is only `requirements.txt`.
- Missing runtime imports were discovered during local setup.
- No separate dev/test dependency list exists.

Impact:

- New machines can fail after following README.

Required direction:

- Add documented dependency strategy.
- Add dev/test requirements.
- Keep startup commands consistent with `.env`.

### Browser Regression Coverage

Evidence:

- Frontend tests largely read files and execute extracted functions.
- No real browser regression suite is configured.

Impact:

- DOM, CSS, route, polling, and upload regressions can pass tests.

Required direction:

- Add Playwright smoke tests before frontend migration.

## Public Repository Readiness Checklist

- No real tokens, passwords, relay URLs, CDN URLs, database credentials, or Redis credentials in tracked files.
- `.env` and local variants are ignored.
- `.env.example` contains only placeholders.
- Startup scripts do not print credentials.
- Admin credentials are not present in frontend bundles.
- Backup files are not served publicly.
- README does not contain private setup values.
- Security-sensitive endpoints have tests.
