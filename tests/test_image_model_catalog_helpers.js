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
  extractFunction('normalizeImageModelCatalogResponse'),
  extractFunction('getImageProviders'),
  extractFunction('getImageModelsForProvider'),
  extractFunction('getImageRouteOptions'),
  extractFunction('getDefaultImageSelection'),
  extractFunction('normalizeDetailImagesModel'),
  extractFunction('normalizeDetailImagesProvider'),
].join('\n\n');

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

const oldCatalog = sandbox.normalizeImageModelCatalogResponse({
  models: {
    'jimeng-4.0': {
      name: 'Jimeng 4.0',
      provider: 'jimeng',
      sizes: ['1:1', '9:16'],
      resolutions: ['2K', '4K'],
    },
    'banana-pro': {
      name: 'Banana Pro',
      provider: 'moti',
      sizes: ['16:9'],
      resolutions: ['1K'],
    },
    midjourney: {
      name: 'Midjourney',
      provider: 'midjourney',
      sizes: ['1:1'],
    },
    'gpt-image-1.5': {
      name: 'GPT Image 1.5',
      provider: 'openai',
      sizes: ['1:1'],
    },
  },
});

assert.deepStrictEqual(
  plain(sandbox.getImageProviders(oldCatalog).map(provider => provider.value)),
  ['jimeng', 'moti']
);
assert.deepStrictEqual(
  plain(sandbox.getImageModelsForProvider(oldCatalog, 'moti').map(model => model.value)),
  ['banana-pro']
);
assert.deepStrictEqual(
  plain(sandbox.getImageRouteOptions(oldCatalog, 'jimeng', 'jimeng-4.0')),
  {
    sizes: ['1:1', '9:16'],
    resolutions: ['2K', '4K'],
    supports_reference: false,
  }
);

const newCatalog = sandbox.normalizeImageModelCatalogResponse({
  models: [
    {
      provider: 'fal',
      provider_label: 'FAL',
      id: 'flux-pro',
      label: 'Flux Pro',
      ratios: ['4:3', '16:9'],
      resolutions: ['HD'],
      supports_reference: true,
    },
    {
      provider: 'openai',
      model: 'gpt-image-1',
      name: 'GPT Image 1',
      aspect_ratios: ['1:1'],
    },
    {
      provider: 'openai',
      model: 'gpt-image-1.5',
      name: 'GPT Image 1.5',
      aspect_ratios: ['1:1'],
    },
  ],
});

assert.deepStrictEqual(
  plain(sandbox.getImageProviders(newCatalog).map(provider => provider.value)),
  ['fal', 'openai']
);
assert.deepStrictEqual(
  plain(sandbox.getImageModelsForProvider(newCatalog, 'fal')),
  [{ value: 'flux-pro', label: 'Flux Pro' }]
);
assert.deepStrictEqual(
  plain(sandbox.getImageRouteOptions(newCatalog, 'fal', 'flux-pro')),
  {
    sizes: ['4:3', '16:9'],
    resolutions: ['HD'],
    supports_reference: true,
  }
);
assert.deepStrictEqual(
  plain(sandbox.getImageModelsForProvider(newCatalog, 'openai').map(model => model.value)),
  ['gpt-image-1']
);

assert.deepStrictEqual(
  plain(sandbox.getDefaultImageSelection(newCatalog, 'openai', 'missing-model')),
  {
    provider: 'openai',
    model: 'gpt-image-1',
    size: '1:1',
    resolution: null,
  }
);
assert.deepStrictEqual(
  plain(sandbox.getDefaultImageSelection(newCatalog, 'fal', 'flux-pro')),
  {
    provider: 'fal',
    model: 'flux-pro',
    size: '16:9',
    resolution: 'HD',
  }
);

assert.strictEqual(sandbox.normalizeDetailImagesModel('jimeng-4.0'), 'seedream-4.0');
assert.strictEqual(sandbox.normalizeDetailImagesModel('banana2'), 'nano-banana-2');
assert.strictEqual(sandbox.normalizeDetailImagesModel('banana-pro'), 'nano-banana-pro');
assert.strictEqual(sandbox.normalizeDetailImagesModel('', 'gpt-image-2'), 'gpt-image-2');
assert.strictEqual(sandbox.normalizeDetailImagesProvider('banana'), 'momo');
assert.strictEqual(sandbox.normalizeDetailImagesProvider('', 'JiMeng'), 'jimeng');

const upstreamCatalog = sandbox.normalizeImageModelCatalogResponse({
  models: [
    {
      key: 'seedream-4.0',
      model: 'Seedream 4.0',
      display_name: 'Seedream 4.0',
      default_provider: 'jimeng',
      ratios: ['1:1', '16:9'],
      resolutions: ['1K', '2K'],
      supports_reference: true,
      providers: [
        {
          provider: 'jimeng',
          ratios: ['1:1'],
          resolutions: ['2K'],
          supports_reference: true,
          enabled: true,
        },
        {
          provider: 'momo',
          ratios: ['16:9'],
          resolutions: ['1K', '4K'],
          supports_reference: true,
          enabled: true,
        },
      ],
    },
    {
      key: 'midjourney',
      model: 'Midjourney',
      display_name: 'Midjourney',
      providers: [
        {
          provider: 'momo',
          ratios: ['1:1'],
          resolutions: ['1K'],
          enabled: true,
        },
      ],
    },
  ],
});

assert.deepStrictEqual(
  plain(sandbox.getImageProviders(upstreamCatalog).map(provider => provider.value)),
  ['jimeng', 'momo']
);
assert.deepStrictEqual(
  plain(sandbox.getImageModelsForProvider(upstreamCatalog, 'momo')),
  [{ value: 'seedream-4.0', label: 'Seedream 4.0' }]
);
assert.deepStrictEqual(
  plain(sandbox.getImageRouteOptions(upstreamCatalog, 'momo', 'seedream-4.0')),
  {
    sizes: ['16:9'],
    resolutions: ['1K', '4K'],
    supports_reference: true,
  }
);

const mixedDefaultCatalog = sandbox.normalizeImageModelCatalogResponse({
  models: [
    {
      key: 'nano-banana-2',
      display_name: 'Nano Banana 2',
      providers: [
        {
          provider: 'momo',
          ratios: ['1:1'],
          enabled: true,
        },
      ],
    },
    {
      key: 'seedream-4.0',
      display_name: 'Seedream 4.0',
      providers: [
        {
          provider: 'jimeng',
          ratios: ['9:16'],
          enabled: true,
        },
      ],
    },
  ],
});

assert.deepStrictEqual(
  plain(sandbox.getDefaultImageSelection(mixedDefaultCatalog, null, null)),
  {
    provider: 'jimeng',
    model: 'seedream-4.0',
    size: '9:16',
    resolution: null,
  }
);

console.log('test_image_model_catalog_helpers.js passed');
