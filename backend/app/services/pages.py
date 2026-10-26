"""Page transactions: authorization, optimistic updates, history and link identity."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.db.base import utcnow
from app.models import Page, PageRevision
from app.repositories.pages import compare_and_swap, find_page
from app.schemas.pages import PageCreate, PageOut
from app.services.identity import WRITERS, audit, lock_workspace, require_member
from app.services.links import reconcile_workspace


async def require_page(db, wid, pid, include_deleted=False):
    page = await find_page(db, wid, pid, include_deleted)
    if page is None:
        raise HTTPException(404, "Page not found")
    return page


async def get_page(db, wid, pid, user_id):
    await require_member(db, wid, user_id)
    meta = (
        await db.execute(
            select(Page.version, Page.deleted_at).where(
                Page.workspace_id == wid,
                Page.id == pid,
            )
        )
    ).first()
    if meta is None or meta.deleted_at is not None:
        raise HTTPException(404, "Page not found")
    key = cache.page_key(wid, pid, meta.version)
    async with cache.fill_lock(key):
        value = await cache.read(key)
        if (
            isinstance(value, dict)
            and value.get("workspace_id") == wid
            and value.get("id") == pid
            and value.get("version") == meta.version
        ):
            try:
                return PageOut.model_validate(value)
            except ValueError:
                pass
        page = await require_page(db, wid, pid)
        result = PageOut.model_validate(page)
        # If a writer committed after metadata read, cache under the fetched version.
        await cache.write(cache.page_key(wid, pid, page.version), result.model_dump(mode="json"))
        return result


async def _parent(db, wid, page_id, parent_id):
    current = parent_id
    visited = {page_id} if page_id else set()
    while current:
        if current in visited:
            raise HTTPException(422, "A page cannot be its own ancestor")
        visited.add(current)
        parent = await require_page(db, wid, current, include_deleted=True)
        if parent.deleted_at:
            raise HTTPException(409, "Restore the parent page before using it")
        current = parent.parent_id


def _snapshot(db, page, actor):
    db.add(
        PageRevision(
            workspace_id=page.workspace_id,
            page_id=page.id,
            version=page.version,
            title=page.title,
            content=page.content,
            tags=list(page.tags),
            parent_id=page.parent_id,
            deleted_at=page.deleted_at,
            created_by=actor,
        )
    )


async def create_in_transaction(
    db: AsyncSession,
    wid: str,
    user_id: str,
    data: PageCreate,
    import_job_id=None,
    import_item_key=None,
    index_links=True,
):
    """Caller owns transaction and workspace lock; also used by import worker."""
    await _parent(db, wid, None, data.parent_id)
    page = Page(
        workspace_id=wid,
        created_by=user_id,
        updated_by=user_id,
        **data.model_dump(),
        import_job_id=import_job_id,
        import_item_key=import_item_key,
    )
    db.add(page)
    await db.flush()
    _snapshot(db, page, user_id)
    audit(db, wid, user_id, "page.create", page.id, {"version": 1, "import_job_id": import_job_id})
    if index_links:
        await reconcile_workspace(db, wid)
    return page


async def create_page(db, wid, user_id, data):
    await lock_workspace(db, wid)
    await require_member(db, wid, user_id, WRITERS)
    return await create_in_transaction(db, wid, user_id, data)


async def mutate_page(db, wid, pid, user_id, version, values, action="page.update", include_deleted=False):
    await lock_workspace(db, wid)
    await require_member(db, wid, user_id, WRITERS)
    page = await require_page(db, wid, pid, include_deleted)
    if "parent_id" in values:
        await _parent(db, wid, pid, values["parent_id"])
    if values.get("deleted_at"):
        child = await db.scalar(
            select(Page.id)
            .where(Page.workspace_id == wid, Page.parent_id == pid, Page.deleted_at.is_(None))
            .limit(1)
        )
        if child:
            raise HTTPException(409, "Move or delete child pages first")
    if values.get("deleted_at", "unchanged") is None:
        await _parent(db, wid, pid, values.get("parent_id", page.parent_id))
    changed = await compare_and_swap(
        db,
        wid,
        pid,
        version,
        {
            **values,
            "updated_at": utcnow(),
            "updated_by": user_id,
        },
    )
    if not changed:
        current = await require_page(db, wid, pid, include_deleted=True)
        raise HTTPException(
            409,
            detail={
                "code": "version_conflict",
                "current": PageOut.model_validate(current).model_dump(mode="json"),
                "submitted_version": version,
            },
        )
    page = await require_page(db, wid, pid, include_deleted=True)
    _snapshot(db, page, user_id)
    audit(db, wid, user_id, action, pid, {"version": page.version})
    await reconcile_workspace(db, wid)
    # Version namespacing guarantees correctness even if eviction runs before commit.
    await cache.invalidate(wid, pid, version)
    return page


async def restore_revision(db, wid, pid, revision_version, user_id, current_version):
    # Keep revision lookup and restore in the same tenant lock as every mutation.
    await lock_workspace(db, wid)
    await require_member(db, wid, user_id, WRITERS)
    await require_page(db, wid, pid, include_deleted=True)
    revision = await db.scalar(
        select(PageRevision).where(
            PageRevision.workspace_id == wid,
            PageRevision.page_id == pid,
            PageRevision.version == revision_version,
        )
    )
    if revision is None:
        raise HTTPException(404, "Revision not found")
    return await mutate_page(
        db,
        wid,
        pid,
        user_id,
        current_version,
        {
            "title": revision.title,
            "content": revision.content,
            "tags": list(revision.tags),
            "parent_id": revision.parent_id,
            "deleted_at": revision.deleted_at,
        },
        action="page.restore_revision",
        include_deleted=True,
    )
