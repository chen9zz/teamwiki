import { randomUUID } from 'node:crypto';
import {
  test as base,
  expect,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test';

export interface WikiPage {
  id: string;
  workspace_id: string;
  parent_id: string | null;
  title: string;
  content: string;
  version: number;
}

interface Account {
  api: APIRequestContext;
  id: string;
  email: string;
  password: string;
}

interface Space {
  id: string;
  owner: Account;
}

export async function json<T>(response: APIResponse, status = 200): Promise<T> {
  expect(response.status(), await response.text()).toBe(status);
  return response.json() as Promise<T>;
}

// One disposable account per worker and one workspace per test keep data isolated
// while respecting the production authentication rate limit.
export const test = base.extend<{ space: Space }, { owner: Account }>({
  owner: [
    async ({ playwright }, use, workerInfo) => {
      const api = await playwright.request.newContext({
        baseURL: workerInfo.project.use.baseURL,
      });
      const email = `e2e-${randomUUID()}@example.com`;
      const password = `E2e-${randomUUID()}`;
      const { user } = await json<{ user: { id: string } }>(
        await api.post('/api/auth/register', {
          data: { name: 'Browser regression owner', email, password },
        }),
        201,
      );
      await use({ api, id: user.id, email, password });
      await api.dispose();
    },
    { scope: 'worker' },
  ],
  space: async ({ owner }, use) => {
    const workspace = await json<{ id: string }>(
      await owner.api.post('/api/workspaces', {
        data: { name: `Browser regression ${randomUUID()}` },
      }),
      201,
    );
    await use({ id: workspace.id, owner });
  },
});

export { expect };

export async function createPage(
  space: Space,
  title: string,
  content = 'Original browser regression content.',
  parent_id: string | null = null,
): Promise<WikiPage> {
  return json<WikiPage>(
    await space.owner.api.post(`/api/workspaces/${space.id}/pages`, {
      data: { title, content, parent_id },
    }),
    201,
  );
}

export async function login(page: Page, account: Pick<Account, 'email' | 'password'>, path: string) {
  await page.goto('/');
  await page.getByLabel('电子邮箱').fill(account.email);
  await page.getByLabel('密码', { exact: true }).fill(account.password);
  const [response] = await Promise.all([
    page.waitForResponse(
      (item) => new URL(item.url()).pathname === '/api/auth/login' && item.request().method() === 'POST',
    ),
    page.getByRole('button', { name: '登录空间' }).click(),
  ]);
  expect(response.status()).toBe(200);
  await expect(page.getByLabel('电子邮箱')).toHaveCount(0);
  await page.goto(path);
  await expect(page.locator('.sidebar')).toBeVisible();
}

export async function editContent(page: Page, content: string) {
  await page.getByRole('button', { name: '编辑文档', exact: true }).click();
  const editor = page.locator('.cm-content[contenteditable="true"]');
  await expect(editor).toBeVisible();
  await editor.fill(content);
  await expect(editor).toHaveText(content);
}

export async function saveContent(page: Page, pageId: string, expectedStatus = 200) {
  const [saved] = await Promise.all([
    page.waitForResponse(
      (item) =>
        new URL(item.url()).pathname.endsWith(`/pages/${pageId}`) &&
        item.request().method() === 'PATCH',
    ),
    page.getByRole('button', { name: '保存文档' }).click(),
  ]);
  expect(saved.status()).toBe(expectedStatus);
  return saved;
}
