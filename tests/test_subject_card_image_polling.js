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
  extractFunction('getCardPreviewImage'),
  extractFunction('getCardIdsNeedingImagePolling'),
].join('\n\n');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

const pollingIds = sandbox.getCardIdsNeedingImagePolling({
  selectedCardForPrompt: null,
  cards: [
    { id: 11, is_generating_images: true, generating_count: 0 },
    { id: 12, is_generating_images: false, generating_count: 2 },
    { id: 13, is_generating_images: false, generating_count: 0 },
  ],
});

assert.deepStrictEqual(Array.from(pollingIds), [11, 12]);

const scenePreview = sandbox.getCardPreviewImage({
  card_type: '场景',
  generated_images: [
    { status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/new-scene.png' },
    { status: 'completed', is_reference: true, image_path: 'https://cdn.example.com/old-scene.png' },
  ],
  images: [],
});

assert.strictEqual(scenePreview, 'https://cdn.example.com/new-scene.png');

console.log('test_subject_card_image_polling.js passed');
