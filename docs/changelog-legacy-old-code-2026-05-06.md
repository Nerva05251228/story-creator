# Legacy Old Code Changelog - 2026-05-06

Branch: `codex/legacy-old-code`

This changelog records the legacy-branch-only fixes and feature work completed on 2026-05-06 so the same behavior can be reviewed and ported into `main` manually during the refactor.

## Storyboard Sora

- Enabled multi-select scene cards in the storyboard `sora` right sidebar.
- Preserved scene selection order in `selected_card_ids`.
- Updated storyboard video reference assembly so multiple scene images are included in request order.
- Added per-shot `生成推理提示词` and toolbar `批量生成推理提示词` actions in storyboard `sora`.
- Added backend reasoning prompt tasks that write plain-text results back into `script_excerpt`.
- Added `storyboard_reasoning_prompt_prefix` prompt config so the reasoning prompt prefix can be edited from `/manage`.
- Added `reasoning_prompt_status` tracking for single-shot and batch reasoning prompt generation.

## Moti Account Handling

- Kept frontend display values based on `account_id`.
- Mapped outgoing Moti video requests to `robot_id` on the backend when a matching provider account record exists.
- Removed the global Moti account selector from the shared `图/视频设置` modal.
- Added a per-shot `单独设置账号` selector in the storyboard shot sidebar.
- Added shot-level persistence for `storyboard_video_appoint_account`.

## Subject Image Selection and Preview

- Automatically set the latest uploaded or generated subject image as the reference image.
- Applied the same auto-reference behavior to scene cards.
- Restricted subject preview rendering to reference images only, preventing unselected images from looking active.
- Ensured storyboard-related subject payloads only expose reference previews.

## Providers Stats and Startup

- Routed provider stats polling through the backend to avoid browser CORS failures.
- Corrected the upstream provider stats URL to `/api/video/stats/providers`.
- Added runtime bootstrap coverage for `storyboard_shots.storyboard_video_appoint_account`.
- Updated poller startup to run schema migration before background threads start.
- Updated web and poller PowerShell startup scripts to invoke the project `venv` Python explicitly.

## Verification

- Frontend JS regression tests updated and passing for multi-scene selection, Moti account handling, shot settings UI, provider stats polling, subject preview behavior, and clone payload sync.
- Frontend JS regression tests updated and passing for storyboard reasoning prompt buttons and batch-state handling.
- Backend Python regression tests updated and passing for scene reference ordering, Moti account mapping, startup scripts, provider account helpers, managed generation, storyboard image flow, and reasoning prompt request/result handling.
