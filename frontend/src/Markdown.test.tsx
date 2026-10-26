import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { Markdown } from './Markdown';
describe('safe document preview', () => {
  it('does not execute embedded HTML or unsafe link protocols', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <Markdown
          content={
            '# Welcome\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[unsafe](javascript:alert%281%29)'
          }
        />
      </MemoryRouter>,
    );
    expect(html).not.toContain('<script');
    expect(html).not.toContain('onerror');
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain('id="heading-1"');
  });
});
