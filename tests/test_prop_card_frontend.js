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
  extractFunction('groupSubjectCardsByType'),
  extractFunction('resolveSubjectCardType'),
  extractFunction('getDefaultSubjectCardName'),
  extractFunction('getSubjectPromptPlaceholder'),
].join('\n\n');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

const grouped = sandbox.groupSubjectCardsByType([
  { id: 1, card_type: '角色', name: '林七' },
  { id: 2, card_type: '场景', name: '仓库' },
  { id: 3, card_type: '道具', name: '青铜匕首' },
  { id: 4, card_type: '声音', name: '旁白' },
]);

assert.deepStrictEqual(Array.from(grouped.characters).map(item => item.id), [1]);
assert.deepStrictEqual(Array.from(grouped.scenes).map(item => item.id), [2]);
assert.deepStrictEqual(Array.from(grouped.props).map(item => item.id), [3]);
assert.deepStrictEqual(Array.from(grouped.sounds).map(item => item.id), [4]);

assert.strictEqual(sandbox.resolveSubjectCardType('道具'), '道具');
assert.strictEqual(sandbox.resolveSubjectCardType('声音'), '声音');
assert.strictEqual(sandbox.resolveSubjectCardType('未知类型'), '角色');

assert.strictEqual(sandbox.getDefaultSubjectCardName('道具'), '未命名道具');
assert.strictEqual(
  sandbox.getSubjectPromptPlaceholder('道具'),
  '描述道具的材质、结构和关键细节（例如：青铜材质的古旧匕首，刀刃有磨损，木柄缠着发黑布条...）'
);

console.log('test_prop_card_frontend.js passed');
