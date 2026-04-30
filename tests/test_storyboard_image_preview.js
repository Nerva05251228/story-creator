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
  extractFunction('buildShotFirstFrameReferenceCandidates'),
  extractFunction('getShotCardPreviewImageUrl'),
  extractFunction('getShotCardPreviewOverlayText'),
  extractFunction('getShotImageViewerInitialUrl'),
].join('\n\n');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

{
  const shot = {
    storyboard_image_path: 'https://img.example.com/current.png',
  };
  const detailImagesPayload = {
    uploaded_first_frame_reference_image_url: 'https://img.example.com/uploaded-first-frame.png',
    detail_images: [],
  };

  const candidates = sandbox.buildShotFirstFrameReferenceCandidates(shot, detailImagesPayload);

  assert.ok(
    candidates.some((candidate) => candidate.image_url === 'https://img.example.com/uploaded-first-frame.png'),
    'uploaded first-frame image should appear in candidates'
  );
}

assert.ok(
  source.includes('uploadFirstFrameReferenceImage'),
  'app.js should expose a first-frame upload handler',
);

{
  const shot = {
    storyboard_image_path: 'https://img.example.com/current.png',
    first_frame_reference_image_url: 'https://img.example.com/old-selected.png',
    detail_images_preview_path: 'https://img.example.com/fallback.png',
  };

  assert.strictEqual(
    sandbox.getShotCardPreviewImageUrl(shot),
    'https://img.example.com/old-selected.png'
  );
}

{
  const shot = {
    storyboard_image_path: 'https://img.example.com/current.png',
    first_frame_reference_image_url: '',
    detail_images_preview_path: 'https://img.example.com/fallback.png',
  };

  assert.strictEqual(
    sandbox.getShotCardPreviewImageUrl(shot),
    'https://img.example.com/current.png'
  );
}

{
  const shot = {
    detail_images_status: 'processing',
    detail_images_progress: '1/2',
    storyboard_image_path: 'https://img.example.com/current.png',
  };

  assert.strictEqual(
    sandbox.getShotCardPreviewOverlayText(shot),
    '镜头图生成中 1/2'
  );
}

{
  const shot = {
    storyboard_image_path: 'https://img.example.com/newest.png',
    first_frame_reference_image_url: 'https://img.example.com/older.png',
  };
  const detailImagesPayload = {
    detail_images: [
      {
        sub_shot_index: 1,
        time_range: '00-03s',
        images: [
          'https://img.example.com/older.png',
          'https://img.example.com/newest.png',
        ],
      },
    ],
  };

  assert.strictEqual(
    sandbox.getShotImageViewerInitialUrl(shot, detailImagesPayload),
    'https://img.example.com/older.png'
  );
}

console.log('test_storyboard_image_preview.js passed');
