"""Synchronous link indexing, protected by the workspace write lock.

A resolved raw target is pinned to a page ID. Title collisions only affect new
or unresolved references; renaming a target never silently retargets a link.
"""

import re
from collections import defaultdict


def _escaped(text: str, position: int) -> bool:
    end = position
    while position > 0 and text[position - 1] == "\\":
        position -= 1
    return (end - position) % 2 == 1


def _without_code(content: str) -> str:
    # Linear fence scan; unclosed fences extend through EOF. Closing fences may
    # be longer than the opener. Avoid nested regexes on untrusted Markdown.
    lines = []
    fence_char, fence_size = "", 0
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        marker = stripped[:1]
        size = 0
        if indent <= 3 and marker in {"`", "~"}:
            while size < len(stripped) and stripped[size] == marker:
                size += 1
        if fence_char:
            if marker == fence_char and size >= fence_size and not stripped[size:].strip():
                fence_char, fence_size = "", 0
            continue
        if size >= 3 and (marker != "`" or "`" not in stripped[size:]):
            fence_char, fence_size = marker, size
            continue
        if indent >= 4 or line.startswith("\t"):
            continue
        lines.append(line)
    text = "".join(lines)
    # Pair inline delimiters using a precomputed next-equal-run table. Unmatched
    # backticks are literal text; repeatedly searching for a close is quadratic.
    runs = []
    for match in re.finditer(r"`+", text):
        start, end = match.span()
        if _escaped(text, start):
            start += 1
        if end > start:
            runs.append((start, end))
    next_run, latest = {}, {}
    for index in range(len(runs) - 1, -1, -1):
        start, end = runs[index]
        size = end - start
        if size in latest:
            next_run[index] = latest[size]
        latest[size] = index
    parts, cursor, index = [], 0, 0
    while index < len(runs):
        if index in next_run:
            closing = next_run[index]
            parts.append(text[cursor : runs[index][0]])
            parts.append(" ")
            cursor = runs[closing][1]
            index = closing + 1
        else:
            index += 1
    parts.append(text[cursor:])
    return "".join(parts)


def parse_wiki_links(content: str) -> list[str]:
    visible = _without_code(content)
    targets, seen = [], set()
    for match in re.finditer(r"\[\[([^\[\]\n]{1,1024})\]\]", visible):
        if _escaped(visible, match.start()):
            continue
        target = match.group(1).split("|", 1)[0].strip()
        if target and target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


async def reconcile_workspace(db, workspace_id: str) -> None:
    """Caller must hold the workspace row lock; index writes share its transaction."""
    from sqlalchemy import delete, select

    from app.models import Page, PageLink

    pages = list((await db.scalars(select(Page).where(Page.workspace_id == workspace_id))).all())
    old_links = list((await db.scalars(select(PageLink).where(PageLink.workspace_id == workspace_id))).all())
    by_id = {page.id: page for page in pages}
    by_title = defaultdict(list)
    for page in pages:
        if page.deleted_at is None:
            by_title[page.title.strip().casefold()].append(page)
    previous = {(link.source_id, link.raw_target): link.target_id for link in old_links if link.target_id}
    await db.execute(delete(PageLink).where(PageLink.workspace_id == workspace_id))
    # Keep deleted sources' identity bindings so undelete can recover their links.
    for page in pages:
        for raw in parse_wiki_links(page.content):
            target = by_id.get(previous.get((page.id, raw)))
            if target is None:
                if raw.startswith("id:"):
                    target = by_id.get(raw[3:])
                    matches = [target] if target else []
                else:
                    matches = by_title.get(raw.casefold(), [])
                    target = matches[0] if len(matches) == 1 else None
                state = "ambiguous" if len(matches) > 1 else "unresolved"
            else:
                state = "resolved"
            if target:
                state = "deleted" if target.deleted_at else "resolved"
            db.add(
                PageLink(
                    workspace_id=workspace_id,
                    source_id=page.id,
                    target_id=target.id if target else None,
                    raw_target=raw,
                    state=state,
                    source_version=page.version,
                )
            )
    await db.flush()
