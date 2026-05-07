const assert = require('assert');
const fs = require('fs');
const path = require('path');

const appJsPath = path.join(__dirname, '..', 'frontend', 'js', 'app.js');
const managePath = path.join(__dirname, '..', 'frontend', 'manage.html');
const appSource = fs.readFileSync(appJsPath, 'utf8');
const manageSource = fs.readFileSync(managePath, 'utf8');

function extractFunction(source, name) {
  const pattern = new RegExp(`(?:async\\s+)?function ${name}\\(.*?\\) \\{[\\s\\S]*?^\\s*\\}`, 'm');
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`Function ${name} not found`);
  }
  return match[0];
}

const openEditShotDurationModalSource = extractFunction(manageSource, 'openEditShotDurationModal');

assert(
  manageSource.includes('storyboardSoraPromptTemplatesGrid'),
  'manage page should render a dedicated storyboard sora prompt template section'
);
assert(
  manageSource.includes('openCreateStoryboardSoraPromptTemplateModal'),
  'manage page should expose create storyboard sora prompt template controls'
);
assert(
  manageSource.includes('toggleTemplateContentCollapsed'),
  'manage page should expose collapsed template cards for large shot and storyboard sora prompt templates'
);
assert(
  !openEditShotDurationModalSource.includes('videoPromptRule'),
  'shot duration edit modal should no longer expose the legacy video prompt rule editor'
);
assert(
  appSource.includes('toggleSoraPromptTemplateMenu(event)') &&
    appSource.includes('renderStoryboardSoraPromptTemplateMenu(') &&
    appSource.includes('generateSoraPrompt('),
  'storyboard sidebar should render a dropdown-style storyboard sora prompt template menu'
);
assert(
  appSource.includes('storyboard_sora_template_id'),
  'storyboard sora generation requests should send the selected storyboard sora template id'
);
assert(
  appSource.includes('/api/storyboard-sora-prompt-templates'),
  'frontend should load storyboard sora prompt templates from the dedicated API'
);
assert(
  appSource.includes('storyboard_sora_template_id'),
  'batch storyboard sora generation should send the selected storyboard sora template id'
);
assert(
  appSource.includes('batchSoraPromptTemplateSelect'),
  'batch storyboard sora generation modal should expose a storyboard sora template selector'
);
assert(
  manageSource.includes('管理全局大镜头模板，用于故事板 Sora 中生成大镜头提示词时选择第一个镜头的运镜策略'),
  'manage page should display the corrected large shot template section description without garbled text'
);

console.log('test_storyboard_sora_prompt_templates_frontend.js passed');
