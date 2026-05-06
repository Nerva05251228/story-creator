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

const helperSource = [
  extractFunction('getCardPreviewImage'),
  extractFunction('ensureSubjectReferenceImage'),
  extractFunction('getCardIdsNeedingImagePolling'),
].join('\n\n');

const sandbox = {
  APP_STATE: {
    cards: [
      { id: 10, card_type: '场景' },
    ],
  },
  apiCalls: [],
  apiRequest(url, options) {
    sandbox.apiCalls.push({ url, options });
    return Promise.resolve({ ok: true });
  },
  console,
};

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
    { status: 'completed', is_reference: true, image_path: 'https://cdn.example.com/current-scene.png' },
    { status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/old-scene.png' },
  ],
  images: [],
});

assert.strictEqual(scenePreview, 'https://cdn.example.com/current-scene.png');

const scenePreviewWithoutReference = sandbox.getCardPreviewImage({
  card_type: '场景',
  generated_images: [
    { status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/new-scene.png' },
  ],
  images: [],
});

assert.strictEqual(scenePreviewWithoutReference, null);

(async () => {
  const normalizedSceneImages = await sandbox.ensureSubjectReferenceImage(10, [
    { id: 1, status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/new-scene.png' },
    { id: 2, status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/old-scene.png' },
  ]);

  assert.strictEqual(sandbox.apiCalls.length, 1);
  assert.strictEqual(sandbox.apiCalls[0].url, '/api/cards/10/reference-images');
  assert.deepStrictEqual(JSON.parse(sandbox.apiCalls[0].options.body), {
    generated_image_ids: [1],
  });
  assert.strictEqual(normalizedSceneImages[0].is_reference, true);
  assert.strictEqual(normalizedSceneImages[1].is_reference, false);

  console.log('test_subject_card_image_polling.js passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
