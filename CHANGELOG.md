# Changelog

All notable changes to this project will be documented in this file.

This project uses a lightweight `Added`, `Changed`, `Fixed`, `Security`, and `Migration` structure.

## 2026-05-02 - Script Episode Route Extraction

### Added

- Added focused episode router coverage for `POST/GET /api/scripts/{script_id}/episodes`, owner-only access, automatic story library creation, response defaults, and route ownership.

### Changed

- Moved script-scoped episode create/list routes from `backend/main.py` into `backend/api/routers/episodes.py` while preserving paths, response models, ownership checks, and main-module compatibility exports.
- Replaced duplicate `EpisodeCreate` and `EpisodeResponse` definitions in `backend/main.py` with aliases to the extracted episode schemas.

## 2026-05-02 - Card Image Generation Route Extraction

### Added

- Added focused card image generation route coverage for owner submission, non-owner rejection, missing prompt rejection, processing row creation, card generation counters, three-view helper compatibility, and route ownership.

### Changed

- Moved `POST /api/cards/{card_id}/generate-image` from `backend/main.py` into `backend/api/routers/card_media.py`.
- Extracted card image generation request schema and helper logic into `backend/api/schemas/card_media.py` and `backend/api/services/card_image_generation.py`, while keeping `backend/main.py` compatibility exports for direct callers.

## 2026-05-02 - Subject Card Prompt Route Extraction

### Added

- Added focused subject card prompt route coverage for owner updates, non-owner rejection, persistence, and route ownership.

### Changed

- Moved `PUT /api/cards/{card_id}/prompt` from `backend/main.py` into `backend/api/routers/subject_cards.py` while preserving the existing `{ "prompt": "..." }` request shape and response payload.
- Kept the `backend/main.py` compatibility export for direct callers and extended route registry coverage.

## 2026-05-02 - Script Router Extraction

### Added

- Added focused script route tests covering authenticated CRUD behavior, owner 403/404 handling, script delete cleanup, and copy-script deep-copy behavior for episodes, libraries, cards, media, and storyboard shot ID remapping.

### Changed

- Moved script CRUD and copy routes from `backend/main.py` into `backend/api/routers/scripts.py` with request/response schemas in `backend/api/schemas/scripts.py`.
- Extracted shared episode dependency cleanup into `backend/api/services/episode_cleanup.py` and reused it from admin user deletion and script deletion.
- Registered the new script router in `backend/main.py`, retained compatibility exports for direct callers, and extended route ownership coverage in `tests/test_route_registry.py`.

## 2026-05-02 - Admin User and Billing Router Extraction

### Added

- Added focused admin user route tests covering admin password enforcement, hidden-account filtering, default-password creation, reset/impersonation behavior, and delete handling.
- Added focused billing route tests covering bearer plus admin-header auth, summary/detail wrapper response shapes, reimbursement export normalization, and billing rule create/update rollback behavior.

### Changed

- Moved `/api/admin/users*` routes into `backend/api/routers/admin_users.py` with request schema in `backend/api/schemas/admin_users.py`.
- Moved `/api/billing*` routes into `backend/api/routers/billing.py` with billing rule request schema in `backend/api/schemas/billing.py`.
- Registered the new routers in `backend/main.py`, removed the migrated inline route bodies from `backend/main.py`, and extended route ownership coverage in `tests/test_route_registry.py`.

## 2026-05-02 - Template and Settings Router Extraction

### Added

- Added focused route tests for template APIs and global settings APIs covering route registration, style template CRUD behavior, video rule updates, prompt template updates, prompt config ordering, and shot duration validation.

### Changed

- Moved prompt templates, image style templates, video style templates, large shot templates, and storyboard image templates into `backend/api/routers/templates.py`.
- Moved video generation rules, Sora rules, global prompt settings, prompt configs, and shot duration template routes into `backend/api/routers/settings.py`.
- Extracted template and settings request/response schemas into `backend/api/schemas/templates.py` and `backend/api/schemas/settings.py`.
- Extracted style-template prompt cleanup helpers into `backend/api/services/style_templates.py` while preserving existing compatibility helpers in `backend/main.py`.
- Registered the new routers in `backend/main.py` and extended route ownership coverage in `tests/test_route_registry.py`.

