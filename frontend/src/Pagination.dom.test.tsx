// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { PageDialog, WorkspaceApp } from './Workspace';
import { PageEditor } from './PageEditor';
import type { Page, Revision, User, Workspace } from './types';
import { ToastProvider } from './ui';

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;
const user: User = { id: 'u', name: 'Owner', email: 'owner@example.com' };
const workspace: Workspace = { id: 'w', name: 'Workspace', role: 'owner' };
function page(id: string, title = id, parent_id: string | null = null): Page {
  return {
    id,
    workspace_id: 'w',
    title,
    parent_id,
    content: 'Published',
    tags: [],
    version: 52,
    deleted_at: null,
    created_at: '2026-10-28T00:00:00Z',
    updated_at: '2026-10-28T00:00:00Z',
    created_by: 'u',
    updated_by: 'u',
  };
}
const mounts: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const close of mounts.splice(0)) await close();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  sessionStorage.clear();
});
async function render(node: React.ReactNode, initial = '/w/w') {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  mounts.push(async () => {
    await act(async () => root.unmount());
    client.clear();
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <MemoryRouter initialEntries={[initial]}>{node}</MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    );
  });
  await settle();
  return host;
}
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}
function button(host: HTMLElement, label: string) {
  return Array.from(host.querySelectorAll('button')).find(
    (item) => item.getAttribute('aria-label') === label || item.textContent === label,
  );
}

it('loads only root metadata until expansion and loads further siblings explicitly', async () => {
  const roots = Array.from({ length: 51 }, (_, i) => ({
    ...page(`r${i}`, `Root ${String(i).padStart(3, '0')}`),
    has_children: i === 0,
  }));
  const children = Array.from({ length: 51 }, (_, i) => ({
    ...page(`c${i}`, `Child ${String(i).padStart(3, '0')}`, 'r0'),
    has_children: false,
  }));
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input, 'http://test');
    const offset = Number(url.searchParams.get('offset') || 0);
    if (url.pathname.endsWith('/pages')) return Response.json([...roots, ...children]);
    if (url.pathname.endsWith('/page-tree') || url.pathname.endsWith('/page-summaries')) {
      const list = url.searchParams.get('parent_id') ? children : roots;
      return Response.json({
        items: list.slice(offset, offset + 50),
        limit: 50,
        offset,
        has_more: offset + 50 < list.length,
      });
    }
    throw new Error(`Unexpected ${url}`);
  });
  const host = await render(<WorkspaceApp user={user} workspaces={[workspace]} />);
  expect(host.querySelectorAll('.tree-row')).toHaveLength(50);
  expect(host.querySelector('.page-tree')?.textContent).not.toContain('Child 000');
  await act(async () => button(host, '展开 Root 000')!.click());
  await settle();
  expect(host.querySelectorAll('.tree-row')).toHaveLength(100);
  await act(async () => button(host, '加载更多 Root 000 的子文档')!.click());
  await settle();
  expect(host.querySelectorAll('.tree-row')).toHaveLength(101);
  expect(host.querySelector('.page-tree')?.textContent).toContain('Child 050');
  await act(async () => button(host, '加载更多根文档')!.click());
  await settle();
  expect(host.querySelectorAll('.tree-row')).toHaveLength(102);
  expect(button(host, '加载更多根文档')).toBeUndefined();
});

it('preserves an unloaded current parent when only the title is edited', async () => {
  const current = page('deep', 'Child', 'outside-batch');
  let saved: Record<string, unknown> | null = null;
  vi.stubGlobal('fetch', async (input: string, options?: RequestInit) => {
    if (input.includes('/page-summaries'))
      return Response.json({ items: [], limit: 50, offset: 0, has_more: false });
    if (options?.method === 'PATCH') {
      saved = JSON.parse(String(options.body));
      return Response.json({ ...current, ...saved });
    }
    throw new Error(`Unexpected ${input}`);
  });
  const host = await render(
    <PageDialog wid="w" pages={[]} page={current} onClose={() => {}} onSaved={() => {}} />,
  );
  expect(host.querySelector<HTMLSelectElement>('select[name="parent_id"]')!.value).toBe(
    'outside-batch',
  );
  await act(async () =>
    host
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
  );
  await settle();
  expect(saved).toMatchObject({ parent_id: 'outside-batch' });
});

