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
    cards: [
      { id: 1, card_type: '角色', name: '角色A' },
      { id: 2, card_type: '场景', name: '场景一' },
      { id: 3, card_type: '场景', name: '场景二' },
    ],
    currentShot: {
      id: 99,
      selected_card_ids: '[1,2]',
      use_uploaded_scene_image: false,
      scene_override_locked: false,
      scene_override: '',
    },
  },
  document: {
    querySelector(selector) {
      if (selector === '.storyboard-sidebar-content') {
        return { scrollTop: 24 };
      }
      return null;
    },
  },
  apiCalls: [],
  apiRequest(url, options) {
    sandbox.apiCalls.push({ url, options });
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
  },
  renderStoryboardSidebar() {},
  updateSceneOverrideFromSelection() {},
  applyCurrentShotSceneImageState() {},
  showToast() {},
  console,
};

vm.createContext(sandbox);
vm.runInContext([
  extractFunction('getSelectedShotCardIds'),
  extractFunction('getSelectedStoryboardSceneCardIds'),
  extractFunction('getSelectedStoryboardSceneCardId'),
  extractFunction('toggleShotSubject'),
].join('\n\n'), sandbox);

(async () => {
  await sandbox.toggleShotSubject(3);

  assert.deepStrictEqual(
    JSON.parse(sandbox.APP_STATE.currentShot.selected_card_ids),
    [1, 2, 3],
    'adding a second scene should preserve the previously selected scene'
  );

  await sandbox.toggleShotSubject(2);

  assert.deepStrictEqual(
    JSON.parse(sandbox.APP_STATE.currentShot.selected_card_ids),
    [1, 3],
    'clicking an already-selected scene should only remove that scene'
  );

  assert.strictEqual(sandbox.apiCalls.length, 2);
  assert.deepStrictEqual(JSON.parse(sandbox.apiCalls[0].options.body), {
    selected_card_ids: [1, 2, 3],
  });
  assert.deepStrictEqual(JSON.parse(sandbox.apiCalls[1].options.body), {
    selected_card_ids: [1, 3],
  });

  console.log('test_storyboard_multi_scene_selection_frontend.js passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
