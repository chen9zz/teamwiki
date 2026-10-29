import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { Request } from '@playwright/test';
import { createPage, editContent, expect, json, login, saveContent, test, type WikiPage } from './support';

test('login, edit and save through the production UI', async ({ page, space }) => {
  const document = await createPage(space, 'Browser saved document');
  await login(page, space.owner, `/w/${space.id}/p/${document.id}`);
  await expect(page.getByRole('heading', { name: document.title, exact: true })).toBeVisible();
  const content = 'Saved by a real browser against PostgreSQL.';
  await editContent(page, content);
  const saved = await saveContent(page, document.id);
  expect(saved.request().postDataJSON().version).toBe(1);
  await expect(page.locator('.editor-footer')).toContainText('所有修改已保存');
  await expect(page.getByRole('button', { name: '保存文档' })).toBeDisabled();
  await page.reload();
  await expect(page.locator('.prose')).toContainText(content);
  const stored = await json<WikiPage>(
    await space.owner.api.get(`/api/workspaces/${space.id}/pages/${document.id}`),
  );
  expect(stored.content).toBe(content);
  expect(stored.version).toBe(2);
});

test('independent browser sessions compare the same version and retain the 409 draft', async ({
  page,
  browser,
  baseURL,
  space,
}, testInfo) => {
  const document = await createPage(space, 'Concurrent browser document');
  const otherContext = await browser.newContext({ baseURL });
  const other = await otherContext.newPage();
  try {
    const path = `/w/${space.id}/p/${document.id}`;
    await login(page, space.owner, path);
    await login(other, space.owner, path);
    await editContent(page, 'The first independent browser saved this.');
    const draft = 'The second independent browser must retain this unsaved draft.';
    await editContent(other, draft);
    const first = await saveContent(page, document.id);
    const second = await saveContent(other, document.id, 409);
    expect(first.request().postDataJSON().version).toBe(1);
    expect(second.request().postDataJSON().version).toBe(1);
    const comparison = other.getByRole('dialog', { name: '文档有了新的修改' });
    await expect(comparison).toBeVisible();
    await expect(comparison.locator('pre').first()).toContainText(draft);
    await expect(comparison.locator('pre').last()).toContainText('The first independent browser saved this.');
    await comparison.getByRole('button', { name: '保留草稿，继续合并' }).click();
    await expect(other.locator('.cm-content')).toHaveText(draft);
    const storedDraft = await other.evaluate(
      (key) => JSON.parse(sessionStorage.getItem(key) || 'null'),
      `teamwiki-draft:${space.owner.id}:${document.id}`,
    );
    expect(storedDraft.content).toBe(draft);
    expect(storedDraft.version).toBe(2);
    // Reload verifies the real browser's saved draft survives navigation.
    other.once('dialog', (dialog) => dialog.accept());
    await other.reload();
    await expect(other.locator('.cm-content')).toHaveText(draft);
    const stored = await json<WikiPage>(
      await space.owner.api.get(`/api/workspaces/${space.id}/pages/${document.id}`),
    );
    expect(stored.content).toBe('The first independent browser saved this.');
    expect(stored.version).toBe(2);
  } catch (error) {
    const screenshot = testInfo.outputPath('second-browser-failure.png');
    await other.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach('second browser screenshot', { path: screenshot, contentType: 'image/png' });
    throw error;
  } finally {
    await otherContext.close();
  }
});

