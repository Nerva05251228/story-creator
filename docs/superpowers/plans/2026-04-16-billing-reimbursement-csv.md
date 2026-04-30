# Billing Reimbursement CSV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reimbursement-focused CSV export button to `/billing` that downloads a monthly summary for the current grouping mode.

**Architecture:** Keep aggregation on the backend so the export can cover the full billing dataset instead of only the rows already loaded in the browser. Keep CSV generation in the frontend so the existing admin auth flow and static billing page remain simple.

**Tech Stack:** FastAPI, SQLAlchemy, static HTML/JS, Python unittest, Node assert

---

### Task 1: Lock backend monthly reimbursement aggregation

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_reimbursement_export_groups_monthly_net_amounts_by_script_and_user(self):
        db = self.Session()
        try:
            first_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="reimbursement-month-1",
                provider="yyds",
                model_name="model-a",
                quantity=Decimal("1"),
                billing_key="reimbursement-script-1",
                operation_key="reimbursement-script-op-1",
            )
            second_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.second_new_episode_id,
                category="image",
                stage="reimbursement-month-2",
                provider="banana",
                model_name="banana2",
                resolution="4k",
                quantity=Decimal("1"),
                billing_key="reimbursement-script-2",
                operation_key="reimbursement-script-op-2",
            )
            other_user_charge = billing_service.create_charge_entry(
                db,
                user_id=self.second_user_id,
                script_id=self.other_user_script_id,
                episode_id=self.other_user_episode_id,
                category="text",
                stage="reimbursement-user-1",
                provider="openrouter",
                model_name="model-b",
                quantity=Decimal("1"),
                billing_key="reimbursement-user-1",
                operation_key="reimbursement-user-op-1",
            )
            billing_service.reverse_charge_entry(
                db,
                billing_key="reimbursement-script-2",
                reason="refund-month-2",
            )

            first_charge.created_at = datetime(2026, 1, 15, 8, 30, 0)
            second_charge.created_at = datetime(2026, 2, 3, 9, 0, 0)
            other_user_charge.created_at = datetime(2026, 2, 8, 10, 15, 0)
            refund = db.query(models.BillingLedgerEntry).filter(
                models.BillingLedgerEntry.billing_key == "reimbursement-script-2:refund"
            ).first()
            refund.created_at = datetime(2026, 2, 4, 11, 0, 0)
            db.commit()

            script_rows = billing_service.get_billing_reimbursement_rows(db, group_by="script")
            user_rows = billing_service.get_billing_reimbursement_rows(db, group_by="user")

            self.assertEqual(
                script_rows,
                [
                    {
                        "month": "2026-01",
                        "group_by": "script",
                        "script_id": self.script_id,
                        "script_name": "script",
                        "user_id": self.user_id,
                        "username": "tester",
                        "amount_rmb": "0.18000",
                    },
                    {
                        "month": "2026-02",
                        "group_by": "script",
                        "script_id": self.other_user_script_id,
                        "script_name": "other-user-script",
                        "user_id": self.second_user_id,
                        "username": "tester-2",
                        "amount_rmb": "0.18000",
                    },
                ],
            )
            self.assertEqual(
                user_rows,
                [
                    {
                        "month": "2026-01",
                        "group_by": "user",
                        "user_id": self.user_id,
                        "username": "tester",
                        "amount_rmb": "0.18000",
                    },
                    {
                        "month": "2026-02",
                        "group_by": "user",
                        "user_id": self.second_user_id,
                        "username": "tester-2",
                        "amount_rmb": "0.18000",
                    },
                ],
            )
        finally:
            db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_billing_service.py -k reimbursement_export_groups_monthly_net_amounts_by_script_and_user -q`
Expected: `FAIL` with an `AttributeError` because `get_billing_reimbursement_rows` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def get_billing_reimbursement_rows(
    db,
    *,
    group_by: str = "script",
) -> List[Dict[str, Any]]:
    entries = _query_billing_entries(db)
    if not entries:
        return []

    meta_maps = _build_billing_meta_maps(db, entries)
    buckets: Dict[str, Dict[str, Any]] = {}

    for entry in entries:
        user_row = meta_maps["users"].get(int(entry.user_id))
        script_row = meta_maps["scripts"].get(int(entry.script_id))
        if not user_row or (group_by == "script" and not script_row):
            continue

        month = _format_month_key(entry.created_at)
        if not month:
            continue

        if str(group_by or "script").strip().lower() == "user":
            bucket_key = f"user::{month}::{int(entry.user_id)}"
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "month": month,
                    "group_by": "user",
                    "user_id": int(entry.user_id),
                    "username": str(user_row["username"] or ""),
                    "amount_rmb": Decimal("0"),
                },
            )
        else:
            bucket_key = f"script::{month}::{int(entry.script_id)}"
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "month": month,
                    "group_by": "script",
                    "script_id": int(entry.script_id),
                    "script_name": str(script_row["script_name"] or ""),
                    "user_id": int(entry.user_id),
                    "username": str(user_row["username"] or ""),
                    "amount_rmb": Decimal("0"),
                },
            )

        bucket["amount_rmb"] += _to_decimal(entry.amount_rmb)

    rows = []
    for row in buckets.values():
        if _to_decimal(row["amount_rmb"]) == Decimal("0"):
            continue
        rows.append(
            {
                **{key: value for key, value in row.items() if key != "amount_rmb"},
                "amount_rmb": _format_money(row["amount_rmb"]),
            }
        )

    if str(group_by or "script").strip().lower() == "user":
        rows.sort(key=lambda item: (str(item["month"]), str(item["username"]), int(item["user_id"])))
    else:
        rows.sort(key=lambda item: (str(item["month"]), str(item["script_name"]), int(item["script_id"])))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_billing_service.py -k reimbursement_export_groups_monthly_net_amounts_by_script_and_user -q`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_billing_service.py backend/billing_service.py
