const assert = require('assert');
const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..');
const billingPagePath = path.join(rootDir, 'frontend', 'billing.html');
const billingSource = fs.readFileSync(billingPagePath, 'utf8');

assert.ok(
  billingSource.includes('id="monthFilterInput"'),
  'billing.html should expose a month filter input',
);

assert.ok(
  billingSource.includes('id="clearMonthFilterButton"'),
  'billing.html should expose a clear-month button',
);

assert.ok(
  billingSource.includes('selectedMonth'),
  'billing.html should track the selected billing month in page state',
);

assert.ok(
  billingSource.includes('function buildBillingQuery(') &&
    billingSource.includes("query.set('month',state.selectedMonth)") &&
    billingSource.includes('buildReimbursementFilename('),
  'billing.html should append the selected month to billing requests and export filenames',
);

console.log('test_billing_month_filter_frontend.js passed');
