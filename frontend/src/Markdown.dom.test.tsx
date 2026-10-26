// @vitest-environment jsdom
import { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import { Markdown } from './Markdown';
import { headingList } from './lib';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;
afterEach(() => document.body.replaceChildren());

it('every outline target resolves to its rendered heading under StrictMode', async () => {
  const content = '# 浏览器验证\n\n使用 [[指南]] 连接知识。\n\n## 团队链接';
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () =>
      root.render(
        <StrictMode>
          <MemoryRouter>
            <Markdown content={content} />
          </MemoryRouter>
        </StrictMode>,
      ),
    );
    const targets = headingList(content);
    const rendered = Array.from(host.querySelectorAll('h1, h2')).map((heading) => heading.id);
    expect(rendered).toEqual(targets.map((heading) => heading.id));
    for (const target of targets) {
      expect(document.getElementById(target.id)?.textContent).toBe(target.text);
    }
    expect(rendered).toEqual(['heading-1', 'heading-5']);
  } finally {
    await act(async () => root.unmount());
  }
});