## 2026-05-02 - Episodes, Shots, and Hit Drama Router Extraction

### Added

- Added focused route tests for episodes, shots, and hit drama APIs covering ownership, auth-sensitive behavior, and payload defaults.

### Changed

- Moved the remaining episode/storyboard/voiceover/managed-generation/storyboard2 routes into `backend/api/routers/episodes.py`.
- Moved the remaining shot CRUD, media, prompt, video, and export routes into `backend/api/routers/shots.py`.
- Moved the hit drama CRUD, history, upload, and Excel import routes into `backend/api/routers/hit_dramas.py`.
- Registered the new routers in `backend/main.py` and extended route ownership coverage in `tests/test_route_registry.py`.
- Removed the old hit drama schema/helper/route bodies from `backend/main.py` while keeping compatibility exports for direct callers.

## 2026-05-01 - Model Config Router Extraction

### Added

- Added focused model config route tests covering admin password rejection, cache listing and sync, update behavior, and missing function-key handling.

### Changed

- Moved the `/api/admin/model-configs`, `/api/admin/model-configs/sync-models`, and `/api/admin/model-config/{function_key}` routes into `backend/api/routers/model_configs.py` while preserving paths, methods, response shapes, and admin header behavior.
- Extracted model config defaults and persistence helpers into `backend/api/services/model_configs.py` and the update request schema into `backend/api/schemas/model_configs.py`.
- Kept `backend/main.py` compatibility wrappers for older direct-call tests while registering the new model config router in the FastAPI app.

## 2026-05-01 - Dashboard Task Router Extraction

### Added

- Added focused dashboard task route tests covering admin password rejection, list filtering and pagination, detail payload parsing, query-status mapping, single delete, and bulk delete behavior.

### Changed

- Moved the dashboard task list, detail, query-status, delete, and bulk-delete routes into `backend/api/routers/dashboard.py` while preserving paths, methods, response shapes, and admin header behavior.
- Extracted dashboard task list and serialization helpers into `backend/api/routers/dashboard.py`, moved `DashboardBulkDeleteRequest` into `backend/api/schemas/dashboard.py`, and shared the admin password helper through `backend/api/services/admin_auth.py`.
- Registered the new dashboard router in `backend/main.py` while keeping the existing `main._verify_admin_panel_password` compatibility path for older direct-call tests.

## 2026-05-01 - Auth Router Extraction

### Added

- Added focused auth route tests covering login, token verification, password change, and Nerva password validation behavior.

### Changed

- Moved the `/api/auth/login`, `/api/auth/verify`, `/api/auth/change-password`, and `/api/auth/verify-nerva-password` routes into `backend/api/routers/auth.py` while preserving the existing response shapes and authentication behavior.
- Extracted auth password helpers into `backend/api/services/auth.py` and auth request models into `backend/api/schemas/auth.py`.
- Kept `backend/main.py` compatibility callables for existing direct-call tests while registering the new auth router in the FastAPI app.

## 2026-05-01 - Card Media Router Extraction

### Added

- Added focused card media route tests covering image upload/delete, audio upload/list/delete, generated image listing, reference selection, and generated image deletion behavior.

### Changed

- Moved the card image, audio, generated image, and reference image routes into `backend/api/routers/card_media.py` while preserving existing paths, methods, auth behavior, and response shapes.
- Extracted card media upload and audio duration helpers into `backend/api/services/card_media.py` and updated `backend/main.py` to register the new router.

## 2026-05-01 - Subject Card Router Extraction

### Added

- Added focused subject card route tests covering CRUD, list filtering, ownership checks, deletion cleanup, and prompt task submission behavior.

### Changed

- Moved the seven subject card CRUD and AI prompt routes into `backend/api/routers/subject_cards.py` while preserving existing paths, methods, auth behavior, and response shapes.
- Extracted subject card request/response schemas into `backend/api/schemas/subject_cards.py` for reuse by the moved routes and remaining card media endpoints.

## 2026-04-30 - Phase 1A Config, Startup, and Runtime Fixes

### Added

