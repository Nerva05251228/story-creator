# Legacy Old Code Changelog - 2026-05-06

Branch: `codex/legacy-old-code`

This changelog records legacy-branch-only changes completed on 2026-05-06 so the same behavior can be reviewed and ported into `main` manually during the refactor.

## Storyboard Sora

- Enabled multi-select scene cards in the storyboard `sora` right sidebar.
- Preserved scene selection order in `selected_card_ids`.
- Updated storyboard video reference assembly so multiple scene images are included in request order.
- Added per-shot `生成推理提示词` and toolbar `批量生成推理提示词` actions in storyboard `sora`.
- Added backend reasoning prompt tasks that write plain-text results back into `script_excerpt`.
- Added `storyboard_reasoning_prompt_prefix` prompt config so the reasoning prompt prefix can be edited from `/manage`.
- Added `reasoning_prompt_status` tracking for single-shot and batch reasoning prompt generation.
- Added a dedicated storyboard Sora prompt template library seeded from `模板1.txt` and `模板2.txt`.
- Added `/manage` CRUD support for storyboard Sora prompt templates with collapsed cards by default.
- Changed storyboard Sora prompt generation so selecting a storyboard Sora prompt template replaces the full prompt template rather than injecting `extra_style`.
- Changed the storyboard sidebar `生成Sora提示词` button into a dropdown-style template picker and kept it on the far right of the action row.
- Added batch storyboard Sora prompt generation support for choosing a storyboard Sora prompt template.

## Simple Storyboard

- Added a new `规则分段` mode for simple storyboard generation.
- When `规则分段` is selected, the system strictly follows numbered user input blocks such as `1 / 正文 / 2 / 正文 / 3 / 正文`.
- Blank lines are ignored in `规则分段` mode.
- Updated the script tab duration selector so `规则分段` is the default frontend mode for new and unconfigured episodes.
- Added a `规则分段` explanatory card in the `/manage` duration template section.
- Removed the legacy `视频提示词规则` editor from the duration-template edit modal and left duration editing focused on simple storyboard rules plus large-shot prompt rules.

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

- Frontend JS regression tests updated and passing for multi-scene selection, reasoning prompt buttons, shot settings UI, provider stats polling, subject preview behavior, clone payload sync, and rule-segment duration mode.
- Backend Python regression tests updated and passing for scene reference ordering, Moti account mapping, startup scripts, provider account helpers, managed generation, storyboard image flow, reasoning prompt request/result handling, and rule-segment simple storyboard parsing.

## Storyboard Default Models

- Changed the frontend storyboard video default model from `Seedance 2.0 Fast` to `Seedance 2.0 VIP`.
- Extended `/model-select` with a storyboard defaults panel for default storyboard image provider, default storyboard image model, and default storyboard video model.
- Added backend storage and API support for storyboard default model settings and wired them into new episode default resolution so unconfigured episodes follow the configured storyboard defaults instead of hardcoded fallbacks.

## Moti Accounts Resilience

- Updated the default upstream video API token used by the legacy branch to the current Moti key while still preserving environment-variable override priority.
- Increased Moti account list fetch timeout to `180s` because the upstream account endpoint is slow.
- Added backend debug logging for Moti account cache refreshes, upstream status codes, and upstream error payload previews.
- Added frontend debug logging for the Moti account dropdown request payload and storyboard sidebar account state.
- Added postgres-backed fallback snapshots for Moti account lists so the last successful account payload is returned when upstream account queries fail after a restart.
