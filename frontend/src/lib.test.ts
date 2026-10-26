import { describe, expect, it } from 'vitest';
import { availableParents, headingList, wikiMarkdown } from './lib';
import type { Page, PageLink } from './types';
const pages = [
  { id: 'a', parent_id: null, title: 'Root' },
  { id: 'b', parent_id: 'a', title: 'Child' },
  { id: 'c', parent_id: 'b', title: 'Grandchild' },
  { id: 'd', parent_id: null, title: 'Other' },
] as Page[];
describe('document navigation', () => {
  it('excludes the page and every descendant from move targets', () => {
    expect(availableParents(pages, 'a').map((p) => p.id)).toEqual(['d']);
  });
  it('uses pinned link IDs so a renamed wiki target still navigates', () => {
    const links = [{ raw_target: 'Old title', target_id: 'a', state: 'resolved' }] as PageLink[];
    expect(wikiMarkdown('See [[Old title]]', pages, links, 'w')).toBe('See [Old title](/w/w/p/a)');
  });
  it('does not silently choose between duplicate title matches', () => {
    expect(wikiMarkdown('[[Root]]', [...pages, { id: 'x', title: 'Root' } as Page], [], 'w')).toBe(
      'Root ⟨待解析⟩',
    );
  });
  it('does not interpret wiki syntax inside fenced or inline code', () => {
    expect(wikiMarkdown('`[[Root]]`\n```md\n[[Root]]\n```', pages, [], 'w')).toBe(
      '`[[Root]]`\n```md\n[[Root]]\n```',
    );
  });
  it('extracts the outline while ignoring headings in code blocks', () => {
    expect(headingList('# Main\n```md\n# Example\n```\n## Next')).toEqual([
      { depth: 1, text: 'Main', id: 'heading-1' },
      { depth: 2, text: 'Next', id: 'heading-5' },
    ]);
  });
});
