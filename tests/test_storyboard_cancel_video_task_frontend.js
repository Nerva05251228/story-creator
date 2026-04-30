const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

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

const sandbox = {
  APP_STATE: {
    currentEpisode: 1,
    currentShot: null,
    shots: [
      {
        id: 42,
        shot_number: 7,
        variant_index: 0,
        video_status: 'processing',
        task_id: 'task-processing',
        script_excerpt: 'excerpt',
        sora_prompt_status: 'completed',
      },
      {
        id: 43,
        shot_number: 8,
        variant_index: 0,
        video_status: 'completed',
        task_id: 'task-completed',
        script_excerpt: 'excerpt',
      },
      {
        id: 44,
        shot_number: 9,
        variant_index: 0,
        video_status: 'processing',
        task_id: '',
        managed_task_id: 'old-managed-task',
        script_excerpt: 'excerpt',
      },
    ],
  },
  IMPORT_BATCH_COLORS: ['#2f7edb'],
  apiCalls: [],
  toasts: [],
  eventState: {
    prevented: false,
    stopped: false,
  },
  document: {
    _mockCard: null,
    getElementById(id) {
      if (id !== 'storyboardShotsGrid') {
        return null;
      }
      return this._grid;
    },
    querySelector(selector) {
      if (selector === '[data-shot-id="42"]') {
        return this._mockCard;
      }
      return null;
    },
    _grid: {
      innerHTML: '',
      querySelectorAll() {
        return [];
      },
    },
  },
  getImportBatchesForEpisode() {
    return [];
  },
  getShotLabel(shot) {
    return String(shot.shot_number);
  },
  getShotDetailImagePreviewPath() {
    return '';
  },
  getShotCardPreviewImageUrl() {
    return '';
  },
  getShotCardPreviewOverlayText() {
    return '';
  },
  buildShotCardPreviewImageHtml(url) {
    return `<img src="${url}">`;
  },
  getShotVideoStatusInteractionMeta() {
    return { inlineAttrs: '' };
  },
  apiRequest(url, options) {
    sandbox.apiCalls.push({ url, options });
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
  },
  showToast(message, type) {
    sandbox.toasts.push({ message, type });
  },
  console,
};

sandbox.escapeHtml = (value) => String(value ?? '');

vm.createContext(sandbox);
vm.runInContext(extractFunction('getShotDisplayTaskId'), sandbox);
vm.runInContext(extractFunction('getShotCancelableVideoTaskId'), sandbox);
vm.runInContext(extractFunction('buildShotVideoActionButtonsHtml'), sandbox);
vm.runInContext(extractFunction('renderStoryboardShotsGrid'), sandbox);
vm.runInContext(extractFunction('updateShotCardInDOM'), sandbox);
vm.runInContext(extractFunction('cancelVideoGenerationForShot'), sandbox);

sandbox.renderStoryboardShotsGrid(true);
const html = sandbox.document._grid.innerHTML;

assert.ok(html.includes('取消生成'), 'processing shot card should render a cancel button');
assert.ok(html.includes('cancelVideoGenerationForShot(event, 42)'), 'cancel button should target the processing shot');
assert.ok(!html.includes('cancelVideoGenerationForShot(event, 43)'), 'completed shot should not render a cancel button');

function createClassList(initialClasses) {
  const classes = new Set(initialClasses);
  return {
    contains(className) {
      return classes.has(className);
    },
    add(className) {
      classes.add(className);
    },
    remove(className) {
      classes.delete(className);
    },
  };
}

const actionsRight = {
  innerHTML: '<button class="shot-card-btn-link" onclick="event.stopPropagation(); generateVideoForShot(42)">生成视频</button>',
};
const mockCard = {
  classList: createClassList(['storyboard-shot-card', 'status-idle']),
  querySelector(selector) {
    if (selector === '.shot-card-actions-right') {
      return actionsRight;
    }
    if (selector === '.shot-card-status') {
      return {
        textContent: '未生成',
        className: 'shot-card-status status-idle',
        style: {},
        onclick: null,
      };
    }
    if (selector === '.shot-card-number') {
      return {
        innerHTML: '7',
      };
    }
    return null;
  },
};

sandbox.document._mockCard = mockCard;
sandbox.updateShotCardInDOM({
  id: 42,
  shot_number: 7,
  variant_index: 0,
  video_status: 'processing',
  task_id: 'task-processing',
  script_excerpt: 'excerpt',
});

assert.ok(
  actionsRight.innerHTML.includes('cancelVideoGenerationForShot(event, 42)'),
  'incremental card update should replace generate action with cancel action for processing shots'
);

(async () => {
  await sandbox.cancelVideoGenerationForShot({
    preventDefault() {},
    stopPropagation() {},
  }, 44);

  assert.strictEqual(
    sandbox.apiCalls.length,
    0,
    'cancel should not fall back to display-only managed_task_id when shot.task_id is empty'
  );

  await sandbox.cancelVideoGenerationForShot({
    preventDefault() {
      sandbox.eventState.prevented = true;
    },
    stopPropagation() {
      sandbox.eventState.stopped = true;
    },
  }, 42);

  assert.strictEqual(sandbox.eventState.prevented, true);
  assert.strictEqual(sandbox.eventState.stopped, true);
  assert.strictEqual(sandbox.apiCalls.length, 1);
  assert.strictEqual(sandbox.apiCalls[0].url, '/api/video/tasks/cancel');
  assert.strictEqual(sandbox.apiCalls[0].options.method, 'POST');
  assert.deepStrictEqual(JSON.parse(sandbox.apiCalls[0].options.body), {
    task_ids: ['task-processing'],
  });

  console.log('test_storyboard_cancel_video_task_frontend.js passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