git commit -m "feat: add billing reimbursement export aggregation"
```

### Task 2: Expose the admin reimbursement export API

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`

- [ ] **Step 1: Add the route after the existing billing detail endpoints**

```python
@app.get("/api/billing/reimbursement-export")
async def get_billing_reimbursement_export(
    group_by: str = Query("script"),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    _ = user
    _verify_admin_panel_password(x_admin_password)
    normalized_group_by = "user" if str(group_by or "").strip().lower() == "user" else "script"
    return {
        "group_by": normalized_group_by,
        "title": "按用户月度报销汇总" if normalized_group_by == "user" else "按剧本月度报销汇总",
        "rows": billing_service.get_billing_reimbursement_rows(db, group_by=normalized_group_by),
    }
```

- [ ] **Step 2: Smoke-check the Python file for syntax mistakes**

Run: `python -m py_compile backend/main.py backend/billing_service.py`
Expected: command exits successfully with no output.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: expose billing reimbursement export api"
```

### Task 3: Add the billing page export button and CSV builder

**Files:**
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`
- Modify: `D:\text2image2video_20260310\frontend\billing.html`

- [ ] **Step 1: Write the failing frontend assertions**

```javascript
assert.ok(
  billingSource.includes('id="exportReimbursementButton"'),
  'billing.html should expose a reimbursement csv export button',
);

assert.ok(
  billingSource.includes('function buildReimbursementCsv(') &&
    billingSource.includes('function downloadReimbursementCsv(') &&
    billingSource.includes('/api/billing/reimbursement-export'),
  'billing.html should build and download reimbursement csv data from the billing export api',
);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_billing_frontend.js`
Expected: `AssertionError` mentioning the missing reimbursement csv export button or helper functions.

- [ ] **Step 3: Write minimal implementation**

```html
<button class="btn" id="exportReimbursementButton" type="button" onclick="downloadReimbursementCsv()">导出报销 CSV</button>
```

```javascript
function buildReimbursementCsv(payload){
  const groupBy=payload&&payload.group_by==='user'?'user':'script';
  const title=payload&&payload.title?String(payload.title):groupBy==='user'?'按用户月度报销汇总':'按剧本月度报销汇总';
  const rows=Array.isArray(payload&&payload.rows)?payload.rows:[];
  const header=groupBy==='user'?['月份','用户','报销金额（元）']:['月份','剧本','用户','报销金额（元）'];
  const lines=[[title],header];
  rows.forEach((row)=>{
    lines.push(groupBy==='user'
      ? [row.month||'', row.username||'', row.amount_rmb||'0.00000']
      : [row.month||'', row.script_name||'', row.username||'', row.amount_rmb||'0.00000']);
  });
  return '\uFEFF'+lines.map((cols)=>cols.map(csvCell).join(',')).join('\r\n');
}

function csvCell(value){
  const text=String(value==null?'':value);
  return /[",\r\n]/.test(text)?`"${text.replace(/"/g,'""')}"`:text;
}

async function downloadReimbursementCsv(){
  if(!ensureAdminAuth())return;
  const button=document.getElementById('exportReimbursementButton');
  button.disabled=true;
  try{
    setStatus('正在生成报销 CSV...');
    const payload=await apiFetch(`/api/billing/reimbursement-export?group_by=${encodeURIComponent(state.groupMode)}`);
    const csv=buildReimbursementCsv(payload);
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    const dateLabel=new Date().toISOString().slice(0,10);
    link.href=url;
    link.download=`billing-reimbursement-${payload.group_by||state.groupMode}-${dateLabel}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus('报销 CSV 已开始下载。');
  }catch(error){
    console.error('Failed to export reimbursement csv:',error);
    setStatus(error.message||'导出报销 CSV 失败。','error');
  }finally{
    button.disabled=false;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_billing_frontend.js`
Expected: `test_billing_frontend.js passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_billing_frontend.js frontend/billing.html
git commit -m "feat: add billing reimbursement csv download"
```

### Task 4: Run focused verification

**Files:**
- Modify: `D:\text2image2video_20260310\backend\billing_service.py`
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Modify: `D:\text2image2video_20260310\frontend\billing.html`
- Modify: `D:\text2image2video_20260310\tests\test_billing_service.py`
- Modify: `D:\text2image2video_20260310\tests\test_billing_frontend.js`

- [ ] **Step 1: Run the focused backend and frontend tests**

Run: `python -m pytest tests/test_billing_service.py -q`
Expected: billing service tests pass.

Run: `node tests/test_billing_frontend.js`
Expected: `test_billing_frontend.js passed`

- [ ] **Step 2: Run a syntax smoke-check**

Run: `python -m py_compile backend/billing_service.py backend/main.py`
Expected: command exits successfully with no output.

- [ ] **Step 3: Commit**

```bash
git add backend/billing_service.py backend/main.py frontend/billing.html tests/test_billing_service.py tests/test_billing_frontend.js
git commit -m "test: verify billing reimbursement export flow"
```
