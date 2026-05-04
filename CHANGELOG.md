# Changelog

All notable changes to this project will be documented in this file.

This project uses a lightweight `Added`, `Changed`, `Fixed`, `Security`, and `Migration` structure.

## 2026-05-04 - Episode Cleanup Service Expansion

### Added

- Added focused service and import-contract coverage for storyboard shot ID normalization, dependency cleanup, shot deletion by IDs, and episode-wide storyboard shot deletion.

### Changed

- Moved duplicated storyboard shot cleanup helpers out of `backend/main.py` and `backend/api/routers/episodes.py` into `backend/api/services/episode_cleanup.py` while keeping compatibility aliases for existing direct callers.
- Reused the existing episode cleanup service for the legacy `_clear_episode_dependencies` export in `backend/main.py`.

## 2026-05-04 - Storyboard2 Reference Image Service Extraction

### Added

- Added `backend/api/services/storyboard2_reference_images.py` for storyboard2 subject-card ID parsing, selected-card fallback, scene-card filtering, and reference image collection.
- Added focused service and import-contract coverage for storyboard2 reference image ordering, deduplication, scene inclusion, sub-shot overrides, and source-shot fallback.

### Changed

- Moved duplicated storyboard2 reference-image collection logic out of `backend/main.py` and `backend/api/routers/storyboard2.py` while keeping compatibility aliases and route patch points unchanged.

## 2026-05-04 - Storyboard Sound Card Service Extraction

### Added

- Added `backend/api/services/storyboard_sound_cards.py` for storyboard selected sound-card parsing, episode-library validation, and default sound-card resolution.
- Added focused service and import-contract coverage for explicit sound-card IDs, invalid library scope, linked role sound cards, same-name fallback, and narrator inclusion.

### Changed

- Moved duplicated storyboard sound-card helper logic out of `backend/main.py` and `backend/api/services/storyboard_video_payload.py` while keeping compatibility aliases for existing direct callers.

## 2026-05-04 - Storyboard Prompt Context Service Extraction

### Added

- Added `backend/api/services/storyboard_prompt_context.py` for shared storyboard subject text, Sora reference prompt, large-shot template, and debug subject-name resolution helpers.
- Added focused service and import-contract coverage for storyboard prompt context helper behavior.

### Changed

- Moved duplicated prompt context helpers out of `backend/main.py`, `backend/api/routers/episodes.py`, and `backend/api/routers/storyboard2.py` while keeping compatibility aliases for existing direct callers.

## 2026-05-04 - Storyboard Video Generation Limit Service Extraction

### Added

- Added `backend/api/services/storyboard_video_generation_limits.py` for shared storyboard shot-family video generation concurrency guards.
- Added focused service and import-contract coverage for active video/managed-task limit behavior.

### Changed

- Moved duplicated shot-family active generation limit helpers out of `backend/main.py` and `backend/api/routers/episodes.py` while keeping compatibility aliases for existing callers.
- Replaced duplicate shot and video workflow request/response schema definitions in `backend/main.py` with aliases to `backend/api/schemas/shots.py` and `backend/api/schemas/episodes.py`.

## 2026-05-04 - Shot Reference Workflow Service Extraction

### Added

- Added `backend/api/services/shot_reference_workflow.py` for storyboard image generation, first-frame reference selection/upload, and shot scene image upload/selection logic.
- Added focused route and startup import-contract coverage for the shot reference workflow.

### Changed

- Moved shot reference workflow implementations out of `backend/main.py` while keeping compatibility aliases for existing direct callers.
- Routed `/api/shots/{shot_id}/generate-storyboard-image`, `/api/shots/{shot_id}/first-frame-reference-image`, and `/api/shots/{shot_id}/scene-image` through the shared service without changing response payloads or authorization behavior.
- Moved first-frame and scene-image selection request schemas into `backend/api/schemas/shots.py`.

### Fixed

- Registered the frontend-used `PATCH /api/shots/{shot_id}/first-frame-reference` and `PATCH /api/shots/{shot_id}/scene-image-selection` endpoints in the shots router.