- Added refactor planning documents under `docs/refactor/`.
- Added the first backend extraction slice by moving fixed HTML page routes into a dedicated FastAPI router.
- Added a master plan for backend modularization, Vue/shadcn-vue migration, Redis worker boundaries, and public repository readiness.
- Added validation planning for API contracts, permissions, PostgreSQL integration, browser regression, worker concurrency, and secret scanning.
- Added `.env.example` and shared startup environment loading for the Phase 1A configuration migration.
- Added backend video provider stats and quota proxy endpoints so the browser no longer calls private upstream video APIs directly.
- Added environment validation tests for local `.env` loading, placeholder rejection, admin password handling, and frontend runtime configuration.
- Added a route registry test that fails on duplicate FastAPI method/path registrations.
- Added startup/preflight boundary contract tests before the app factory and router refactor.
- Added a dedicated media router for `/files/{filename:path}` while preserving traversal protections.
- Added a dedicated public API router for `GET /api/public/users` with backend route ownership coverage.
- Added a dedicated image generation router for `GET /api/image-generation/models` with route ownership coverage.
- Added a dedicated video router for `GET /api/video/providers/{provider}/accounts` with route ownership and behavior coverage.
- Added route ownership and behavior coverage for `GET /api/video-model-pricing`.
- Added story library CRUD route behavior and ownership coverage.

### Changed

- Moved the backend video provider stats and quota proxy routes into the dedicated video router.
- Moved `GET /api/video-model-pricing` into the dedicated video router while preserving its pricing response shape.
- Extracted story library response/create schemas into `backend/api/schemas/story_library.py` and moved `GET /api/public/users/{user_id}/libraries` into the public API router.
- Moved the five story library CRUD routes into a dedicated library router while preserving their existing paths.

### Security

- Documented the requirement that private keys, API tokens, service endpoints, database credentials, Redis credentials, CDN settings, LLM relay settings, image service settings, video service settings, and TTS settings must move to local `.env` files.
- Documented that the repository should only contain safe placeholders in `.env.example` before public release.
- Rebuilt git history from the sanitized current tree so old committed secrets are no longer reachable from `main`.
- Removed real default service tokens and private service URLs from the first backend configuration path and Windows startup scripts.
- Moved admin panel, master password, and Nerva password defaults out of source and into local environment configuration.
- Rejected placeholder relay and video API configuration values before network requests are sent.
- Restricted `/files/{filename}` serving to resolved files inside approved upload/video roots and added traversal regression tests.
- Required the admin password header on all `/api/admin/*` route contracts and moved ordinary copy-script user selection to the public users endpoint.

### Fixed

- Kept CMD startup windows open on startup failure so double-click users can read missing `.env`, `DATABASE_URL`, preflight, or virtual environment errors before the terminal closes.
- Fixed dashboard/admin password retry behavior so a wrong or stale local password is cleared and the password dialog is shown again after backend rejection.
- Fixed frontend CORS errors by routing provider stats and quota requests through the local backend.
- Fixed relay task submission failures caused by `relay.example.invalid` placeholder configuration.
- Reduced local UI stalls by deferring startup video metadata polling, making new-script selector loading asynchronous, rendering shot selection from local state before background refresh, and moving Sora video URL refresh to the background.
- Repaired malformed admin page markup that could corrupt toolbar, modal, and table controls.
- Removed unreachable duplicate script copy and episode route handlers so FastAPI route registration is unambiguous.
- Sent the saved admin password header from the model selection page when reading, syncing, or saving model configuration.
- Fixed story library updates failing with an undefined `card` reference.

### Removed

- Removed obsolete backup and ad hoc local test artifacts: `backend/ai_service.py.backup`, `frontend/js/app.js.backup`, `test.py`, and `test2.py`.
- Removed stale test wrappers that imported the deleted ad hoc `test.py` and `test2.py` scripts.
- Removed the superseded `docs/simple-storyboard-params-guide.md`; follow-up architecture and migration notes now live under `docs/refactor/`.

### Migration

- Documented the intended migration path from runtime DDL and legacy startup migration helpers toward versioned database migrations.
- Documented the intended frontend migration path from static HTML/JS/CSS to Vue 3, Vite, TypeScript, and shadcn-vue.
