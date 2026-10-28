// @vitest-environment jsdom
import { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { PageEditor } from './PageEditor';
import type { Page, User, Workspace } from './types';

// Exercise the real editor state and route lifecycle while replacing only CodeMirror's
// browser layout dependency with its standard value/onChange interface.
vi.mock('./MarkdownEditor', () => ({
  default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea
      aria-label="测试文档正文"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => {
  sessionStorage.clear();
  document.body.replaceChildren();
});

it('retains unsaved content across SPA navigation and restores it in edit mode', async () => {
  const user: User = { id: 'user-1', name: 'Chen', email: 'chen@example.com' };
  const workspace: Workspace = { id: 'workspace-1', name: 'Team', role: 'owner' };
  const page: Page = {
    id: 'page-1',
    workspace_id: workspace.id,
    parent_id: null,
    title: 'Original title',
    content: 'Published copy',
    tags: [],
    version: 3,
    deleted_at: null,
    created_at: '2026-10-26T00:00:00Z',
    updated_at: '2026-10-26T00:00:00Z',
    created_by: user.id,
    updated_by: user.id,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  client.setQueryData(['pages', workspace.id, 'tree', page.id], {
    pages: [{ items: [], limit: 50, offset: 0, has_more: false }],
    pageParams: [0],
  });
  client.setQueryData(['page', page.id], page);
  client.setQueryData(['links', page.id, page.version], { outgoing: [], backlinks: [] });
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () =>
      root.render(
        <StrictMode>
          <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={['/document']}>
              <Routes>
                <Route
                  path="/document"
                  element={
                    <>
                      <Link to="/away" data-testid="leave">
                        Leave document
                      </Link>
                      <PageEditor
                        pid={page.id}
                        workspace={workspace}
                        pages={[page]}
                        user={user}
                        onManage={() => {}}
                        onCreateChild={() => {}}
                      />
                    </>
                  }
                />
                <Route
                  path="/away"
                  element={
                    <Link to="/document" data-testid="return">
                      Return to document
                    </Link>
                  }
                />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </StrictMode>,
      ),
    );
    const edit = Array.from(host.querySelectorAll('button')).find(
      (button) => button.textContent === '编辑文档',
    );
    expect(edit).toBeDefined();
    await act(async () => {
      edit!.click();
    });
    const input = host.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="测试文档正文"]',
    );
    expect(input).not.toBeNull();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
        input,
        'Unsaved ideas stay here',
      );
      input!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(
      JSON.parse(sessionStorage.getItem(`teamwiki-draft:${user.id}:${page.id}`) || 'null'),
    ).toMatchObject({ content: 'Unsaved ideas stay here', version: 3 });
    await act(async () => {
      host.querySelector<HTMLAnchorElement>('[data-testid="leave"]')!.click();
    });
    expect(host.textContent).toBe('Return to document');
    await act(async () => {
      host.querySelector<HTMLAnchorElement>('[data-testid="return"]')!.click();
    });
    expect(
      host.querySelector<HTMLTextAreaElement>('textarea[aria-label="测试文档正文"]')?.value,
    ).toBe('Unsaved ideas stay here');
    expect(host.querySelector<HTMLInputElement>('input[aria-label="文档标题"]')?.value).toBe(
      page.title,
    );
  } finally {
    await act(async () => root.unmount());
    client.clear();
  }
});
