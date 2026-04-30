const assert = require('assert');
const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..');
const appJsPath = path.join(rootDir, 'frontend', 'js', 'app.js');
const modelSelectPath = path.join(rootDir, 'frontend', 'model_select.html');
const billingPagePath = path.join(rootDir, 'frontend', 'billing.html');
const billingRulesPagePath = path.join(rootDir, 'frontend', 'billing_rules.html');

const appSource = fs.readFileSync(appJsPath, 'utf8');
const modelSelectSource = fs.readFileSync(modelSelectPath, 'utf8');
const billingSource = fs.readFileSync(billingPagePath, 'utf8');
const billingRulesSource = fs.readFileSync(billingRulesPagePath, 'utf8');

assert.ok(fs.existsSync(billingPagePath), 'billing.html should exist');
assert.ok(fs.existsSync(billingRulesPagePath), 'billing_rules.html should exist');

assert.ok(
  billingSource.includes('id="scriptList"'),
  'billing.html should expose a script list container',
);

assert.ok(
  billingSource.includes('id="primaryList"'),
  'billing.html should expose the primary billing list container',
);

assert.ok(
  billingSource.includes('id="adminAuthModal"') && billingSource.includes('id="adminPasswordInput"'),
  'billing.html should expose an admin auth modal and password input',
);

assert.ok(
  billingSource.includes('id="groupModeUserBtn"') && billingSource.includes('id="groupModeScriptBtn"'),
  'billing.html should expose group mode toggle controls',
);

assert.ok(
  billingSource.includes('function ensureAdminAuth()'),
  'billing.html should gate initialization behind ensureAdminAuth()',
);

assert.ok(
  billingSource.includes('function restoreAdminAuth(){const unlocked=localStorage.getItem(ADMIN_AUTH_STORAGE_KEY)===\'1\';const password=localStorage.getItem(ADMIN_AUTH_PASSWORD_KEY)||\'\';if(unlocked&&password){adminAuthed=true;return true}'),
  'billing.html should restore admin auth from stored password without hardcoded client-side equality checks',
);

assert.ok(
  billingSource.includes("if(response.status===403&&/not authenticated/i.test(detail)){throw new Error('请先登录后再访问计费页')}") &&
    billingSource.includes("if(response.status===403){clearAdminAuth();showAdminAuthModal();document.getElementById('adminAuthError').style.display='block';throw new Error(detail||'管理员密码错误')}"),
  'billing.html should distinguish login failures from admin password failures',
);

assert.ok(
  billingSource.includes('id="billingRulesButton"') && billingSource.includes('/billing-rules'),
  'billing.html should expose a button to open the separate billing rules page',
);

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

assert.ok(
  !billingSource.includes('id="ruleResolutionInput"'),
  'billing.html should no longer embed the billing rules editor',
);

assert.ok(
  billingRulesSource.includes('id="ruleResolutionInput"'),
  'billing_rules.html should expose a resolution field for editing billing rules',
);

assert.ok(
  billingRulesSource.includes('id="ruleTableBody"') && billingRulesSource.includes('保存修改'),
  'billing_rules.html should expose an edit-only rules table and save action',
);

assert.ok(
  !billingRulesSource.includes('新增规则'),
  'billing_rules.html should not expose a create-rule action',
);

assert.ok(
  appSource.includes('function openBillingPanel()'),
  'app.js should expose openBillingPanel()',
);

assert.ok(
  appSource.includes('function buildSimpleStoryboardGeneratingBanner(') &&
    appSource.includes('function buildSimpleStoryboardErrorBanner(') &&
    appSource.includes('function retryFailedSimpleStoryboardBatches('),
  'app.js should expose simple storyboard batch progress and retry helpers',
);

assert.ok(
  appSource.includes('简单分镜存在失败批次，请先重试失败批次') &&
    appSource.includes('failed_batch_errors') &&
    appSource.includes('simple-storyboard/retry-failed-batches'),
  'app.js should block next step on failed simple storyboard batches and call the retry endpoint',
);

assert.ok(
  appSource.includes('onclick="openBillingPanel()"'),
  'storyboard toolbar should include a billing entry button',
);

assert.ok(
  modelSelectSource.includes('OpenRouter') &&
    modelSelectSource.includes('YYDS') &&
    modelSelectSource.includes('id="libraryProviderSelect"') &&
    modelSelectSource.includes("provider_key:providerSelect.value") &&
    modelSelectSource.includes('当前生效：${escHtml(activeProvider.provider_name||cfg.provider_key)} /'),
  'model_select.html should expose provider switching and provider-aware saves',
);

console.log('test_billing_frontend.js passed');
