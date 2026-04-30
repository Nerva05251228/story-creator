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
    currentShot: { id: 4, shot_number: 4, variant_index: 0 },
    shots: [
      { id: 1, shot_number: 1, variant_index: 0, sora_prompt: 'left-right blocking', sora_prompt_status: 'completed' },
      { id: 2, shot_number: 2, variant_index: 0, sora_prompt: 'nearest previous prompt', sora_prompt_status: 'completed' },
      { id: 3, shot_number: 3, sora_prompt: '   ', sora_prompt_status: 'completed' },
      { id: 4, shot_number: 4, variant_index: 0, sora_prompt: 'current old prompt', sora_prompt_status: 'completed' },
      { id: 5, shot_number: 5, variant_index: 0, sora_prompt: 'generating old prompt', sora_prompt_status: 'generating' },
    ],
  },
};

vm.createContext(sandbox);
vm.runInContext([
  extractFunction('getSoraPromptReferenceSortValue'),
  extractFunction('getSoraPromptReferenceCandidates'),
  extractFunction('getDefaultSoraPromptReferenceShotId'),
].join('\n\n'), sandbox);

const candidates = sandbox.getSoraPromptReferenceCandidates();

assert.deepStrictEqual(candidates.map(item => item.id), [1, 2, 4]);
assert.strictEqual(candidates[0].prompt, 'left-right blocking');
assert.strictEqual(candidates[1].label, '镜头 2');
assert.strictEqual(sandbox.getDefaultSoraPromptReferenceShotId(candidates), 2);

console.log('test_storyboard_sora_reference_frontend.js passed');
