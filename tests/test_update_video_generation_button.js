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

const sandbox = {
  APP_STATE: {
    currentShot: {
      id: 42,
      video_status: 'idle',
    },
  },
  document: {
    querySelector(selector) {
      if (selector !== '.storyboard-sidebar-actions') {
        return null;
      }
      return this._actionsDiv;
    },
    _actionsDiv: {
      innerHTML: '',
    },
  },
};

vm.createContext(sandbox);
vm.runInContext(extractFunction('updateVideoGenerationButton'), sandbox);

assert.doesNotThrow(() => sandbox.updateVideoGenerationButton());
assert.ok(sandbox.document._actionsDiv.innerHTML.includes('生成视频'));
assert.ok(sandbox.document._actionsDiv.innerHTML.includes('复制镜头'));

console.log('test_update_video_generation_button.js passed');
