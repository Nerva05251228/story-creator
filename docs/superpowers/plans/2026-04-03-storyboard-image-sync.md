# Storyboard Image Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify storyboard(sora) shot-image generation so it carries all selected subject references, persists all returned candidate images, follows the visible video ratio setting, and refreshes subject-card previews immediately after generation completes.

**Architecture:** Reuse the selected-card based reference flow, but centralize it in a helper shared by storyboard image generation and detail image generation. Persist all upstream detail images into `ShotDetailImage.images_json`, let existing viewers consume the full list, and tighten storyboard(sora) ratio selection to the video-ratio setting shown in the current modal. Upgrade subject-card polling from single-card to multi-card so generated image completions can update card previews without a page refresh.

**Tech Stack:** FastAPI, SQLAlchemy, existing polling/background workers, vanilla frontend JS.

---

### Task 1: Reference Collection And Ratio Source

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Modify: `D:\text2image2video_20260310\frontend\js\app.js`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_backend.py`

- [ ] Add failing tests proving storyboard detail-image generation includes role, scene, and prop reference images together.
- [ ] Add failing tests or assertions covering storyboard(sora) generation using the episode video aspect ratio instead of stale `shot_image_size`.
- [ ] Implement a shared helper in `backend/main.py` to collect all selected subject reference URLs in `selected_card_ids` order without filtering out scene cards for storyboard(sora) detail images.
- [ ] Update the storyboard(sora) frontend generation entry points to pass the visible video aspect ratio setting.
- [ ] Verify the targeted tests pass.

### Task 2: Persist All Upstream Candidate Images

**Files:**
- Modify: `D:\text2image2video_20260310\backend\image_generation_service.py`
- Test: `D:\text2image2video_20260310\tests\test_detail_image_poller.py`

- [ ] Add a failing test showing detail-image polling receives multiple upstream images but only one is persisted today.
- [ ] Implement full upstream image persistence by downloading/uploading every returned image, deduplicating, and storing the full list into `ShotDetailImage.images_json`.
- [ ] Keep `storyboard_image_path` synchronized to the first saved image so existing cover behavior still works.
- [ ] Verify the poller tests pass.

### Task 3: Subject Card Live Preview Refresh

**Files:**
- Modify: `D:\text2image2video_20260310\frontend\js\app.js`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_frontend.js`

- [ ] Add failing tests for image polling refreshing only the currently selected card.
- [ ] Expand image polling to watch all cards with in-flight image generation, refresh their generated image lists, and sync their preview thumbnails immediately.
- [ ] Adjust preview fallback logic if needed so newly completed generated images can become visible without a manual page refresh.
- [ ] Verify the targeted frontend tests pass.

### Task 4: Verification

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_storyboard_scene_reference.py`
- Modify: `D:\text2image2video_20260310\tests\test_storyboard_video_reference.py`

- [ ] Run the targeted unittest suites for storyboard/detail-image/reference behavior.
- [ ] Run Python syntax checks for touched backend files.
- [ ] Run a frontend syntax/test pass for touched JS.
