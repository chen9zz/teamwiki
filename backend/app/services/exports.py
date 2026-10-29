"""Bounded exports from one coherent snapshot, with off-loop ZIP compression."""

import asyncio
import io
import json
import re
import threading
import zipfile
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import LargeBinary, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.models import Page, PageLink
from app.services.archive_format import MAX_MANIFEST, MAX_ZIP, validate_pages
from app.services.identity import require_member

EXPORT_CONCURRENCY = 2
_export_admission = threading.BoundedSemaphore(EXPORT_CONCURRENCY)


@asynccontextmanager
async def _snapshot(db):
    """Request authentication may already have started its READ COMMITTED tx.

    A separate PostgreSQL session configures isolation before its first query.
    SQLite is only a test fallback and uses its caller's existing transaction.
    """
    bind = db.bind
    if bind.dialect.name != "postgresql":
        yield db
        return
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    async with AsyncSession(bind=engine.execution_options(isolation_level="REPEATABLE READ")) as snapshot:
        async with snapshot.begin():
            await snapshot.execute(text("SET TRANSACTION READ ONLY"))
            yield snapshot


def _limit(message):
    raise HTTPException(413, message)


async def _collect(db, workspace_id, user_id, format):
    await require_member(db, workspace_id, user_id)
    portable = format == "teamwiki"
    condition = (Page.workspace_id == workspace_id, Page.deleted_at.is_(None))
    byte_length = (
        func.octet_length(Page.content)
        if db.bind.dialect.name == "postgresql"
        else func.length(cast(Page.content, LargeBinary))
    )
    count, size, largest, longest = (
        await db.execute(
            select(
                func.count(Page.id),
                func.coalesce(func.sum(byte_length), 0),
                func.coalesce(func.max(byte_length), 0),
                func.coalesce(func.max(func.length(Page.content)), 0),
            ).where(*condition)
        )
    ).one()
    max_pages, max_bytes = (400, 16 * 1024 * 1024) if portable else (2000, 32 * 1024 * 1024)
    if count > max_pages or size > max_bytes:
        _limit(f"Workspace export is limited to {max_pages} pages or {max_bytes // 1024 // 1024} MiB")
    if portable and (largest > 2 * 1024 * 1024 or longest > 1_000_000):
        _limit("Portable export requires each body to be at most 2 MiB and 1 million characters")
    # The aggregate above rejects excessive work before any Markdown body is sent
    # over the database connection or materialized in Python.
    pages = (
        await db.execute(
            select(
                Page.id,
                Page.parent_id,
                Page.title,
                Page.tags,
                Page.version,
                Page.content,
            )
            .where(*condition)
            .order_by(Page.id)
        )
    ).all()
    if not portable:
        return [
            (
                f"{re.sub(r'[^\w -]', '', page.title)[:80].strip() or 'page'}-{page.id}.md",
                page.content.encode("utf-8"),
            )
            for page in pages
        ]
    identities = {page.id for page in pages}
    if any(page.parent_id is not None and page.parent_id not in identities for page in pages):
        raise HTTPException(409, "Repair missing or deleted page parents before portable export")
    documents = [
        {
            "format": "teamwiki-v1",
            "key": page.id,
            "path": f"pages/{page.id}.md",
            "title": page.title,
            "parent_key": page.parent_id,
            "tags": page.tags,
            "content": page.content,
            "links": [],
        }
        for page in pages
    ]
    by_id = {document["key"]: document for document in documents}
    link_condition = (
        PageLink.workspace_id == workspace_id,
        PageLink.state == "resolved",
        PageLink.source_id.in_(identities),
        PageLink.target_id.in_(identities),
    )
    raw_bytes = (
        func.octet_length(PageLink.raw_target)
        if db.bind.dialect.name == "postgresql"
        else func.length(cast(PageLink.raw_target, LargeBinary))
    )
    link_count, target_bytes = (
        await db.execute(
            select(func.count(PageLink.id), func.coalesce(func.sum(raw_bytes), 0)).where(*link_condition)
        )
    ).one()
    # Every serialized binding needs more than 64 bytes besides its raw target.
    # This lower bound rejects oversized relationship catalogs before reading
    # rows, including workspaces with many deleted historical sources.
    if link_count * 64 + target_bytes > MAX_MANIFEST:
        _limit("Portable relationship metadata exceeds 1 MiB")
    links = (
        await db.execute(
            select(PageLink.source_id, PageLink.target_id, PageLink.raw_target)
            .where(*link_condition)
            .order_by(PageLink.source_id, PageLink.raw_target)
        )
    ).all()
    for link in links:
        if link.source_id in by_id and link.target_id in identities:
            by_id[link.source_id]["links"].append(
                {"raw_target": link.raw_target, "target_key": link.target_id}
            )
    try:
        validate_pages(documents)
    except HTTPException as error:
        raise HTTPException(
            409, "Repair inconsistent page metadata or links before portable export"
        ) from error
    manifest = json.dumps(
        {
            "format": "teamwiki",
            "version": 1,
            "pages": [
                {key: value for key, value in document.items() if key not in {"content", "format"}}
                for document in documents
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(manifest) > MAX_MANIFEST:
        _limit("Portable manifest exceeds 1 MiB")
    return [
        ("teamwiki.json", manifest),
        *[(document["path"], document["content"].encode("utf-8")) for document in documents],
    ]


def _compress(files, portable):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in files:
            archive.writestr(path, body)
            if portable and output.tell() > MAX_ZIP:
                _limit("Portable output ZIP exceeds 8 MiB")
    if portable and output.tell() > MAX_ZIP:
        _limit("Portable output ZIP exceeds 8 MiB")
    return output.getvalue()


async def export_workspace(db, workspace_id, user_id, format="markdown"):
    if format not in {"markdown", "teamwiki"}:
        raise HTTPException(422, "Export format must be markdown or teamwiki")
    await require_member(db, workspace_id, user_id)
    if not _export_admission.acquire(blocking=False):
        raise HTTPException(503, "Workspace export is busy; retry shortly", headers={"Retry-After": "1"})
    release_on_exit = True
    try:
        async with _snapshot(db) as snapshot:
            files = await _collect(snapshot, workspace_id, user_id, format)
        # Cancellation must not release admission while its CPU thread is still
        # running. A done callback releases the slot after compression finishes.
        compression = asyncio.create_task(asyncio.to_thread(_compress, files, format == "teamwiki"))
        try:
            return await asyncio.shield(compression)
        except asyncio.CancelledError:
            release_on_exit = False
            compression.add_done_callback(lambda task: _export_admission.release())
            compression.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            raise
    finally:
        if release_on_exit:
            _export_admission.release()
