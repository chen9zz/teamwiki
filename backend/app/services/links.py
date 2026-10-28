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


def _target_catalog(pages):
    by_id = {page.id: page for page in pages}
    by_title = defaultdict(list)
    for page in pages:
        if page.deleted_at is None:
            by_title[page.title.strip().casefold()].append(page)
    return by_id, by_title


def _source_links(page, old_links, by_id, by_title):
    from app.models import PageLink

    previous = {link.raw_target: link.target_id for link in old_links if link.target_id}
    for raw in parse_wiki_links(page.content):
        target = by_id.get(previous.get(raw))
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
        yield PageLink(
            workspace_id=page.workspace_id,
            source_id=page.id,
            target_id=target.id if target else None,
            raw_target=raw,
            state=state,
            source_version=page.version,
        )


async def preview_revision_links(db, workspace_id, page_id, revision_version, user_id):
    """Resolve a selected snapshot against current tenant metadata without indexing.

    Revisions store Markdown, not historical relationship snapshots. Explicit ID
    links keep their identity; title links use today's catalog and ambiguity rules.
    """
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy import select

    from app.models import Page, PageRevision
    from app.services.identity import require_member

    await require_member(db, workspace_id, user_id)
    source = await db.scalar(
        select(Page.id).where(Page.workspace_id == workspace_id, Page.id == page_id)
    )
    if source is None:
        raise HTTPException(404, "Page not found")
    content = await db.scalar(
        select(PageRevision.content).where(
            PageRevision.workspace_id == workspace_id,
            PageRevision.page_id == page_id,
            PageRevision.version == revision_version,
        )
    )
    if content is None:
        raise HTTPException(404, "Revision not found")
    targets = (
        await db.execute(
            select(Page.id, Page.title, Page.deleted_at).where(Page.workspace_id == workspace_id)
        )
    ).all()
    by_id, by_title = _target_catalog(targets)
    snapshot = SimpleNamespace(
        id=page_id, workspace_id=workspace_id, content=content, version=revision_version
    )
    return {
        "resolution": "current_workspace",
        "outgoing": [
            {"raw_target": link.raw_target, "target_id": link.target_id, "state": link.state}
            for link in _source_links(snapshot, [], by_id, by_title)
        ],
    }


async def reconcile_source(db, page) -> None:
    """Index only the edited source under a shared workspace lock.

    Catalog reads omit other Markdown bodies. Structural writes require an
    exclusive workspace lock, so title/target state remains stable until commit.
    Independent sources never delete or recreate each other's relationship rows.
    """
    from sqlalchemy import delete, select

    from app.models import Page, PageLink

    targets = (
        await db.execute(
            select(Page.id, Page.title, Page.deleted_at).where(
                Page.workspace_id == page.workspace_id,
            )
        )
    ).all()
    by_id, by_title = _target_catalog(targets)
    old_links = list(
        (
            await db.scalars(
                select(PageLink).where(
                    PageLink.workspace_id == page.workspace_id,
                    PageLink.source_id == page.id,
                )
            )
        ).all()
    )
    await db.execute(
        delete(PageLink).where(
            PageLink.workspace_id == page.workspace_id,
            PageLink.source_id == page.id,
        )
    )
    db.add_all(list(_source_links(page, old_links, by_id, by_title)))
    await db.flush()


async def reconcile_workspace(db, workspace_id: str) -> None:
    """Structural/bulk reconciliation only; caller holds exclusive workspace lock."""
    from sqlalchemy import delete, select

    from app.models import Page, PageLink

    pages = list((await db.scalars(select(Page).where(Page.workspace_id == workspace_id))).all())
    old_links = list((await db.scalars(select(PageLink).where(PageLink.workspace_id == workspace_id))).all())
    by_id, by_title = _target_catalog(pages)
    by_source = defaultdict(list)
    for link in old_links:
        by_source[link.source_id].append(link)
    await db.execute(delete(PageLink).where(PageLink.workspace_id == workspace_id))
    # Deleted sources retain their identity bindings for later undelete.
    for page in pages:
        db.add_all(list(_source_links(page, by_source[page.id], by_id, by_title)))
    await db.flush()