it('loads older history in batches', async () => {
  const current = page('p');
  const revisions: Revision[] = Array.from({ length: 52 }, (_, i) => ({
    page_id: 'p',
    version: 52 - i,
    title: `Snapshot ${52 - i}`,
    content: '',
    tags: [],
    parent_id: null,
    created_at: current.created_at,
  }));
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input, 'http://test');
    if (url.pathname.endsWith('/links'))
      return Response.json({ outgoing: [], backlinks: [] });
    if (url.pathname.endsWith('/page-tree'))
      return Response.json({ items: [], limit: 50, offset: 0, has_more: false });
    if (url.pathname.endsWith('/revisions'))
      return Response.json(
        revisions.slice(
          Number(url.searchParams.get('offset') || 0),
          Number(url.searchParams.get('offset') || 0) +
            Number(url.searchParams.get('limit') || 200),
        ),
      );
    return Response.json(current);
  });
  const host = await render(
    <PageEditor
      pid="p"
      workspace={workspace}
      pages={[]}
      user={user}
      onManage={() => {}}
      onCreateChild={() => {}}
    />,
  );
  await act(async () => button(host, '历史版本')!.click());
  await settle();
  expect(host.querySelectorAll('.revision-row')).toHaveLength(50);
  await act(async () => button(host, '加载更早版本')!.click());
  await settle();
  expect(host.querySelectorAll('.revision-row')).toHaveLength(52);
  expect(button(host, '加载更早版本')).toBeUndefined();
});

it('loads search results beyond the first batch', async () => {
  const results = Array.from({ length: 51 }, (_, i) => ({ ...page(`result-${i}`), rank: 1 }));
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input, 'http://test');
    if (url.pathname.endsWith('/search')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      return Response.json(results.slice(offset, offset + 50));
    }
    return Response.json({ items: [], limit: 50, offset: 0, has_more: false });
  });
  const host = await render(<WorkspaceApp user={user} workspaces={[workspace]} />);
  await act(async () => button(host, '搜索')!.click());
  const input = host.querySelector<HTMLInputElement>('input[aria-label="搜索文档"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
      input,
      'query',
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
  await settle();
  expect(host.querySelectorAll('.search-result')).toHaveLength(50);
  expect(button(host, '加载更多搜索结果')).toBeDefined();
  await act(async () => button(host, '加载更多搜索结果')!.click());
  await settle();
  expect(host.querySelectorAll('.search-result')).toHaveLength(51);
  expect(button(host, '加载更多搜索结果')).toBeUndefined();
});

