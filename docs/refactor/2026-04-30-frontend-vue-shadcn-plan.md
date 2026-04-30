# Frontend Vue and shadcn-vue Migration Plan

Date: 2026-04-30

## Goal

Move the frontend from static HTML and large global JavaScript files to a typed Vue 3 application using Vite, TypeScript, Vue Router, Pinia, Tailwind, and shadcn-vue.

The migration must be incremental. The legacy frontend remains available until the Vue version passes functional acceptance.

## Current Problems

- `frontend/js/app.js` owns app state, API calls, DOM rendering, modal logic, polling, and workflow control.
- Admin pages duplicate auth, API, escape, modal, table, and toast logic.
- Several pages rely on inline scripts and global functions.
- API paths are scattered through JavaScript and HTML.
- `localStorage` keys are shared implicitly across pages.
- XSS risk exists through `innerHTML`, inline event handlers, and data interpolation.
- The entire `frontend/` directory is exposed under `/static`, including backup files.

## Target Stack

- Vue 3
- Vite
- TypeScript
- Vue Router
- Pinia
- shadcn-vue
- Tailwind CSS
- TanStack Vue Query or domain composables for server state
- Playwright for browser regression

## Target Structure

```text
frontend/
  legacy/
  web/
    package.json
    vite.config.ts
    tailwind.config.ts
    src/
      app/
        router/
        layouts/
        providers/
      pages/
      features/
      entities/
      shared/
        api/
        ui/
        components/
        lib/
```

## Routing Plan

Initial Vue routes:

- `/login`
- `/app/scripts`
- `/app/scripts/:scriptId`
- `/app/scripts/:scriptId/episodes/:episodeId/:step`
- `/app/hit-dramas`
- `/admin/users`
- `/admin/prompts`
- `/admin/model-select`
- `/admin/billing`
- `/admin/billing/rules`
- `/admin/dashboard`

Migration route:

- Serve Vue under `/app-v2` first.
- Keep legacy `/app` until acceptance.
- Cut `/app` to Vue only after sign-off.

## Module Boundaries

### `src/shared/api`

Responsibilities:

- `httpClient`
- auth token injection
- 401 handling
- JSON and FormData requests
- normalized error model
- upload progress helper if needed

Suggested clients:

- `authApi`
- `scriptsApi`
- `episodesApi`
- `shotsApi`
- `cardsApi`
- `storyboardApi`
- `storyboard2Api`
- `videoApi`
- `voiceoverApi`
- `billingApi`
- `adminApi`
- `hitDramasApi`

### `src/shared/components`

Reusable application components:

- `PageHeader`
- `ConfirmDialog`
- `DataTable`
- `MediaPreview`
- `FileDropzone`
- `StatusBadge`
- `AsyncButton`
- `FormSection`
- `PollingIndicator`

### `src/entities`

Typed domain models and small display helpers:

- `user`
- `script`
- `episode`
- `shot`
- `subject-card`
- `template`
- `billing`
- `hit-drama`
- `task`

### `src/features`

Workflow-level features:

- `auth`
- `script-library`
- `creation-flow`
- `subject-cards`
- `simple-storyboard`
- `storyboard2`
- `video-generation`
- `voiceover`
- `billing-admin`
- `dashboard-admin`
- `hit-dramas`

## Security Requirements Before Admin Migration

- Client-side admin password checks must be removed.
- Admin pages must depend on server-side admin authorization.
- Admin secrets must never be bundled into frontend assets.
- API responses must not include plaintext passwords.
- Frontend bundle must not contain private model, image, video, CDN, or LLM service credentials.

## Migration Phases

### Phase 0: Inventory and Smoke Tests

Deliverables:

- Legacy route inventory.
- API endpoint inventory used by frontend.
- `localStorage` key inventory.
- Polling interval inventory.
- Minimal Playwright smoke test for login page and legacy app load.

### Phase 1: Vue Shell

Deliverables:

- Vite/Vue/TypeScript project under `frontend/web`.
- shadcn-vue and Tailwind installed.
- Router and layout foundation.
- Auth token read/write compatibility with legacy keys.
- API client foundation.
- `/app-v2` served by FastAPI.

### Phase 2: Low-Risk Pages

Deliverables:

- Login page.
- Script list.
- Script detail shell.
- Episode list.
- Basic route guards.

### Phase 3: Core Creation Workflow

Order:

1. Subject cards.
2. Simple storyboard.
3. Storyboard table editing.
4. Storyboard2.
5. Voiceover.
6. Video generation and polling.

Each slice must include browser regression coverage before replacing legacy behavior.

### Phase 4: Admin and Operations Pages

Order:

1. Admin authorization fixed on backend.
2. Users and password management.
3. Prompt and model configuration.
4. Billing and billing rules.
5. Dashboard.
6. Hit dramas management.

### Phase 5: Cutover and Cleanup

Deliverables:

- `/app` points to Vue build.
- Legacy pages move to explicit `/legacy` or are removed by approved plan.
- `app.js.backup` and obsolete exposed static assets are removed.
- Browser tests target the Vue app.

## UI Acceptance Criteria

- No in-app text explains implementation details or keyboard shortcuts unless part of existing product behavior.
- Controls use appropriate component types: menus, tabs, checkboxes, toggles, dialogs, data tables, icon buttons, and upload controls.
- Dense operational screens stay utilitarian and scannable.
- Text does not overflow buttons, table cells, dialogs, or sidebars.
- Desktop and mobile breakpoints have no overlapping controls.
- Route changes stop previous polling and do not duplicate requests.
- Console has no unexpected errors in critical workflows.

## Functional Acceptance Criteria

- Login, logout, password change, 401 handling, and user switch state cleanup match current behavior.
- Scripts and episodes can be created, selected, edited, deleted, and restored.
- Subject cards support CRUD, upload, prompt generation, and reference selection.
- Simple and detailed storyboards support editing, import/export, batch generation, retry, and table settings.
- Storyboard2 supports child shots, image generation, video generation, selection, download, cover, and reference image flows.
- Voiceover supports templates, voice reference, emotion/audio settings, generation, and playback.
- Billing, dashboard, model config, prompts, admin users, and hit drama flows match current behavior before legacy removal.
