const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appJsPath = path.join(__dirname, '..', 'frontend', 'js', 'app.js');
const source = fs.readFileSync(appJsPath, 'utf8');

function extractFunction(name) {
  const pattern = new RegExp(`(?:async\\s+)?function ${name}\\(.*?\\) \\{[\\s\\S]*?^\\}`, 'm');
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`Function ${name} not found in app.js`);
  }
  return match[0];
}

const sandbox = {
  APP_STATE: {
    providersStats: {},
    currentView: 'storyboard',
    currentShot: { id: 1 },
  },
  apiCalls: [],
  apiRequest(url) {
    sandbox.apiCalls.push(url);
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        providers: [
          { provider: 'yijia', success_rate: 91, average_duration: 12 },
          { provider: 'suchuang', success_rate: 88, average_duration: 15 },
        ],
      }),
    });
  },
  updateProviderSelectDisplayCalled: 0,
  updateProviderSelectDisplay() {
    sandbox.updateProviderSelectDisplayCalled += 1;
  },
  console,
};

vm.createContext(sandbox);
vm.runInContext(extractFunction('fetchProvidersStats'), sandbox);

(async () => {
  await sandbox.fetchProvidersStats();

  assert.deepStrictEqual(sandbox.apiCalls, ['/api/video/providers/stats']);
  assert.strictEqual(sandbox.APP_STATE.providersStats.yijia.success_rate, 91);
  assert.strictEqual(sandbox.APP_STATE.providersStats.suchuang.average_duration, 15);
  assert.strictEqual(sandbox.updateProviderSelectDisplayCalled, 1);

  console.log('test_providers_stats_frontend.js passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
