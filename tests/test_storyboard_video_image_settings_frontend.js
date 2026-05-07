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

const openSettingsSource = extractFunction('openStoryboardVideoSettingModal');
const saveSettingsSource = extractFunction('saveStoryboardVideoSettings');

assert(
  source.includes("const DEFAULT_STORYBOARD_VIDEO_MODEL = 'Seedance 2.0 VIP';"),
  'storyboard video settings frontend default model should be Seedance 2.0 VIP'
);
assert(
  openSettingsSource.includes('ensureImageModelCatalogLoaded'),
  'storyboard video settings modal should load the dynamic image model catalog before rendering'
);
assert(
  openSettingsSource.includes('detailImagesProviderSelect'),
  'storyboard video settings modal should render an image provider select'
);
assert(
  !openSettingsSource.includes('storyboardVideoAppointAccountSelect'),
  'storyboard video settings modal should not render the legacy global Moti account select'
);
assert(
  openSettingsSource.includes('updateDetailImagesProviderModels'),
  'storyboard video settings modal should wire provider changes to model options'
);
assert(
  !openSettingsSource.includes('getDetailImagesModelOptionsHtml'),
  'storyboard video settings modal should not render legacy static model options'
);
assert(
  saveSettingsSource.includes('detailImagesProviderSelect'),
  'storyboard video settings save should read the image provider select'
);
assert(
  saveSettingsSource.includes('detail_images_provider'),
  'storyboard video settings save should send detail_images_provider'
);
assert(
  saveSettingsSource.includes('detail_images_model'),
  'storyboard video settings save should send detail_images_model'
);
assert(
  !saveSettingsSource.includes('storyboard_video_appoint_account'),
  'storyboard video settings save should not send the legacy global Moti account field'
);

console.log('test_storyboard_video_image_settings_frontend.js passed');
