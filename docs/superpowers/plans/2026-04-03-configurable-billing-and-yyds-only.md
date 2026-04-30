# Configurable Billing And YYDS-Only Model Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable billing ledger for new episodes only, record every billable request including retries, support refund-style reversals for failed async tasks, add a `/billing` page with episode and request detail views, and simplify `model-select` to YYDS-only.

**Architecture:** Add two new database-backed concepts: global pricing rules and immutable billing ledger entries. Route all text, image, and video submissions through a small billing service that resolves a pricing rule, snapshots the price onto a ledger row, and later finalizes or reverses async charges. Expose billing summary/detail/rule APIs and a standalone `/billing` page, while shrinking model configuration UI and defaults to YYDS-only.

**Tech Stack:** FastAPI, SQLAlchemy ORM, SQLite/PostgreSQL-compatible schema helpers, vanilla HTML/CSS/JS, `unittest`

---

### Task 1: Add failing tests for pricing rules and billing ledger behavior

**Files:**
- Create: `tests/test_billing_service.py`
- Test: `tests/test_billing_service.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import billing_service
import models


class BillingServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        user = models.User(username="tester", token="token", password_hash="hash", password_plain="123456")
        db.add(user)
        db.flush()
        script = models.Script(user_id=user.id, name="script")
        db.add(script)
        db.flush()
        old_episode = models.Episode(script_id=script.id, name="old-ep", billing_version=0)
        new_episode = models.Episode(script_id=script.id, name="new-ep", billing_version=1)
        db.add(old_episode)
        db.add(new_episode)
        db.flush()
        db.add_all([
            models.BillingPriceRule(
                rule_name="yyds text",
                category="text",
                stage="",
                provider="yyds",
                model_name="",
                billing_mode="per_call",
                unit_price_rmb=Decimal("0.15485"),
                is_active=True,
                priority=100,
            ),
            models.BillingPriceRule(
                rule_name="banana2 image",
                category="image",
                stage="",
                provider="banana",
                model_name="banana2",
                billing_mode="per_image",
                unit_price_rmb=Decimal("0.12"),
                is_active=True,
                priority=100,
            ),
            models.BillingPriceRule(
                rule_name="grok video",
                category="video",
                stage="",
                provider="yijia-grok",
                model_name="grok",
                billing_mode="per_second",
                unit_price_rmb=Decimal("0.049"),
                is_active=True,
                priority=100,
            ),
        ])
        db.commit()
        self.user_id = int(user.id)
        self.script_id = int(script.id)
        self.old_episode_id = int(old_episode.id)
        self.new_episode_id = int(new_episode.id)
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_charge_is_skipped_for_legacy_episode(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.old_episode_id,
                category="text",
                stage="detailed_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="legacy-text-1",
                operation_key="legacy-op",
            )
            self.assertIsNone(entry)
            self.assertEqual(db.query(models.BillingLedgerEntry).count(), 0)
        finally:
            db.close()

    def test_text_charge_uses_per_call_rule(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="detailed_storyboard_stage1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="text-1",
                operation_key="ep1-stage1",
            )
            self.assertIsNotNone(entry)
            self.assertEqual(entry.billing_mode, "per_call")
            self.assertEqual(entry.unit_price_rmb, Decimal("0.15485"))
            self.assertEqual(entry.amount_rmb, Decimal("0.15485"))
            self.assertEqual(entry.status, "finalized")
        finally:
            db.close()

    def test_pending_video_charge_can_be_reversed_once(self):
        db = self.Session()
        try:
            charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="video-1",
                operation_key="shot-1-attempt-1",
                initial_status="pending",
            )
            refund = billing_service.reverse_charge_entry(
                db,
                billing_key="video-1",
                reason="provider_failed",
            )
            duplicate_refund = billing_service.reverse_charge_entry(
                db,
                billing_key="video-1",
                reason="provider_failed",
            )
            self.assertIsNotNone(refund)
            self.assertIsNone(duplicate_refund)
            db.refresh(charge)
            self.assertEqual(charge.amount_rmb, Decimal("0.294"))
            self.assertEqual(charge.status, "reversed")
            self.assertEqual(refund.entry_type, "refund")
            self.assertEqual(refund.amount_rmb, Decimal("-0.294"))
        finally:
            db.close()

    def test_episode_summary_includes_charges_and_refunds(self):
        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="detailed_storyboard_stage1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("2"),
                billing_key="text-2",
                operation_key="stage1",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="image",
                stage="detail_images",
                provider="banana",
                model_name="banana2",
                quantity=Decimal("1"),
                billing_key="image-1",
                operation_key="detail-1",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="video-2",
                operation_key="video-2",
                initial_status="pending",
            )
            billing_service.reverse_charge_entry(db, billing_key="video-2", reason="provider_failed")

            summary = billing_service.get_episode_billing_summary(db, user_id=self.user_id)
            self.assertEqual(len(summary), 1)
            row = summary[0]
            self.assertEqual(row["episode_id"], self.new_episode_id)
            self.assertEqual(row["text_amount_rmb"], "0.30970")
            self.assertEqual(row["image_amount_rmb"], "0.12000")
            self.assertEqual(row["video_amount_rmb"], "0.29400")
            self.assertEqual(row["refund_amount_rmb"], "-0.29400")
            self.assertEqual(row["net_amount_rmb"], "0.42970")
        finally:
            db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_billing_service -v`
