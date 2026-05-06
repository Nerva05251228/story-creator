const assert = require('assert');
const fs = require('fs');
const path = require('path');

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

const loadStoryboardStepSource = extractFunction('loadStoryboardStep');
const renderStoryboardSidebarSource = extractFunction('renderStoryboardSidebar');

assert(
  loadStoryboardStepSource.includes('batchGenerateStoryboardReasoningPrompts()'),
  'storyboard toolbar should expose batch reasoning prompt generation'
);
assert(
  loadStoryboardStepSource.includes('批量生成推理提示词'),
  'storyboard toolbar should render the batch reasoning prompt button text'
);
assert(
  renderStoryboardSidebarSource.includes('generateStoryboardReasoningPrompt()'),
  'storyboard sidebar should expose per-shot reasoning prompt generation'
);
assert(
  renderStoryboardSidebarSource.includes('生成推理提示词'),
  'storyboard sidebar should render the per-shot reasoning prompt button text'
);

console.log('test_storyboard_reasoning_prompt_frontend.js passed');
