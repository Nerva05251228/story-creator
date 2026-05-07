const assert = require('assert');
const fs = require('fs');
const path = require('path');

const modelSelectPath = path.join(__dirname, '..', 'frontend', 'model_select.html');
const source = fs.readFileSync(modelSelectPath, 'utf8');

assert(
  source.includes('id="storyboardDefaultsPanel"'),
  'model_select.html should render a storyboard defaults panel'
);
assert(
  source.includes('id="defaultStoryboardImageProviderSelect"'),
  'model_select.html should render a default storyboard image provider select'
);
assert(
  source.includes('id="defaultStoryboardImageModelSelect"'),
  'model_select.html should render a default storyboard image model select'
);
assert(
  source.includes('id="defaultStoryboardVideoModelSelect"'),
  'model_select.html should render a default storyboard video model select'
);
assert(
  source.includes('/api/image-generation/models'),
  'model_select.html should load the public image model catalog for storyboard defaults'
);
assert(
  source.includes('storyboard_defaults'),
  'model_select.html should read storyboard defaults from the admin model-config payload'
);
assert(
  source.includes('/api/admin/storyboard-defaults'),
  'model_select.html should save storyboard defaults through the dedicated admin endpoint'
);
assert(
  source.includes('saveStoryboardDefaults'),
  'model_select.html should expose saveStoryboardDefaults()'
);
assert(
  source.includes('updateStoryboardDefaultImageModels'),
  'model_select.html should keep default image model options in sync with the selected provider'
);

console.log('test_storyboard_model_select_defaults_frontend.js passed');
