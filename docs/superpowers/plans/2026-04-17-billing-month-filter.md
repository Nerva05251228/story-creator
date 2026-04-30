# Billing Month Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add month filtering to `/billing` and make reimbursement exports follow the selected month with timezone-consistent month labels.

**Architecture:** Keep month filtering centralized in the billing service so every billing endpoint reuses the same month logic. Keep the billing page state-driven so one selected month flows into all list/detail requests and the CSV export request.

**Tech Stack:** FastAPI, SQLAlchemy, static HTML/JS, Python unittest, Node assert

---

### Task 1: Lock backend month filtering and Shanghai month grouping

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_reimbursement_export_filters_selected_month(self):
        ...

    def test_reimbursement_month_uses_shanghai_calendar_month(self):
        ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service.BillingServiceTests.test_reimbursement_export_filters_selected_month`
Expected: FAIL because `month` filtering is not implemented yet.

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service.BillingServiceTests.test_reimbursement_month_uses_shanghai_calendar_month`
Expected: FAIL because month grouping still uses raw `created_at.strftime('%Y-%m')`.

- [ ] **Step 3: Write minimal implementation**

```python
def _coerce_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    ...

def _format_billing_month_key(value: Optional[datetime]) -> str:
    ...

def _match_billing_month(value: Optional[datetime], month: Optional[str]) -> bool:
    ...
```

Update `_query_billing_entries(...)` to accept `month`.

Update:
- `get_billing_user_list`
- `get_billing_episode_list`
- `get_billing_script_list`
- `get_episode_billing_detail`
- `get_script_billing_detail`
- `get_billing_reimbursement_rows`

so each forwards the optional month argument.

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service.BillingServiceTests.test_reimbursement_export_filters_selected_month`
Expected: `OK`

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service.BillingServiceTests.test_reimbursement_month_uses_shanghai_calendar_month`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add tests/test_billing_service.py backend/billing_service.py
git commit -m "feat: add billing month filtering"
```

### Task 2: Expose month filtering in billing routes

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`

- [ ] **Step 1: Add `month` query parameters to billing routes**

Update:
- `/api/billing/users`
- `/api/billing/episodes`
- `/api/billing/scripts`
- `/api/billing/scripts/{script_id}`
- `/api/billing/episodes/{episode_id}`
- `/api/billing/reimbursement-export`

so each route passes `month` into the billing service.

- [ ] **Step 2: Run syntax smoke-check**

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m py_compile backend\billing_service.py backend\main.py`
Expected: success with no output.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: pass billing month filters through api"
```

### Task 3: Add month controls and export behavior on the billing page

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`
- Modify: `D:\text2image2video_20260310\frontend\billing.html`

- [ ] **Step 1: Write the failing frontend assertions**

Add assertions for:
- `id="monthFilterInput"`
- `id="clearMonthFilterButton"`
- `month=` appearing in billing fetch URLs
- export filename including the selected month

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_billing_frontend.js`
Expected: FAIL on the new month filter assertions.

- [ ] **Step 3: Write minimal implementation**

Add:
- `state.selectedMonth`
- month input + clear button
- helper that appends `month` when present
- billing loaders that send the selected month
- export filename helper using the selected month
- status text that mentions active month filtering when useful

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_billing_frontend.js`
Expected: the new month filter assertions pass; note any unrelated pre-existing failures separately if they remain.

- [ ] **Step 5: Commit**

```bash
git add tests/test_billing_frontend.js frontend/billing.html
git commit -m "feat: add billing month filter ui"
```

### Task 4: Verify end-to-end behavior against Postgres data

**Files:**
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Modify: `D:\text2image2video_20260310\frontend\billing.html`
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`

- [ ] **Step 1: Run focused backend verification**

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m unittest tests.test_billing_service.BillingServiceTests.test_reimbursement_export_filters_selected_month tests.test_billing_service.BillingServiceTests.test_reimbursement_month_uses_shanghai_calendar_month`
Expected: both tests pass.

- [ ] **Step 2: Inspect real Postgres reimbursement samples**

Run a short script with:
- `group_by='script', month='2026-04'`
- `group_by='user', month='2026-04'`

Expected: both samples show only `2026-04` rows.

- [ ] **Step 3: Run syntax smoke-check**

Run: `D:\text2image2video_20260310\venv\Scripts\python.exe -m py_compile backend\billing_service.py backend\main.py`
Expected: success with no output.

- [ ] **Step 4: Commit**

```bash
git add backend/billing_service.py backend/main.py frontend/billing.html tests/test_billing_service.py tests/test_billing_frontend.js
git commit -m "test: verify billing month filter behavior"
```