test('Viewer has read-only controls and the real API rejects a forced write', async ({
  page,
  playwright,
  baseURL,
  space,
}) => {
  const document = await createPage(space, 'Viewer protected document');
  const viewerApi = await playwright.request.newContext({ baseURL });
  const viewer = { email: `e2e-viewer-${randomUUID()}@example.com`, password: `E2e-${randomUUID()}` };
  try {
    await json(
      await viewerApi.post('/api/auth/register', { data: { name: 'Browser viewer', ...viewer } }),
      201,
    );
    const invitation = await json<{ token: string }>(
      await space.owner.api.post(`/api/workspaces/${space.id}/invitations`, {
        data: { email: viewer.email, role: 'viewer' },
      }),
      201,
    );
    await json(await viewerApi.post('/api/invitations/accept', { data: { token: invitation.token } }));
    await login(page, viewer, `/w/${space.id}/p/${document.id}`);
    await expect(page.locator('.prose')).toContainText(document.content);
    await expect(page.getByRole('button', { name: '编辑文档', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '新建文档', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '更多操作', exact: true }).click();
    await expect(page.getByRole('button', { name: '文档设置 / 移动' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '移入回收站' })).toHaveCount(0);
    // Send with this UI session's cookies, not an owner setup session.
    const denied = await page.context().request.patch(
      `/api/workspaces/${space.id}/pages/${document.id}`,
      { data: { version: document.version, content: 'Viewer must never save this.' } },
    );
    expect(denied.status()).toBe(403);
    const stored = await json<WikiPage>(
      await space.owner.api.get(`/api/workspaces/${space.id}/pages/${document.id}`),
    );
    expect(stored.content).toBe(document.content);
    expect(stored.version).toBe(1);
  } finally {
    await viewerApi.dispose();
  }
});

test('nested ZIP uploaded in the UI completes in the real worker and refreshes the tree', async ({
  page,
  space,
}) => {
  await login(page, space.owner, `/w/${space.id}`);
  await page.getByRole('button', { name: /导入 Markdown/ }).click();
  const dialog = page.getByRole('dialog', { name: '导入团队知识' });
  await dialog.getByLabel('选择导入文件').setInputFiles({
    name: 'browser-nested.zip',
    mimeType: 'application/zip',
    buffer: readFileSync(new URL('./fixtures/nested.zip', import.meta.url)),
  });
  const uploaded = page.waitForResponse(
    (response) => response.url().endsWith(`/api/workspaces/${space.id}/imports`) && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: '开始导入', exact: true }).click();
  const job = await (await uploaded).json();
  expect(job.status).toBe('queued');
  await expect(dialog.getByText('导入已完成', { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(dialog.getByText('已处理 3 / 3 篇文档', { exact: true })).toBeVisible();
  const completed = await json<{ status: string; processed: number; result: unknown[] }>(
    await space.owner.api.get(`/api/workspaces/${space.id}/imports/${job.id}`),
  );
  expect(completed.status).toBe('completed');
  expect(completed.processed).toBe(3);
  expect(completed.result).toHaveLength(3);
  await dialog.getByRole('button', { name: '返回知识空间' }).click();
  const tree = page.locator('.page-tree');
  await expect(tree.getByRole('link', { name: 'Handbook', exact: true })).toBeVisible();
  await expect(tree.getByRole('link', { name: 'Overview', exact: true })).toBeVisible();
  await expect(tree.getByRole('link', { name: 'start', exact: true })).toHaveCount(0);
  await tree.getByRole('button', { name: '展开 Handbook', exact: true }).click();
  await expect(tree.getByRole('link', { name: 'policy', exact: true })).toBeVisible();
  await tree.getByRole('button', { name: '展开 Guides', exact: true }).click();
  await tree.getByRole('link', { name: 'start', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'start', exact: true })).toBeVisible();
  await expect(page.locator('.prose')).toContainText('Nested worker import reached the browser.');
});

test('root and child tree collections load bounded batches beyond fifty rows', async ({ page, space }) => {
  const parent = await createPage(space, 'A Parent');
  for (let index = 0; index < 51; index++) {
    await createPage(space, `Root ${String(index).padStart(3, '0')}`);
    await createPage(space, `Child ${String(index).padStart(3, '0')}`, '', parent.id);
  }
  const requests: Request[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === `/api/workspaces/${space.id}/page-tree`) requests.push(request);
  });
  await login(page, space.owner, `/w/${space.id}`);
  const tree = page.locator('.page-tree');
  await expect(tree.getByRole('link')).toHaveCount(50);
  await expect(tree.getByRole('link', { name: 'Root 050', exact: true })).toHaveCount(0);
  await expect(tree.getByRole('link', { name: 'Child 000', exact: true })).toHaveCount(0);
  expect(requests.some((request) => new URL(request.url()).searchParams.has('parent_id'))).toBe(false);
  await tree.getByRole('button', { name: '加载更多根文档', exact: true }).click();
  await expect(tree.getByRole('link', { name: 'Root 050', exact: true })).toBeVisible();
  await expect(tree.getByRole('link')).toHaveCount(52);
  await expect(tree.getByRole('button', { name: '加载更多根文档', exact: true })).toHaveCount(0);
  await tree.getByRole('button', { name: '展开 A Parent', exact: true }).click();
  await expect(tree.getByRole('link', { name: 'Child 049', exact: true })).toBeVisible();
  await expect(tree.getByRole('link', { name: 'Child 050', exact: true })).toHaveCount(0);
  await tree.getByRole('button', { name: '加载更多 A Parent 的子文档', exact: true }).click();
  await expect(tree.getByRole('link', { name: 'Child 050', exact: true })).toBeVisible();
  await expect(tree.getByRole('link')).toHaveCount(103);
  await expect(tree.getByRole('button', { name: '加载更多 A Parent 的子文档', exact: true })).toHaveCount(0);
  const urls = requests.map((request) => new URL(request.url()));
  expect(urls.every((url) => url.searchParams.get('limit') === '50')).toBe(true);
  expect(urls.some((url) => !url.searchParams.has('parent_id') && url.searchParams.get('offset') === '50')).toBe(true);
  expect(urls.some((url) => url.searchParams.get('parent_id') === parent.id && url.searchParams.get('offset') === '50')).toBe(true);
  await tree.getByRole('link', { name: 'Child 050', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Child 050', exact: true })).toBeVisible();
  await tree.getByRole('link', { name: 'A Parent', exact: true }).click();
  await page.reload();
  const article = page.locator('article');
  await expect(article.getByRole('link', { name: 'Child 049', exact: true })).toBeVisible();
  await expect(article.getByRole('link', { name: 'Child 050', exact: true })).toHaveCount(0);
  await article.getByRole('button', { name: '加载更多子文档', exact: true }).click();
  await expect(article.getByRole('link', { name: 'Child 050', exact: true })).toBeVisible();
});

test('history loads older real revisions beyond the first fifty snapshots', async ({ page, space }) => {
  const target = await createPage(space, 'ZZ Historical target');
  let document = await createPage(space, 'History browser document', 'Snapshot 1 [[ZZ Historical target]]');
  for (let version = 2; version <= 52; version++) {
    document = await json<WikiPage>(
      await space.owner.api.patch(`/api/workspaces/${space.id}/pages/${document.id}`, {
        data: { version: document.version, content: `Snapshot ${version}` },
      }),
    );
  }
  await login(page, space.owner, `/w/${space.id}/p/${document.id}`);
  const historyRequests: URL[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/pages/${document.id}/revisions`)) historyRequests.push(url);
  });
  await page.getByRole('button', { name: '历史版本', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '文档历史版本' });
  await expect(dialog.locator('.revision-row')).toHaveCount(50);
  await expect(dialog.getByRole('button', { name: /^版本 1\s/ })).toHaveCount(0);
  await dialog.getByRole('button', { name: '加载更早版本', exact: true }).click();
  await expect(dialog.locator('.revision-row')).toHaveCount(52);
  await expect(dialog.getByRole('button', { name: '加载更早版本', exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: /^版本 1\s/ }).click();
  await expect(dialog.locator('.revision-preview')).toContainText('Snapshot 1');
  await expect(dialog.locator('.revision-preview').getByRole('link', { name: target.title, exact: true }))
    .toHaveAttribute('href', `/w/${space.id}/p/${target.id}`);
  await expect(dialog).toContainText('链接按当前空间解析');
  expect(historyRequests.every((url) => url.searchParams.get('limit') === '50')).toBe(true);
  expect(historyRequests.some((url) => url.searchParams.get('offset') === '50')).toBe(true);
});

test('Chinese search opens real results and a TeamWiki archive retains metadata and internal identities in another space', async ({ page, space }) => {
  const root = await createPage(space, '工程知识手册', '团队工程文档目录。', null, ['工程', 'v0.4']);
  const target = await createPage(space, '部署指南', '归档内正确的部署步骤。', root.id, ['发布']);
  const body = `# 知识沉淀 API 实践\n\n团队知识管理与 API 集成。\n\n[[部署指南|按标题打开]] 与 [[id:${target.id}|按 ID 打开]]。\n\n示例代码：\`[[id:${target.id}]]\``;
  const source = await createPage(space, '知识沉淀 API 实践', body, root.id, ['知识管理', 'API']);
  const destination = await json<{ id: string }>(
    await space.owner.api.post('/api/workspaces', { data: { name: `Archive destination ${randomUUID()}` } }),
    201,
  );
  const destinationSpace = { id: destination.id, owner: space.owner };
  const existing = await createPage(destinationSpace, target.title, '同名已有文档应保持独立。');

  await login(page, space.owner, `/w/${space.id}`);
  await page.getByRole('button', { name: '搜索知识库' }).click();
  const search = page.getByRole('dialog', { name: '搜索团队知识' });
  for (const query of ['知识', '知识 API']) {
    const [searched] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === `/api/workspaces/${space.id}/search` && url.searchParams.get('q') === query;
      }),
      search.getByLabel('搜索文档', { exact: true }).fill(query),
    ]);
    expect(searched.status()).toBe(200);
    await expect(search.locator('.search-result').filter({ has: page.getByText(source.title, { exact: true }) }))
      .toBeVisible();
  }
  await search.locator('.search-result').filter({ has: page.getByText(source.title, { exact: true }) }).click();
  await expect(page.getByRole('heading', { name: source.title, exact: true }).first()).toBeVisible();
  await expect(page.locator('.prose')).toContainText('团队知识管理与 API 集成');

  await page.getByRole('button', { name: '导出知识库', exact: true }).click();
  const exporting = page.getByRole('dialog', { name: '导出团队知识' });
  await exporting.getByLabel('导出格式', { exact: true }).selectOption('teamwiki');
  const [archive, exported] = await Promise.all([
    page.waitForEvent('download'),
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === `/api/workspaces/${space.id}/export` && url.searchParams.get('format') === 'teamwiki';
    }),
    exporting.getByRole('button', { name: '下载 ZIP', exact: true }).click(),
  ]);
  expect(exported.status()).toBe(200);
  expect(archive.suggestedFilename()).toMatch(/\.teamwiki\.zip$/);
  expect(await archive.failure()).toBeNull();
  const archivePath = await archive.path();
  expect(archivePath).not.toBeNull();
  const archiveBody = readFileSync(archivePath!);
  expect(archiveBody.subarray(0, 2).toString()).toBe('PK');

  await page.getByLabel('选择知识空间').selectOption(destination.id);
  await expect(page).toHaveURL(new RegExp(`/w/${destination.id}$`));
  await page.getByRole('button', { name: /导入 Markdown/ }).click();
  const importing = page.getByRole('dialog', { name: '导入团队知识' });
  await importing.getByLabel('选择导入文件').setInputFiles({ name: 'browser-roundtrip.teamwiki.zip', mimeType: 'application/zip', buffer: archiveBody });
  const uploaded = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/workspaces/${destination.id}/imports` && response.request().method() === 'POST');
  await importing.getByRole('button', { name: '开始导入', exact: true }).click();
  const upload = await uploaded;
  expect(upload.status()).toBe(202);
  const job = await upload.json();
  await expect(importing.getByText('导入已完成', { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(importing.getByText('已处理 3 / 3 篇文档', { exact: true })).toBeVisible();
  const completed = await json<{ status: string; result: string[] }>(await space.owner.api.get(`/api/workspaces/${destination.id}/imports/${job.id}`));
  expect(completed.status).toBe('completed');
  expect(completed.result).toHaveLength(3);
  const imported = await Promise.all(completed.result.map(async (id) => json<WikiPage>(await space.owner.api.get(`/api/workspaces/${destination.id}/pages/${id}`))));
  const importedRoot = imported.find((item) => item.title === root.title)!;
  const importedTarget = imported.find((item) => item.title === target.title)!;
  const importedSource = imported.find((item) => item.title === source.title)!;
  expect(importedRoot.tags).toEqual(['工程', 'v0.4']);
  expect(importedSource.tags).toEqual(['知识管理', 'API']);
  expect(importedTarget.tags).toEqual(['发布']);
  expect(importedRoot.parent_id).toBeNull();
  expect(importedTarget.parent_id).toBe(importedRoot.id);
  expect(importedSource.parent_id).toBe(importedRoot.id);
  expect(imported.every((item) => ![root.id, target.id, source.id].includes(item.id))).toBe(true);
  expect(importedTarget.id).not.toBe(existing.id);
  expect(importedSource.content).toContain(`[[id:${importedTarget.id}|按 ID 打开]]`);
  expect(importedSource.content).toContain(`\`[[id:${target.id}]]\``);
  const links = await json<{ outgoing: { target_id: string | null; state: string; raw_target: string }[] }>(await space.owner.api.get(`/api/workspaces/${destination.id}/pages/${importedSource.id}/links`));
  expect(links.outgoing).toHaveLength(2);
  expect(links.outgoing.every((link) => link.state === 'resolved' && link.target_id === importedTarget.id)).toBe(true);
  expect((await json<WikiPage>(await space.owner.api.get(`/api/workspaces/${destination.id}/pages/${existing.id}`))).content).toBe(existing.content);

  await importing.getByRole('button', { name: '返回知识空间', exact: true }).click();
  const tree = page.locator('.page-tree');
  await expect(tree.getByRole('link', { name: root.title, exact: true })).toBeVisible();
  await expect(tree.getByRole('link', { name: source.title, exact: true })).toHaveCount(0);
  await tree.getByRole('button', { name: `展开 ${root.title}`, exact: true }).click();
  await tree.getByRole('link', { name: source.title, exact: true }).click();
  await expect(page.getByRole('heading', { name: source.title, exact: true }).first()).toBeVisible();
  await expect(page.locator('.tag-list')).toContainText('知识管理');
  await expect(page.locator('.tag-list')).toContainText('API');
  for (const label of ['按标题打开', '按 ID 打开']) {
    await expect(page.locator('.prose').getByRole('link', { name: label, exact: true }))
      .toHaveAttribute('href', `/w/${destination.id}/p/${importedTarget.id}`);
  }
  await page.locator('.prose').getByRole('link', { name: '按标题打开', exact: true }).click();
  await expect(page.locator('.prose')).toContainText('归档内正确的部署步骤');
});