Expected: FAIL with `AttributeError`/`ImportError` because `billing_service`, `BillingPriceRule`, or `BillingLedgerEntry` behaviors do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/models.py
class BillingPriceRule(Base):
    __tablename__ = "billing_price_rules"
    ...

class BillingLedgerEntry(Base):
    __tablename__ = "billing_ledger_entries"
    ...

# backend/billing_service.py
def create_charge_entry(...):
    ...

def reverse_charge_entry(...):
    ...

def get_episode_billing_summary(...):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_billing_service -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 2: Add schema bootstrap and new-episode billing gating

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/main.py`
- Test: `tests/test_billing_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_new_episode_defaults_to_billing_version_one(self):
    db = self.Session()
    try:
        episode = models.Episode(script_id=self.script_id, name="fresh-ep")
        db.add(episode)
        db.commit()
        self.assertEqual(episode.billing_version, 1)
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_billing_service.BillingServiceTests.test_new_episode_defaults_to_billing_version_one -v`
Expected: FAIL because `Episode.billing_version` is missing or defaults incorrectly.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/models.py
class Episode(Base):
    ...
    billing_version = Column(Integer, default=1, nullable=False)

# backend/main.py
def ensure_episode_columns():
    ...
    if "billing_version" not in columns:
        conn.execute(text("ALTER TABLE episodes ADD COLUMN billing_version INTEGER DEFAULT 0"))
    conn.execute(text("UPDATE episodes SET billing_version = 0 WHERE billing_version IS NULL"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_billing_service.BillingServiceTests.test_new_episode_defaults_to_billing_version_one -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 3: Seed configurable default pricing rules

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/billing_service.py`
- Test: `tests/test_billing_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_default_rules_seed_for_empty_database(self):
    db = self.Session()
    try:
        db.query(models.BillingPriceRule).delete()
        db.commit()
        billing_service.ensure_default_pricing_rules(db)
        rules = db.query(models.BillingPriceRule).all()
        self.assertGreaterEqual(len(rules), 6)
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_billing_service.BillingServiceTests.test_default_rules_seed_for_empty_database -v`
Expected: FAIL because the seeding helper does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/billing_service.py
DEFAULT_BILLING_RULES = [
    {... "category": "text", "provider": "yyds", "billing_mode": "per_call", "unit_price_rmb": Decimal("0.15485")},
    {... "category": "image", "provider": "banana", "model_name": "banana2", "billing_mode": "per_image", "unit_price_rmb": Decimal("0.12")},
    {... "category": "image", "provider": "banana", "model_name": "banana-pro", "billing_mode": "per_image", "unit_price_rmb": Decimal("0.2")},
    {... "category": "image", "provider": "jimeng", "model_name": "jimeng-4.5", "billing_mode": "per_image", "unit_price_rmb": Decimal("0")},
    {... "category": "image", "provider": "moti", "model_name": "banana2-moti", "billing_mode": "per_image", "unit_price_rmb": Decimal("0")},
    {... "category": "video", "provider": "yijia-grok", "model_name": "grok", "billing_mode": "per_second", "unit_price_rmb": Decimal("0.049")},
]

def ensure_default_pricing_rules(db):
    ...

# backend/main.py
def run_startup_bootstrap():
    ...
    ensure_billing_setup()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_billing_service.BillingServiceTests.test_default_rules_seed_for_empty_database -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 4: Hook synchronous text billing into YYDS request paths

**Files:**
- Modify: `backend/ai_service.py`
- Modify: `backend/main.py`
- Modify: `backend/billing_service.py`
- Create: `tests/test_text_billing_integration.py`
- Test: `tests/test_text_billing_integration.py`

- [ ] **Step 1: Write the failing test**

```python
@patch("ai_service.requests.post")
def test_stage1_successful_upstream_response_records_a_charge_even_if_parse_fails(self, mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.text = '{"choices":[{"message":{"content":"not-json"}}]}'
    mock_post.return_value.json.return_value = {"choices": [{"message": {"content": "not-json"}}]}

    with self.assertRaises(Exception):
        ai_service.stage1_generate_initial_storyboard("{}", episode_id=self.episode_id, batch_id="b1", task_folder="ep_1")

    db = self.Session()
    try:
        entries = db.query(models.BillingLedgerEntry).filter(models.BillingLedgerEntry.episode_id == self.episode_id).all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].stage, "detailed_storyboard_stage1")
        self.assertEqual(str(entries[0].amount_rmb), "0.15485")
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_text_billing_integration -v`
Expected: FAIL because text request success does not create a billing ledger entry yet.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/billing_service.py
def record_text_model_success(...):
    return create_charge_entry(...)

