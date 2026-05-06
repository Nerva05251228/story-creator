const assert = require('assert');
const fs = require('fs');
const path = require('path');

const appJsPath = path.join(__dirname, '..', 'frontend', 'js', 'app.js');
const appSource = fs.readFileSync(appJsPath, 'utf8');
const managePath = path.join(__dirname, '..', 'frontend', 'manage.html');
const manageSource = fs.readFileSync(managePath, 'utf8');

function extractFunction(source, name) {
  const pattern = new RegExp(`(?:async\\s+)?function ${name}\\(.*?\\) \\{[\\s\\S]*?^\\}`, 'm');
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`Function ${name} not found`);
  }
  return match[0];
}

const loadScriptStepSource = extractFunction(appSource, 'loadScriptStep');

assert(
  loadScriptStepSource.includes('value="35"'),
  'script tab duration select should include the rule segment mode value'
);
assert(
  loadScriptStepSource.includes('<option value="35" selected>规则分段</option>'),
  'script tab create mode should default to the rule segment option'
);
assert(
  loadScriptStepSource.includes('规则分段'),
  'script tab duration select should render the rule segment label'
);
assert(
  loadScriptStepSource.includes("episode.storyboard2_duration === 35 || !episode.storyboard2_duration"),
  'script tab edit mode should default missing duration values to rule segment'
);
assert(
  manageSource.includes('规则分段'),
  'manage page should mention the rule segment mode'
);

console.log('test_rule_segment_simple_storyboard_frontend.js passed');
