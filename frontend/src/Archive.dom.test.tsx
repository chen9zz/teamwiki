// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceApp } from './Workspace';
import { ImportDialog } from './panels';
import { ToastProvider } from './ui';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const mounts: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const close of mounts.splice(0)) await close();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  sessionStorage.clear();
});
async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}
async function render(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  mounts.push(async () => { await act(async () => root.unmount()); client.clear(); });
  await act(async () => {
    root.render(<QueryClientProvider client={client}><ToastProvider><MemoryRouter initialEntries={['/w/w']}>{node}</MemoryRouter></ToastProvider></QueryClientProvider>);
  });
  await settle();
  return host;
}
function button(host: HTMLElement, text: string) {
  return Array.from(host.querySelectorAll('button')).find((item) => item.textContent === text)!;
}
function emptyBatch() { return Response.json({ items: [], limit: 50, offset: 0, has_more: false }); }

// Wrong format URL or starting a download before a format is chosen must fail this flow.
it.each([
  ['teamwiki', '/api/workspaces/w/export?format=teamwiki', 'Research.teamwiki.zip'],
  ['markdown', '/api/workspaces/w/export', 'Research.markdown.zip'],
])('downloads the chosen %s archive through the workspace dialog', async (format, expectedUrl, filename) => {
  const exports: string[] = [];
  const filenames: string[] = [];
  vi.stubGlobal('fetch', async (input: string) => {
    if (input.includes('/export')) { exports.push(input); return new Response('archive'); }
    return emptyBatch();
  });
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:archive');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filenames.push(this.download); });
  const host = await render(<WorkspaceApp user={{ id: 'u', name: 'Owner', email: 'owner@example.com' }} workspaces={[{ id: 'w', name: 'Research', role: 'owner' }]} />);
  await act(async () => button(host, '导出知识库').click());
  const dialog = host.querySelector<HTMLElement>('[role="dialog"][aria-label="导出团队知识"]');
  expect(dialog).not.toBeNull();
  expect(exports).toEqual([]);
  const select = dialog!.querySelector<HTMLSelectElement>('select[aria-label="导出格式"]')!;
  expect(Array.from(select.options).map((option) => option.value)).toEqual(['teamwiki', 'markdown']);
  await act(async () => { select.value = format; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(dialog!.textContent).toContain(format === 'markdown' ? '最多 2,000 篇文档' : '最多 400 篇文档');
  await act(async () => button(dialog!, '下载 ZIP').click());
  await settle();
  expect(exports).toEqual([expectedUrl]);
  expect(filenames).toEqual([filename]);
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

// A failed request must leave the selected format available for a retry.
it('retains the selected export format and exposes an error when a bounded export is rejected', async () => {
  vi.stubGlobal('fetch', async (input: string) => input.includes('/export')
    ? Response.json({ detail: 'Workspace exceeds export limits' }, { status: 413 }) : emptyBatch());
  const host = await render(<WorkspaceApp user={{ id: 'u', name: 'Owner', email: 'owner@example.com' }} workspaces={[{ id: 'w', name: 'Research', role: 'owner' }]} />);
  await act(async () => button(host, '导出知识库').click());
  const dialog = host.querySelector<HTMLElement>('[role="dialog"][aria-label="导出团队知识"]');
  expect(dialog).not.toBeNull();
  await act(async () => button(dialog!, '下载 ZIP').click());
  await settle();
  expect(dialog!.querySelector('[role="alert"]')?.textContent).toContain('Workspace exceeds export limits');
  expect(dialog!.querySelector<HTMLSelectElement>('select')?.value).toBe('teamwiki');
  expect(button(dialog!, '下载 ZIP').disabled).toBe(false);
});

// Selecting an oversized file after a valid one must not submit the stale valid file.
it('clears a prior import selection when the replacement exceeds the upload limit', async () => {
  const uploads: string[] = [];
  vi.stubGlobal('fetch', async (input: string) => { uploads.push(input); return Response.json({}); });
  const host = await render(<ImportDialog wid="w" onClose={() => {}} />);
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
  await act(async () => {
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['# valid'], 'valid.md')] });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(button(host, '开始导入').disabled).toBe(false);
  await act(async () => {
    Object.defineProperty(input, 'files', { configurable: true, value: [new File([new Uint8Array(8 * 1024 * 1024 + 1)], 'oversized.zip')] });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(button(host, '开始导入').disabled).toBe(true);
  expect(uploads).toEqual([]);
});