## 2026-05-04 - Managed Generation Router Extraction

### Added

- Added `backend/api/routers/managed_generation.py` and `backend/api/services/managed_generation.py` for managed-session control and inspection routes.
- Added focused route ownership and startup import-contract coverage for managed generation.

### Changed

- Moved `/api/episodes/{episode_id}/stop-managed-generation`, `/api/managed-sessions/{session_id}/tasks`, and `/api/episodes/{episode_id}/managed-session-status` out of `backend/api/routers/episodes.py` without changing API paths, response shapes, or authorization behavior.
- Registered the managed generation router in `backend/main.py` and kept legacy direct-call compatibility aliases in the episodes router.

## 2026-05-04 - Shot Detail Image Service Extraction

### Added

- Added `backend/api/services/shot_image_generation.py` for detail-image generation, lookup, debug capture, and cover selection logic.
- Added focused route ownership and startup import-contract coverage for the shot detail-image service extraction.

### Changed

- Moved detail-image helper logic out of `backend/main.py` and `backend/api/routers/shots.py` into the shared shot detail-image service without changing API paths, response shapes, or authorization behavior.
- Kept the existing detail-image entry points available through the shots router and legacy compatibility aliases.

## 2026-05-04 - Storyboard Excel Router Extraction

### Added

- Added `backend/api/routers/storyboard_excel.py` as the owner for storyboard import and export routes.
- Added focused route ownership and startup import-contract coverage for the storyboard Excel router.

### Changed

- Moved `/api/episodes/{episode_id}/import-storyboard` and `/api/episodes/{episode_id}/export-storyboard` out of `backend/api/routers/episodes.py` without changing API paths, response shapes, or authorization behavior.
- Registered the storyboard Excel router in `backend/main.py`.

## 2026-05-04 - Storyboard2 Router Extraction

### Added

- Added `backend/api/routers/storyboard2.py` as the owner for storyboard2 board, prompt batch, edit, image generation, and video generation routes.
- Added focused storyboard2 route ownership and startup import-contract coverage.

### Changed

- Moved storyboard2 route handlers and their dependent helper block out of `backend/api/routers/episodes.py` without changing API paths, response shapes, or authorization behavior.
- Registered the storyboard2 router in `backend/main.py` and kept legacy direct-call compatibility aliases in both `backend/main.py` and the episodes router.

## 2026-05-04 - Simple Storyboard Router Extraction

### Added

- Added `backend/api/routers/simple_storyboard.py` as the owner for simple storyboard generation, fetch, status, retry, and update routes.
- Added focused simple storyboard router, route registry, OpenAPI preservation, and startup import-contract tests.

### Changed

- Moved `/api/episodes/{episode_id}/generate-simple-storyboard` and `/api/episodes/{episode_id}/simple-storyboard*` route handlers out of the episodes router without changing API paths, response fields, or authorization behavior.
- Registered the simple storyboard router in `backend/main.py` and kept legacy direct-call compatibility aliases.

## 2026-05-04 - Storyboard Sync Service Extraction

### Added

- Added `backend/api/services/storyboard_sync.py` for storyboard subject normalization, subject-card synchronization, and storyboard-to-shot synchronization.
- Added focused service tests for subject card creation/update, selected-card synchronization, and variant creation for modified shots with active video.

### Changed

- Updated `backend/api/routers/episodes.py` and `backend/main.py` to keep compatibility aliases for storyboard sync helpers while delegating their implementations to the shared service.

## 2026-05-04 - Voiceover Router Extraction

### Added

- Added `backend/api/routers/voiceover.py` as the owner for `/api/episodes/{episode_id}/voiceover/*` routes.
- Added focused voiceover router, route registry, OpenAPI preservation, and startup import-contract tests.

### Changed

- Moved voiceover update, shared preset/reference, line TTS enqueue, generate-all enqueue, and TTS status route handlers out of `backend/api/routers/episodes.py` without changing API paths, methods, response structures, or authorization behavior.
- Registered the voiceover router in `backend/main.py` and kept non-voiceover episode workflows in the episodes router.

