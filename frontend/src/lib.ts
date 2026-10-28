import type { Page, Role, WikiLink } from './types';
export const roleLabels: Record<Role, string> = {
  owner: '所有者',
  admin: '管理员',
  editor: '编辑者',
  viewer: '阅读者',
};
export const canWrite = (role: Role) => role !== 'viewer';
export const canManage = (role: Role) => role === 'owner' || role === 'admin';
export function date(value?: string, time = false): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    ...(time ? { hour: '2-digit', minute: '2-digit' } : { year: 'numeric' }),
  });
}
export function availableParents<T extends Pick<Page, 'id' | 'parent_id'>>(
  pages: T[],
  id?: string,
): T[] {
  if (!id) return pages;
  const descendants = new Set([id]);
  let changed = true;
  while (changed) {
    changed = false;
    pages.forEach((page) => {
      if (page.parent_id && descendants.has(page.parent_id) && !descendants.has(page.id)) {
        descendants.add(page.id);
        changed = true;
      }
    });
  }
  return pages.filter((page) => !descendants.has(page.id));
}
export function headingList(content: string): { depth: number; text: string; id: string }[] {
  let fence = '';
  const headings: { depth: number; text: string; id: string }[] = [];
  for (const [lineIndex, line] of content.split('\n').entries()) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1][0];
      else if (fence === marker[1][0]) fence = '';
      continue;
    }
    const match = !fence && line.match(/^(#{1,6})\s+(.+?)(?:\s+#+)?$/);
    if (match)
      headings.push({
        depth: match[1].length,
        text: match[2].replace(/[*_`]/g, ''),
        id: `heading-${lineIndex + 1}`,
      });
  }
  return headings;
}
export function wikiMarkdown(
  content: string,
  pages: Pick<Page, 'id' | 'title'>[],
  links: WikiLink[],
  wid: string,
): string {
  let fence = '';
  return content
    .split('\n')
    .map((line) => {
      const marker = line.match(/^\s*(`{3,}|~{3,})/);
      if (marker) {
        if (!fence) fence = marker[1][0];
        else if (fence === marker[1][0]) fence = '';
        return line;
      }
      if (fence) return line;
      return line
        .split(/(`+[^`]*`+)/g)
        .map((part) => {
          if (part.startsWith('`')) return part;
          return part.replace(/\[\[([^\]\n]+)\]\]/g, (_all, raw: string) => {
            const [target, customLabel] = raw.split('|');
            const label = (customLabel || target.replace(/^id:/, '')).replace(
              /[\\\[\]]/g,
              '\\$&',
            );
            const pinned = links.find(
              (link) => link.raw_target === raw || link.raw_target === target,
            );
            let id = pinned?.state === 'resolved' ? pinned.target_id : null;
            if (!pinned) {
              const matches = target.startsWith('id:')
                ? pages.filter((page) => page.id === target.slice(3))
                : pages.filter((page) => page.title === target);
              if (matches.length === 1) id = matches[0].id;
            }
            return id
              ? `[${label}](/w/${encodeURIComponent(wid)}/p/${encodeURIComponent(id)})`
              : `${label} ⟨${pinned?.state === 'deleted' ? '已删除' : '待解析'}⟩`;
          });
        })
        .join('');
    })
    .join('\n');
}
