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
  extractFunction('resolveThreeViewReferenceImageId'),
  extractFunction('getCardImageGenerationSize'),
].join('\n\n');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

assert.strictEqual(
  sandbox.resolveThreeViewReferenceImageId([
    { id: 1, status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/1.png' },
    { id: 2, status: 'processing', is_reference: true },
    { id: 3, status: 'completed', is_reference: true, image_path: 'https://cdn.example.com/3.png' },
  ]),
  3
);

assert.strictEqual(
  sandbox.resolveThreeViewReferenceImageId([
    { id: 1, status: 'completed', is_reference: false, image_path: 'https://cdn.example.com/1.png' },
  ]),
  null
);

assert.strictEqual(
  sandbox.getCardImageGenerationSize('default', '1:1'),
  '1:1'
);

assert.strictEqual(
  sandbox.getCardImageGenerationSize('three_view', '1:1'),
  '16:9'
);

console.log('test_three_view_request.js passed');
