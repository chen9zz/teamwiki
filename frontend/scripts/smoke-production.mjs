/** Execute the real production entry and lazy document/editor chunks in a DOM.
 * API calls are the only mocked boundary: no backend, dev transforms or browser server.
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/w/production-workspace/p/production-page',
  pretendToBeVisual: true,
});
for (const key of [
  'window',
  'document',
  'navigator',
  'HTMLElement',
  'Element',
  'Node',
  'Event',
  'CustomEvent',
  'MutationObserver',
  'localStorage',
  'sessionStorage',
]) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    value: key === 'window' ? dom.window : dom.window[key],
  });
}
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
dom.window.Range.prototype.getClientRects = () => [];
dom.window.Range.prototype.getBoundingClientRect = () => ({
  x: 0,
  y: 0,
  width: 0,
  height: 0,
  top: 0,
  right: 0,
  bottom: 0,
  left: 0,
});
dom.window.HTMLElement.prototype.scrollIntoView = () => {};

const user = { id: 'production-user', email: 'production@example.com', name: 'Production User' };
const workspace = { id: 'production-workspace', name: 'Production workspace', role: 'owner' };
const page = {
  id: 'production-page',
  workspace_id: workspace.id,
  title: 'Production article',
  search_index_truncated: false,
  content: '# Production heading\n\nRendered **production content**.\n\n## Second heading',
  parent_id: null,
  tags: ['production'],
  version: 1,
  deleted_at: null,
  created_at: '2026-10-26T00:00:00Z',
  updated_at: '2026-10-26T00:00:00Z',
  created_by: user.id,
  updated_by: user.id,
};
const summary = {
  id: page.id,
  workspace_id: page.workspace_id,
  parent_id: page.parent_id,
  title: page.title,
  version: page.version,
  updated_at: page.updated_at,
  tags: page.tags,
  has_children: false,
};
const batch = { items: [summary], limit: 50, offset: 0, has_more: false };
const apiData = new Map([
  ['/api/auth/me', user],
  ['/api/workspaces', [workspace]],
  [`/api/workspaces/${workspace.id}/page-tree`, batch],
  [`/api/workspaces/${workspace.id}/page-summaries`, batch],
  [`/api/workspaces/${workspace.id}/pages/${page.id}`, page],
  [`/api/workspaces/${workspace.id}/pages/${page.id}/links`, { outgoing: [], backlinks: [] }],
]);
globalThis.fetch = async (input) => {
  const url = new URL(typeof input === 'string' ? input : input.url, dom.window.location.href);
  if (apiData.has(url.pathname)) return Response.json(apiData.get(url.pathname));
  if (url.pathname.startsWith('/assets/')) return new Response('', { status: 200 });
  throw new Error(`Unexpected production smoke request: ${url.pathname}`);
};
const runtimeErrors = [];
const originalError = console.error;
console.error = (...values) => {
  runtimeErrors.push(values.map(String).join(' '));
  originalError(...values);
};
dom.window.addEventListener('error', (event) =>
  runtimeErrors.push(event.error?.stack || event.message),
);
async function waitFor(check, description) {
  const deadline = Date.now() + 5000;
  while (!check()) {
    if (runtimeErrors.length)
      throw new Error(`Production runtime failed: ${runtimeErrors.join('\n')}`);
    if (Date.now() >= deadline)
      throw new Error(`Timed out waiting for ${description}. DOM: ${document.body.textContent}`);
    await new Promise((resolve) => setTimeout(resolve, 15));
  }
}
try {
  const html = await readFile(resolve('dist/index.html'), 'utf8');
  const entry = html.match(/<script[^>]+src="([^"]+)"/);
  assert.ok(entry, 'production index must reference its JavaScript entry');
  await import(pathToFileURL(resolve('dist', `.${entry[1]}`)).href);
  await waitFor(
    () => document.querySelector('.prose h1')?.textContent === 'Production heading',
    'the lazy document preview',
  );
  assert.equal(document.querySelector('.prose strong')?.textContent, 'production content');
  const edit = Array.from(document.querySelectorAll('button')).find(
    (button) => button.textContent === '编辑文档',
  );
  assert.ok(edit, 'the production reader must offer editing');
  edit.click();
  await waitFor(
    () => document.querySelector('.cm-content')?.textContent.includes('Production heading'),
    'the lazy CodeMirror editor',
  );
  const read = Array.from(document.querySelectorAll('button')).find(
    (button) => button.textContent === '阅读',
  );
  assert.ok(read, 'the production editor must offer the reader');
  read.click();
  await waitFor(
    () => document.querySelector('.prose h2')?.textContent === 'Second heading',
    'the Markdown preview after editing',
  );
  const exportButton = Array.from(document.querySelectorAll('button')).find(
    (button) => button.textContent === '导出知识库',
  );
  assert.ok(exportButton, 'the compiled workspace must offer archive export');
  exportButton.click();
  await waitFor(
    () => document.querySelector('[role="dialog"][aria-label="导出团队知识"]'),
    'the archive format dialog',
  );
  const exportDialog = document.querySelector('[role="dialog"][aria-label="导出团队知识"]');
  const format = exportDialog.querySelector('select[aria-label="导出格式"]');
  assert.deepEqual(Array.from(format.options, (option) => option.value), ['teamwiki', 'markdown']);
  assert.equal(format.value, 'teamwiki');
  exportDialog.querySelector('button[aria-label="关闭对话框"]').click();
  await waitFor(() => !document.querySelector('[role="dialog"]'), 'archive dialog dismissal');
  assert.deepEqual(runtimeErrors, [], 'production document flow must not report runtime errors');
  console.log('Production bundle smoke passed: document → CodeMirror editor → Markdown preview → archive format dialog.');
  dom.window.close();
  process.exit(0);
} catch (error) {
  console.error(error);
  dom.window.close();
  process.exit(1);
}
