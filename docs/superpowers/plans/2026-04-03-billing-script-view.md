# Billing Script View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a script-first billing view so billing data can be explored as script > episode > request detail.

**Architecture:** Keep the existing episode detail model, add script-level aggregation functions and API routes on top of the current billing ledger, then update the billing page to a three-column drill-down layout. Reuse existing episode detail rendering for the right-side panel so the change stays incremental.

**Tech Stack:** FastAPI, SQLAlchemy ORM, vanilla HTML/CSS/JS, unittest, Node assert smoke test.

---

### Task 1: Script Aggregation Service

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`

- [ ] Add failing service tests for script list and script detail aggregation.
- [ ] Run the billing service tests and verify the new assertions fail for missing APIs.
- [ ] Implement script-level aggregation helpers by grouping existing billed episodes under each script.
- [ ] Run the billing service tests again and verify they pass.

### Task 2: Billing API Exposure

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`

- [ ] Add API routes for `/api/billing/scripts` and `/api/billing/scripts/{script_id}`.
- [ ] Keep `/api/billing/episodes` and `/api/billing/episodes/{episode_id}` intact for episode drill-down.
- [ ] Return script summary rows plus nested episode summaries for the selected script detail route.

### Task 3: Billing Page Layout

**Files:**
- Modify: `D:\text2image2video_20260310\frontend\billing.html`
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`

- [ ] Update the billing page state model from episode-first to script-first.
- [ ] Change the page layout to three columns: scripts, episodes within selected script, detail panel.
- [ ] Make the right-side detail show script summary when only a script is selected, and episode detail when an episode is selected.
- [ ] Extend the frontend smoke test to assert the new script list container exists.

### Task 4: Verification

**Files:**
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Modify: `D:\text2image2video_20260310\frontend\billing.html`
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`

- [ ] Run `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service -v`.
- [ ] Run `node tests\\test_billing_frontend.js`.
- [ ] Run `D:\text2image2video_20260310\venv\Scripts\python.exe -m compileall backend\\billing_service.py backend\\main.py`.