## 2026-05-03 - Voiceover Shared Data Helper Service Extraction

### Added

- Added shared voiceover shared-data helpers to `backend/api/services/voiceover_data.py` for default shared payloads, caller-relative default reference paths, default reference construction, shared-data normalization, and script load/save normalization.
- Added focused service tests for caller-relative default MP3 path resolution, fresh shared-data defaults, default reference injection, missing-file behavior, and JSON normalization on load/save.
- Added import-contract coverage so the shared-data helper bodies stay out of `backend/main.py` and `backend/api/routers/episodes.py`.

### Changed

- Updated `backend/main.py` and `backend/api/routers/episodes.py` to keep compatibility aliases for voiceover shared-data helpers while preserving existing API paths, response shapes, authorization behavior, and caller-relative default reference resolution.

## 2026-05-03 - Voiceover TTS Helper Service Extraction

### Added

- Extended `backend/api/services/voiceover_data.py` with shared voiceover TTS method constants, vector normalization, setting-template normalization, line TTS defaults, line-state extraction, line lookup, and payload parsing helpers.
- Added focused service tests for vector clamping, neutral fallback behavior, setting template defaults, generated-audio normalization, missing line ID backfill, line-state extraction, line lookup, and first-reference resolution.
- Added import-contract coverage so voiceover TTS helper bodies stay out of `backend/main.py` and the episodes router.

### Changed

- Updated `backend/main.py` and `backend/api/routers/episodes.py` to reuse voiceover TTS helpers and constants through compatibility aliases without changing route paths, responses, authorization, task queue, or database behavior.

## 2026-05-03 - Voiceover Data Merge Service Extraction

### Added

- Added `backend/api/services/voiceover_data.py` for shared voiceover shot, narration, and dialogue merge helpers that preserve existing TTS extensions.
- Added focused service tests for voiceover line merge behavior, TTS field preservation, dialogue line matching, fallback line IDs, and invalid payload handling.
- Added import-contract coverage so voiceover merge helper bodies stay out of `backend/main.py` and the episodes router.

### Changed

- Updated `backend/main.py` and `backend/api/routers/episodes.py` to reuse voiceover merge helpers through compatibility aliases without changing route paths, responses, or authorization behavior.

## 2026-05-03 - Storyboard Reference Asset Service Extraction

### Added

- Added `backend/api/services/storyboard_reference_assets.py` for shared selected-card parsing, ordered card resolution, subject-card reference image lookup, and selected scene reference image resolution.
- Added focused service tests for selected card ID parsing, library-scoped card resolution, reference URL ordering/deduplication, uploaded-image fallback behavior, and uploaded scene image selection.
- Added import-contract coverage so storyboard reference asset helper bodies stay out of `backend/main.py`, the episodes router, and the storyboard video payload service.

### Changed

- Updated `backend/main.py`, `backend/api/routers/episodes.py`, and `backend/api/services/storyboard_video_payload.py` to reuse storyboard reference asset helpers through compatibility aliases instead of maintaining duplicate implementations.

## 2026-05-03 - Storyboard Video Effective Settings Service Extraction

### Added

- Added service-level tests for episode storyboard video defaults, shot model overrides, duration overrides, provider resolution, and prompt-template duration mapping.
- Added import-contract coverage so pure storyboard video effective-setting helpers stay in `backend/api/services/storyboard_video_settings.py`.

### Changed

- Moved pure storyboard video effective-setting helper logic out of `backend/main.py` and the episodes router while keeping compatibility aliases in both modules.
- Kept the shot-mutating apply helper local so ORM state changes remain explicit at the router/main boundary.

## 2026-05-03 - Storyboard Video Payload Service Extraction

### Added

- Added `backend/api/services/storyboard_video_payload.py` for shared Moti/Grok/Sora storyboard video task payload construction and reference asset assembly.
- Added focused payload service tests for Moti appointed accounts, Grok reference-image content, and Sora prompt/image URL payloads.
- Added import-contract coverage so the payload builder stays out of `backend/main.py`.