# backend/ai_service.py
response = requests.post(...)
if response.status_code == 200:
    record_text_model_success(
        episode_id=episode_id,
        stage="detailed_storyboard_stage1",
        provider=config["provider_key"],
        model_name=config["model"],
        billing_key=f"{task_folder}:{stage}:attempt{attempt_num}",
        operation_key=f"{task_folder}:{stage}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_text_billing_integration -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 5: Hook async image/video charges and refund reversals

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/image_generation_service.py`
- Modify: `backend/managed_generation_service.py`
- Modify: `backend/video_service.py`
- Modify: `backend/billing_service.py`
- Create: `tests/test_async_billing_lifecycle.py`
- Test: `tests/test_async_billing_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
def test_detail_image_submission_creates_pending_charge_and_failed_poll_creates_refund(self):
    ...
    charge = db.query(models.BillingLedgerEntry).filter(models.BillingLedgerEntry.billing_key == expected_key).one()
    self.assertEqual(charge.status, "pending")
    ...
    refund = db.query(models.BillingLedgerEntry).filter(models.BillingLedgerEntry.parent_entry_id == charge.id).one()
    self.assertEqual(refund.entry_type, "refund")
    self.assertEqual(refund.amount_rmb, Decimal("-0.12"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_async_billing_lifecycle -v`
Expected: FAIL because async submissions do not create pending ledger entries and pollers do not reverse them on failure.

- [ ] **Step 3: Write minimal implementation**

```python
# submission path
pending_entry = billing_service.create_charge_entry(
    ...,
    initial_status="pending",
    billing_key=f"detail_images:{detail_img.id}:{task_id}",
)

# completion path
billing_service.finalize_charge_entry(db, billing_key=f"detail_images:{detail_img.id}:{detail_img.task_id}")

# failure path
billing_service.reverse_charge_entry(db, billing_key=f"detail_images:{detail_img.id}:{detail_img.task_id}", reason="provider_failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_async_billing_lifecycle -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 6: Add billing APIs and `/billing` page

**Files:**
- Modify: `backend/main.py`
- Create: `frontend/billing.html`
- Test: `tests/test_billing_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_episode_billing_api_returns_stage_rollups_and_entries(self):
    response = client.get("/api/billing/episodes", headers=auth_headers)
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertIn("items", payload)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_billing_api -v`
Expected: FAIL because billing routes and page do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/main.py
@app.get("/billing")
async def billing_page():
    return FileResponse("../frontend/billing.html")

@app.get("/api/billing/episodes")
def list_billing_episodes(...):
    ...

@app.get("/api/billing/episodes/{episode_id}")
def get_billing_episode_detail(...):
    ...

@app.get("/api/billing/rules")
def list_billing_rules(...):
    ...

@app.post("/api/billing/rules")
def create_billing_rule(...):
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_billing_api -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 7: Simplify model-select to YYDS-only and expose `/billing` entry

**Files:**
- Modify: `backend/ai_config.py`
- Modify: `backend/main.py`
- Modify: `frontend/model_select.html`
- Modify: `frontend/index.html`
- Modify: `frontend/js/app.js`
- Create: `tests/test_model_select_yyds_only.py`
- Test: `tests/test_model_select_yyds_only.py`

- [ ] **Step 1: Write the failing test**

```python
def test_default_ai_provider_is_yyds(self):
    self.assertEqual(ai_config.get_default_ai_provider_key(), "yyds")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_model_select_yyds_only -v`
Expected: FAIL because default provider is still `openrouter` and public config still exposes OpenRouter.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/ai_config.py
DEFAULT_AI_PROVIDER = "yyds"

def get_ai_provider_public_configs():
    return [{"provider_key": "yyds", ...}]

# frontend/model_select.html
// remove provider tabs and OpenRouter sync button
// render only a single YYDS model library and model mapping table

# frontend/index.html / frontend/js/app.js
// add external /billing navigation entry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_model_select_yyds_only -v`
Expected: PASS

- [ ] **Step 5: Commit**

Because this workspace is not currently a git repository, skip commit and continue to the next task.

### Task 8: Run focused verification for the full change set

**Files:**
- Test: `tests/test_billing_service.py`
- Test: `tests/test_text_billing_integration.py`
- Test: `tests/test_async_billing_lifecycle.py`
- Test: `tests/test_billing_api.py`
- Test: `tests/test_model_select_yyds_only.py`

- [ ] **Step 1: Run the targeted suite**

Run: `python -m unittest tests.test_billing_service tests.test_text_billing_integration tests.test_async_billing_lifecycle tests.test_billing_api tests.test_model_select_yyds_only -v`
Expected: PASS

- [ ] **Step 2: Run existing related regression tests**

Run: `python -m unittest tests.test_dashboard_service tests.test_detail_image_poller tests.test_managed_generation_service -v`
Expected: PASS

- [ ] **Step 3: Record any gaps honestly**

```text
If a command fails, document the exact failing test/module and stop claiming completion until fixed.
```

- [ ] **Step 4: Commit**

Because this workspace is not currently a git repository, skip commit and report verified status with the exact test commands and outputs.
