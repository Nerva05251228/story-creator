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
  extractFunction('getStoryboardSidebarScrollTop'),
  extractFunction('restoreStoryboardSidebarScrollTop'),
].join('\n\n');

const sandbox = {
  document: null,
};

vm.createContext(sandbox);
vm.runInContext(helperSource, sandbox);

function createDocument({ contentScrollTop = null, sidebarScrollTop = null } = {}) {
  const contentNode = contentScrollTop === null ? null : { scrollTop: contentScrollTop };
  const sidebarNode = sidebarScrollTop === null ? null : { scrollTop: sidebarScrollTop };
  return {
    querySelector(selector) {
      if (selector === '.storyboard-sidebar-content') {
        return contentNode;
      }
      return null;
    },
    getElementById(id) {
      if (id === 'storyboardSidebar') {
        return sidebarNode;
      }
      return null;
    },
  };
}

{
  sandbox.document = createDocument({ contentScrollTop: 168, sidebarScrollTop: 0 });
  assert.strictEqual(sandbox.getStoryboardSidebarScrollTop(), 168);
}

{
  sandbox.document = createDocument({ contentScrollTop: null, sidebarScrollTop: 42 });
  assert.strictEqual(sandbox.getStoryboardSidebarScrollTop(), 42);
}

{
  const documentMock = createDocument({ contentScrollTop: 12, sidebarScrollTop: 3 });
  sandbox.document = documentMock;
  sandbox.restoreStoryboardSidebarScrollTop(245);
  assert.strictEqual(documentMock.querySelector('.storyboard-sidebar-content').scrollTop, 245);
  assert.strictEqual(documentMock.getElementById('storyboardSidebar').scrollTop, 3);
}

{
  const documentMock = createDocument({ contentScrollTop: null, sidebarScrollTop: 5 });
  sandbox.document = documentMock;
  sandbox.restoreStoryboardSidebarScrollTop(99);
  assert.strictEqual(documentMock.getElementById('storyboardSidebar').scrollTop, 99);
}

console.log('test_storyboard_sidebar_scroll.js passed');