### Changed

- Updated `backend/main.py` to keep compatibility aliases while delegating storyboard video payload helpers to the service.

### Fixed

- Wired the episodes router to the shared storyboard video payload builder so batch video generation no longer references an undefined helper at runtime.

## 2026-05-03 - Storyboard Video Settings Service Extraction

### Added

- Added `backend/api/services/storyboard_video_settings.py` for shared storyboard video model configuration, provider resolution, and model/ratio/duration/resolution/account normalization.
- Added service-level and import-contract tests so video setting helper bodies stay out of `backend/main.py`, the episodes router, and the scripts router.

### Changed

- Updated `backend/main.py`, `backend/api/routers/episodes.py`, and `backend/api/routers/scripts.py` to share storyboard video setting helpers through compatibility aliases instead of maintaining duplicate implementations.

## 2026-05-03 - Storyboard Defaults Service Extraction

### Added

- Added `backend/api/services/storyboard_defaults.py` for shared storyboard default inheritance and detail image/storyboard2 setting normalization.
- Added import-contract coverage so `backend/main.py`, the episodes router, and the scripts router keep compatibility aliases without reintroducing duplicate helper bodies.

### Changed

- Updated `backend/main.py`, `backend/api/routers/episodes.py`, and `backend/api/routers/scripts.py` to share storyboard default helpers instead of maintaining separate copies.

## 2026-05-03 - Storyboard Subject Helper Deduplication

### Added

- Added an AST import-contract guard so `backend/main.py` cannot reintroduce duplicate storyboard subject reconciliation helper implementations already owned by the episodes router.

### Changed

- Replaced duplicate `backend/main.py` implementations for storyboard subject normalization, matching, inferred role resolution, and shot subject reconciliation with compatibility aliases to `backend/api/routers/episodes.py`.
- Kept the `backend/main.py` stage2 subject wrapper local because it is not duplicated in the episodes router and is still used by main's detailed storyboard pipeline.

## 2026-05-03 - Episode Metadata Compatibility Cleanup

### Added

- Added an AST import-contract guard so `backend/main.py` cannot reintroduce duplicate episode metadata route helper implementations already owned by the episodes router.

### Changed

- Replaced duplicate `backend/main.py` implementations for episode get/update, poll status, total cost, and storyboard2 duration helpers with compatibility aliases to `backend/api/routers/episodes.py`.

## 2026-05-03 - Billing Charge Helper Extraction

### Added

- Added focused tests for extracted billing charge helpers, preserving active video charge behavior and disabled image precharge behavior.
- Added import-contract coverage so billing charge helper bodies stay out of `backend/main.py` and the episode router.

### Changed

- Moved shared billing JSON serialization, storyboard video charge creation, storyboard2 video charge creation, and image precharge no-op helpers into `backend/api/services/billing_charges.py`.
- Updated `backend/main.py`, `backend/api/routers/episodes.py`, and card image generation to use the shared billing charge service while keeping existing helper names for direct callers.

### Fixed

- Restored the episode router's ordinary storyboard batch video charge helper wiring so submitted external video tasks do not hit a hidden `NameError` after task creation.

## 2026-05-03 - Storyboard2 Core Helper Deduplication

### Added

- Added an AST import-contract guard so `backend/main.py` cannot reintroduce duplicate storyboard2 helper and request model implementations already owned by the episodes router.

### Changed

- Removed duplicate storyboard2 permission, initialization, board serialization, image task recovery, video status sync, debug writer, and request model implementations from `backend/main.py`.
- Kept `backend/main.py` compatibility aliases pointing to `backend/api/routers/episodes.py` so direct callers keep the same symbol names while the live implementation remains in one place.

## 2026-05-03 - Simple Storyboard Batch Service Extraction

### Added

- Added focused coverage that imports the simple storyboard batch service without importing `backend/main.py`, while preserving `main.py` compatibility helper access.

### Changed

