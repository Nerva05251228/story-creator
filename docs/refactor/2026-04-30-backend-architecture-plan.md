# Backend Architecture Refactor Plan

Date: 2026-04-30

## Goal

Split the backend into clear FastAPI, domain, infrastructure, database, and worker boundaries while preserving current behavior during migration.

## Current Problems

- `backend/main.py` owns too many responsibilities.
- Services sometimes import `main`, creating reverse dependencies.
- Runtime migration and app import behavior are intertwined.
- Workers and web process lifecycle are coupled.
- PostgreSQL is both source of truth and informal queue without a durable task contract.
- Secrets and private endpoints are hardcoded in scripts and config modules.

## Target Boundaries

### App Layer

Files:

- `backend/app/main.py`
- `backend/app/settings.py`
- `backend/app/security.py`
- `backend/app/logging.py`

Responsibilities:

- Create FastAPI app.
- Load typed settings from environment.
- Register middleware and routers.
- Configure logging and CORS.
- Avoid database DDL, external calls, or worker startup during import.

### API Layer

Files:

- `backend/api/routers/auth.py`
- `backend/api/routers/admin.py`
- `backend/api/routers/scripts.py`
- `backend/api/routers/episodes.py`
- `backend/api/routers/storyboard.py`
- `backend/api/routers/storyboard2.py`
- `backend/api/routers/media.py`
- `backend/api/routers/billing.py`
- `backend/api/routers/dashboard.py`
- `backend/api/routers/model_configs.py`
- `backend/api/routers/hit_dramas.py`

Responsibilities:

- Validate request and response shapes.
- Call domain services.
- Map service errors to HTTP errors.
- Avoid direct external vendor calls.

### Schema Layer

Files:

- `backend/schemas/*.py`

Responsibilities:

- Pydantic request and response models.
- Domain-specific response contracts.
- Shared pagination and error models.

### Domain Service Layer

Files:

- `backend/domain/services/*.py`

Responsibilities:

- Business rules.
- Transaction orchestration.
- Idempotency decisions.
- No FastAPI request objects.

### Infrastructure Layer

Files:

- `backend/infra/db/*.py`
- `backend/infra/clients/*.py`
- `backend/infra/redis/*.py`

Responsibilities:

- SQLAlchemy session and repository helpers.
- External clients for image, LLM, video, CDN, and TTS services.
- Redis lock, queue, and cache adapters.

### Worker Layer

Files:

- `backend/workers/*.py`

Responsibilities:

- Poll or consume task IDs.
- Claim tasks atomically.
- Heartbeat long work.
- Retry and fail tasks with explicit state transitions.
- Never depend on web worker process lifetime.

## Configuration Direction

All backend settings come from `.env` or process environment.

Required categories:

- `APP_ENV`
- `HOST`
- `PORT`
- `WEB_CONCURRENCY`
- `DATABASE_URL`
- `REDIS_URL`
- `CORS_ALLOWED_ORIGINS`
- `JWT_SECRET_KEY`
- `ADMIN_SECRET`
- `IMAGE_PLATFORM_BASE_URL`
- `IMAGE_PLATFORM_API_TOKEN`
- `IMAGE_SERVICE_API_KEY`
- `TEXT_RELAY_BASE_URL`
- `TEXT_RELAY_API_KEY`
- `LLM_RELAY_BASE_URL`
- `LLM_RELAY_API_KEY`
- `VIDEO_API_BASE_URL`
- `VIDEO_API_TOKEN`
- `SORA_VIDEO_API_BASE_URL`
- `SORA_VIDEO_API_TOKEN`
- `CDN_BASE_URL`
- `CDN_UPLOAD_URL`
- `CDN_API_TOKEN`
- `VOICEOVER_TTS_API_URL`
- `VOICEOVER_TTS_API_TOKEN`

Rules:

- `.env` is local only and ignored.
- `.env.example` contains placeholders only.
- Local defaults may exist only for non-secret values.
- Non-local environments fail fast when required secrets are absent.
- Startup logs must redact credentials.

## Migration Plan

### Step 1: Contract Safety

- Add duplicate route detection.
- Add auth and admin permission contract tests.
- Add file path traversal tests.
- Add app import side-effect test.

### Step 2: Settings and Secret Removal

- Introduce typed settings module.
- Move startup script values into `.env`.
- Remove hardcoded tokens and private addresses from backend modules.
- Add secret scan command to validation docs.

### Step 3: App Factory

- Introduce `create_app()`.
- Keep old `main:app` compatibility while new factory is introduced.
- Ensure importing the app does not run DDL, external prewarm, or pollers.

### Step 4: Router Extraction

Order:

1. Static and health routes.
2. Auth and admin.
3. Billing and dashboard.
4. Scripts and episodes.
5. Media and file routes.
6. Storyboard and video routes.
7. Model config and hit drama routes.

Each extraction must preserve route path, method, status code, and response fields unless an approved behavior change says otherwise.

### Step 5: Service and Repository Extraction

- Move route-internal business logic to services.
- Move repeated query logic to repositories or query helpers.
- Keep transaction ownership explicit.
- Avoid circular imports from services to routers or app modules.

### Step 6: Migrations

- Add Alembic or an equivalent versioned runner.
- Generate baseline from current PostgreSQL schema.
- Convert runtime DDL into migration revisions.
- Keep startup seed idempotent and separate.

### Step 7: Worker and Redis

- Define task state model and claim semantics.
- Keep PostgreSQL durable state.
- Use Redis for short-lived queue/lock/cache only.
- Use idempotency keys for external submissions and billing.
- Add worker crash and duplicate-claim tests.

## Backend Acceptance Criteria

- `backend/main.py` no longer contains business route implementations.
- App import has no DDL, network prewarm, or worker startup side effects.
- No hardcoded private token or service URL remains in tracked backend or scripts.
- API route duplicate test passes.
- Admin and sensitive APIs have permission tests.
- Fresh PostgreSQL migration and upgrade checks pass.
- Two workers cannot process the same task concurrently.