it.each(['success', 'failure'])(
  'discards a late management %s after switching workspaces',
  async (outcome) => {
    let finish!: (response: Response) => void;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const first = page('private', 'Private A document');
    const other: Workspace = { id: 'other', name: 'Other workspace', role: 'owner' };
    vi.stubGlobal('fetch', async (input: string) => {
      if (input === '/api/workspaces/w/pages/private') return delayed;
      return Response.json({
        items: input.includes('/w/') ? [{ ...first, has_children: false }] : [],
        limit: 50,
        offset: 0,
        has_more: false,
      });
    });
    const host = await render(<WorkspaceApp user={user} workspaces={[workspace, other]} />);
    await act(async () => button(host, '管理 Private A document')!.click());
    await act(async () => {
      const select = host.querySelector<HTMLSelectElement>('[aria-label="选择知识空间"]')!;
      select.value = other.id;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();
    await act(async () =>
      finish(
        outcome === 'success'
          ? Response.json(first)
          : Response.json({ detail: 'A request failed' }, { status: 503 }),
      ),
    );
    await settle();
    expect(host.querySelector<HTMLSelectElement>('[aria-label="选择知识空间"]')!.value).toBe(
      other.id,
    );
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(host.querySelector('.toast')).toBeNull();
  },
);

it('closes an open document dialog when changing workspaces', async () => {
  vi.stubGlobal('fetch', async () =>
    Response.json({ items: [], limit: 50, offset: 0, has_more: false }),
  );
  const host = await render(
    <WorkspaceApp user={user} workspaces={[workspace, { ...workspace, id: 'other' }]} />,
  );
  await act(async () => button(host, '新建文档')!.click());
  await settle();
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  await act(async () => {
    const select = host.querySelector<HTMLSelectElement>('[aria-label="选择知识空间"]')!;
    select.value = 'other';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await settle();
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

it('shows and paginates child documents when opening a direct URL without home summaries', async () => {
  const current = page('parent', 'Parent');
  const children = Array.from({ length: 51 }, (_, i) => ({
    ...page(`direct-child-${i}`, `Direct child ${i}`, current.id),
    has_children: false,
  }));
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input, 'http://test');
    if (url.pathname.endsWith('/links'))
      return Response.json({ outgoing: [], backlinks: [] });
    if (url.pathname.endsWith('/page-tree')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      return Response.json({
        items: children.slice(offset, offset + 50),
        limit: 50,
        offset,
        has_more: offset + 50 < children.length,
      });
    }
    return Response.json(current);
  });
  const host = await render(
    <PageEditor
      pid={current.id}
      workspace={workspace}
      pages={[]}
      user={user}
      onManage={() => {}}
      onCreateChild={() => {}}
    />,
  );
  expect(host.querySelectorAll('.document-main .backlink')).toHaveLength(50);
  expect(host.querySelector('.document-main')?.textContent).toContain('Direct child 0');
  await act(async () => button(host, '加载更多子文档')!.click());
  await settle();
  expect(host.querySelectorAll('.document-main .backlink')).toHaveLength(51);
  expect(button(host, '加载更多子文档')).toBeUndefined();
});

it('resolves historical wiki links independently of the current document on a direct URL', async () => {
  const current = {
    ...page('history-page', 'Current title'),
    content: '[[Current target]]',
    version: 2,
  };
  const historical: Revision = {
    page_id: current.id,
    title: 'Historical title',
    content: '[[Old target]]',
    version: 1,
    tags: [],
    parent_id: null,
    created_at: current.created_at,
  };
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input, 'http://test');
    if (url.pathname.endsWith('/revisions/1/links'))
      return Response.json({
        outgoing: [
          { raw_target: 'Old target', target_id: 'historical-target', state: 'resolved' },
        ],
        resolution: 'current_workspace',
      });
    if (url.pathname.endsWith('/links'))
      return Response.json({
        outgoing: [
          {
            id: 'current-link',
            source_id: current.id,
            target_id: 'current-target',
            target_title: 'Current target',
            raw_target: 'Current target',
            state: 'resolved',
            source_version: 2,
          },
        ],
        backlinks: [],
      });
    if (url.pathname.endsWith('/revisions')) return Response.json([historical]);
    if (url.pathname.endsWith('/revisions/1')) return Response.json(historical);
    if (url.pathname.endsWith('/page-tree'))
      return Response.json({ items: [], limit: 50, offset: 0, has_more: false });
    return Response.json(current);
  });
  const host = await render(
    <PageEditor
      pid={current.id}
      workspace={workspace}
      pages={[]}
      user={user}
      onManage={() => {}}
      onCreateChild={() => {}}
    />,
  );
  expect(host.querySelector('.prose a[href="/w/w/p/current-target"]')).not.toBeNull();
  await act(async () => button(host, '历史版本')!.click());
  await settle();
  await act(async () => button(host, '查看')!.click());
  await settle();
  expect(
    host.querySelector('.revision-preview a[href="/w/w/p/historical-target"]'),
  ).not.toBeNull();
  expect(host.querySelector('.revision-preview')?.textContent).toContain(
    '链接按当前空间解析',
  );
});

it.each(['success', 'failure'])(
  'retains a newer create dialog when an earlier management request returns %s',
  async (outcome) => {
    let finish!: (response: Response) => void;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const existing = page('existing', 'Existing document');
    vi.stubGlobal('fetch', async (input: string) => {
      if (input === '/api/workspaces/w/pages/existing') return delayed;
      return Response.json({
        items: [{ ...existing, has_children: false }],
        limit: 50,
        offset: 0,
        has_more: false,
      });
    });
    const host = await render(<WorkspaceApp user={user} workspaces={[workspace]} />);
    await act(async () => button(host, '管理 Existing document')!.click());
    await act(async () => button(host, '新建文档')!.click());
    const title = host.querySelector<HTMLInputElement>('input[name="title"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
        title,
        'Unsaved new document',
      );
      title.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () =>
      finish(
        outcome === 'success'
          ? Response.json(existing)
          : Response.json({ detail: 'Earlier request failed' }, { status: 503 }),
      ),
    );
    await settle();
    expect(host.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe(
      '创建新文档',
    );
    expect(host.querySelector<HTMLInputElement>('input[name="title"]')?.value).toBe(
      'Unsaved new document',
    );
    expect(host.querySelector('.toast')).toBeNull();
  },
);

it('does not reopen an earlier management dialog after a newer dialog is closed', async () => {
  let finish!: (response: Response) => void;
  const delayed = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const existing = page('existing', 'Existing document');
  vi.stubGlobal('fetch', async (input: string) =>
    input === '/api/workspaces/w/pages/existing'
      ? delayed
      : Response.json({
          items: [{ ...existing, has_children: false }],
          limit: 50,
          offset: 0,
          has_more: false,
        }),
  );
  const host = await render(<WorkspaceApp user={user} workspaces={[workspace]} />);
  await act(async () => button(host, '管理 Existing document')!.click());
  await act(async () => button(host, '新建文档')!.click());
  await act(async () => button(host, '关闭对话框')!.click());
  await act(async () => finish(Response.json(existing)));
  await settle();
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});