- Moved simple storyboard batch parsing, aggregation, summary, persistence, reset, update, and runtime item helpers into `backend/api/services/simple_storyboard_batches.py`.
- Updated `backend/main.py` and `backend/api/routers/episodes.py` to share the extracted batch service instead of carrying duplicate helper implementations.

## 2026-05-03 - Background Poller Lifecycle Extraction

### Added

- Added focused runtime poller lifecycle tests for disabled startup, forced startup, start/stop ordering, idempotency, recovery callbacks, and import isolation from `backend/main.py`.

### Changed

- Moved the background poller coordinator state and start/stop lifecycle logic from `backend/main.py` into `backend/runtime/pollers.py`.
- Kept thin `backend/main.py` compatibility wrappers so FastAPI startup/shutdown hooks and direct callers preserve the existing poller order and return behavior.

## 2026-05-03 - Storyboard2 Generation Route Extraction

### Added

- Added focused route coverage for storyboard2 image and video generation submit flows, including processing-task reuse and route ownership checks.

### Changed

- Moved `POST /api/storyboard2/subshots/{sub_shot_id}/generate-images` and `POST /api/storyboard2/subshots/{sub_shot_id}/generate-video` from `backend/main.py` into `backend/api/routers/episodes.py`.
- Moved the supporting storyboard2 generation request schemas, debug writers, billing helpers, polling helpers, and image-task state helpers into the episodes router while keeping `backend/main.py` compatibility exports for direct callers.
- Removed the duplicated storyboard2 generation/edit/delete route bodies from `backend/main.py`, shrinking the file and leaving it as app wiring plus compatibility aliases.

## 2026-05-02 - Storyboard2 Route Cleanup

### Added

- Added focused route registry coverage for the storyboard2 edit/delete endpoints so duplicate FastAPI registrations are caught early.

### Changed

- Removed the legacy `backend/main.py` FastAPI decorators for `PATCH /api/storyboard2/shots/{storyboard2_shot_id}`, `PATCH /api/storyboard2/subshots/{sub_shot_id}`, `DELETE /api/storyboard2/videos/{video_id}`, `PATCH /api/storyboard2/subshots/{sub_shot_id}/current-image`, and `DELETE /api/storyboard2/images/{image_id}`.
- Added `backend/main.py` compatibility exports that point storyboard2 request models and handlers at `backend/api/routers/episodes.py` so the codebase keeps one live implementation.

## 2026-05-02 - Managed Task List Route Extraction

### Added

- Added focused episode router coverage for managed task list route ownership, created-time ordering, status filtering, owner-only access, missing-session handling, `prompt_text`, and `original_shot_number`.

### Changed

- Moved `GET /api/managed-sessions/{session_id}/tasks` from `backend/main.py` into `backend/api/routers/episodes.py`.
- Preserved the existing bare-array response payload used by the legacy frontend, including `prompt_text` and `original_shot_number`, while keeping a `backend/main.py` compatibility export.

### Fixed

- Restored the dashboard task sync import used by the extracted `start-managed-generation` episode route.

## 2026-05-02 - Video Task Route Extraction

### Added

- Added focused video task route coverage for route ownership, raw upstream status proxying, upstream error wrapping, cancellation authorization, and main-module compatibility aliases.

### Changed

- Moved `GET /api/tasks/{task_id}/status` and `POST /api/video/tasks/cancel` from `backend/main.py` into `backend/api/routers/video.py`.
- Moved video task ID normalization, user-owned cancelable task lookup, and upstream cancel proxy helpers into the video router while keeping `backend/main.py` compatibility exports.

## 2026-05-02 - Episode Text Relay Route Extraction

### Added

- Added focused episode text relay route coverage for narration conversion and opening generation task submission, prompt payloads, task IDs, and episode runtime flags.

### Changed

- Moved `POST /api/scripts/{script_id}/episodes/{episode_id}/convert-to-narration` and `POST /api/scripts/{script_id}/episodes/{episode_id}/generate-opening` from `backend/main.py` into `backend/api/routers/episodes.py`.
- Moved the related template resolution and episode text relay submission helpers into the episode router while keeping `backend/main.py` compatibility exports.

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
