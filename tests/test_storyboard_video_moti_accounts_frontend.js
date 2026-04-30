const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appJsPath = path.join(__dirname, '..', 'frontend', 'js', 'app.js');
const source = fs.readFileSync(appJsPath, 'utf8');

function extractFunction(name) {
  const pattern = new RegExp(`function ${name}\\(.*?\\) \\{[\\s\\S]*?^\\}`, 'm');
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`Function ${name} not found in app.js`);
  }
  return match[0];
}

const helperSource = [
  extractFunction('normalizeMotiVideoAccountName'),
  extractFunction('getMotiVideoAccountRecords'),
  extractFunction('buildMotiVideoAccountOptionsHtml'),
  extractFunction('getEpisodeStoryboardVideoAppointAccount'),
  extractFunction('buildStoryboardVideoGenerationRequestBody'),
].join('\n\n');

const sandbox = {
  APP_STATE: {
    currentEpisodeInfo: {
      storyboard_video_appoint_account: '罗西剧场',
    },
    motiVideoProviderAccounts: {
      records: [
        { account_id: '罗西剧场', robot_id: '2429291451132548' },
        { account_id: 'cococo', robot_id: '1852023378305080' },
        { robot_id: 'missing-name' },
      ],
    },
  },
  escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },
};

vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

assert.strictEqual(sandbox.normalizeMotiVideoAccountName('  罗西剧场  '), '罗西剧场');
assert.deepStrictEqual(
  sandbox.getMotiVideoAccountRecords().map(item => item.account_id),
  ['罗西剧场', 'cococo']
);

const optionsHtml = sandbox.buildMotiVideoAccountOptionsHtml('罗西剧场');
assert.ok(optionsHtml.includes('<option value="">不指定账号</option>'));
assert.ok(optionsHtml.includes('value="罗西剧场" selected'));
assert.ok(optionsHtml.includes('>罗西剧场</option>'));
assert.ok(!optionsHtml.includes('2429291451132548</option>'));

const missingCachedAccountOptionsHtml = sandbox.buildMotiVideoAccountOptionsHtml('saved-account');
assert.ok(missingCachedAccountOptionsHtml.includes('value="saved-account" selected'));
assert.ok(missingCachedAccountOptionsHtml.includes('>saved-account</option>'));

assert.strictEqual(sandbox.getEpisodeStoryboardVideoAppointAccount(), '罗西剧场');
const requestBody = sandbox.buildStoryboardVideoGenerationRequestBody('罗西剧场');
assert.strictEqual(requestBody.appoint_account, '罗西剧场');
assert.deepStrictEqual(Object.keys(sandbox.buildStoryboardVideoGenerationRequestBody('  ')), []);

console.log('test_storyboard_video_moti_accounts_frontend.js passed');
