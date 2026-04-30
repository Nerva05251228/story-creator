# Changelog

All notable changes to this project will be documented in this file.

This project uses a lightweight `Added`, `Changed`, `Fixed`, `Security`, and `Migration` structure.

## 2026-04-30 - Phase 1A Config, Startup, and Runtime Fixes

### Added

- Added refactor planning documents under `docs/refactor/`.
- Added a master plan for backend modularization, Vue/shadcn-vue migration, Redis worker boundaries, and public repository readiness.
- Added validation planning for API contracts, permissions, PostgreSQL integration, browser regression, worker concurrency, and secret scanning.
- Added `.env.example` and shared startup environment loading for the Phase 1A configuration migration.
- Added backend video provider stats and quota proxy endpoints so the browser no longer calls private upstream video APIs directly.
- Added environment validation tests for local `.env` loading, placeholder rejection, admin password handling, and frontend runtime configuration.
- Added a route registry baseline test that records the current duplicate route set and fails on new unexpected duplicates.

### Security

- Documented the requirement that private keys, API tokens, service endpoints, database credentials, Redis credentials, CDN settings, LLM relay settings, image service settings, video service settings, and TTS settings must move to local `.env` files.
- Documented that the repository should only contain safe placeholders in `.env.example` before public release.
- Rebuilt git history from the sanitized current tree so old committed secrets are no longer reachable from `main`.
- Removed real default service tokens and private service URLs from the first backend configuration path and Windows startup scripts.
- Moved admin panel, master password, and Nerva password defaults out of source and into local environment configuration.
- Rejected placeholder relay and video API configuration values before network requests are sent.
- Restricted `/files/{filename}` serving to resolved files inside approved upload/video roots and added traversal regression tests.

### Fixed

- Kept CMD startup windows open on startup failure so double-click users can read missing `.env`, `DATABASE_URL`, preflight, or virtual environment errors before the terminal closes.
- Fixed dashboard/admin password retry behavior so a wrong or stale local password is cleared and the password dialog is shown again after backend rejection.
- Fixed frontend CORS errors by routing provider stats and quota requests through the local backend.
- Fixed relay task submission failures caused by `relay.example.invalid` placeholder configuration.
- Reduced local UI stalls by deferring startup video metadata polling, making new-script selector loading asynchronous, rendering shot selection from local state before background refresh, and moving Sora video URL refresh to the background.
- Repaired malformed admin page markup that could corrupt toolbar, modal, and table controls.

### Removed

- Removed obsolete backup and ad hoc local test artifacts: `backend/ai_service.py.backup`, `frontend/js/app.js.backup`, `test.py`, and `test2.py`.
- Removed the superseded `docs/simple-storyboard-params-guide.md`; follow-up architecture and migration notes now live under `docs/refactor/`.

### Migration

- Documented the intended migration path from runtime DDL and legacy startup migration helpers toward versioned database migrations.
- Documented the intended frontend migration path from static HTML/JS/CSS to Vue 3, Vite, TypeScript, and shadcn-vue.
