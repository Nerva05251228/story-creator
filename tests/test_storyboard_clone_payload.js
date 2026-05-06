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
  extractFunction('parseSelectedShotSoundCardIds'),
  extractFunction('getResolvedShotSoundCardIds'),
  extractFunction('buildShotCloneSyncPayload'),
].join('\n\n');

const sandbox = {
  APP_STATE: {},
};

vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

const shot = {
  prompt_template: '模板A',
  script_excerpt: '片段',
  storyboard_video_prompt: '视频提示词',
  storyboard_audio_prompt: '音频提示词',
  storyboard_dialogue: '对白',
  scene_override: '花园角落',
  scene_override_locked: true,
  sora_prompt: '完整提示词',
  sora_prompt_status: 'completed',
  selected_card_ids: '[11,22,33]',
  selected_sound_card_ids: '[44,55]',
  aspect_ratio: '16:9',
  duration: 15,
  duration_override_enabled: true,
  provider: 'moti',
  storyboard_video_appoint_account: '罗西剧场',
  storyboard_image_path: 'https://img.example.com/storyboard.jpg',
  storyboard_image_status: 'completed',
  storyboard_image_model: 'banana-pro',
  first_frame_reference_image_url: 'https://img.example.com/storyboard.jpg',
  uploaded_scene_image_url: 'https://img.example.com/scene.jpg',
  use_uploaded_scene_image: true,
};

const payload = sandbox.buildShotCloneSyncPayload(shot);

assert.deepStrictEqual(Array.from(payload.selected_card_ids), [11, 22, 33]);
assert.deepStrictEqual(Array.from(payload.selected_sound_card_ids), [44, 55]);
assert.strictEqual(payload.storyboard_video_appoint_account, '罗西剧场');
assert.strictEqual(payload.storyboard_image_path, 'https://img.example.com/storyboard.jpg');
assert.strictEqual(payload.storyboard_image_status, 'completed');
assert.strictEqual(payload.storyboard_image_model, 'banana-pro');
assert.strictEqual(payload.first_frame_reference_image_url, 'https://img.example.com/storyboard.jpg');
assert.strictEqual(payload.uploaded_scene_image_url, 'https://img.example.com/scene.jpg');
assert.strictEqual(payload.use_uploaded_scene_image, true);
assert.strictEqual(payload.sora_prompt, '完整提示词');
assert.strictEqual(payload.sora_prompt_status, 'completed');
assert.strictEqual(payload.scene_override_locked, true);

console.log('test_storyboard_clone_payload.js passed');
