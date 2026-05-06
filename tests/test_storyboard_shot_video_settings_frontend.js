const assert = require('assert');
const fs = require('fs');
const path = require('path');

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

const renderStoryboardSidebarSource = extractFunction('renderStoryboardSidebar');

assert(
  renderStoryboardSidebarSource.includes('shotVideoAppointAccountSelect'),
  'storyboard sidebar should render the per-shot appoint account select'
);
assert(
  !renderStoryboardSidebarSource.includes('全局默认视频设置'),
  'storyboard sidebar should not render the legacy global default video settings block'
);
assert(
  !renderStoryboardSidebarSource.includes('当前视频设置'),
  'storyboard sidebar should not render the legacy current video settings block'
);

console.log('test_storyboard_shot_video_settings_frontend.js passed');
