"""HTTP adapters only: page transaction logic lives in services/pages.py."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.api.deps import DB, CurrentUser
from app.db.base import utcnow
from app.models import AuditLog, Page, PageLink, PageRevision
from app.schemas.pages import (
    PageBatch,
    PageCreate,
    PageOut,
    PageUpdate,
    RevisionLinkPreview,
    RevisionOut,
    VersionInput,
)
from app.services import pages as service
from app.services.exports import export_workspace as build_workspace_export
from app.services.identity import MANAGERS, require_member
from app.services.links import preview_revision_links
from app.services.search import search_pages

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["pages"])


async def metadata_batch(db, workspace_id, limit, offset, *, parent_id=None, tree=False):
    child = aliased(Page)
    has_children = (
        select(child.id)
        .where(
            child.workspace_id == workspace_id,
            child.parent_id == Page.id,
            child.deleted_at.is_(None),
        )
        .exists()
        .label("has_children")
    )
    query = select(
        Page.id,
        Page.workspace_id,
        Page.parent_id,
        Page.title,
        Page.tags,
        Page.version,
        Page.updated_at,
        has_children,
    ).where(Page.workspace_id == workspace_id, Page.deleted_at.is_(None))
    if tree:
        query = query.where(Page.parent_id == parent_id)
    rows = (
        (await db.execute(query.order_by(Page.title, Page.id).offset(offset).limit(limit + 1)))
        .mappings()
        .all()
    )
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@router.get("/page-tree", response_model=PageBatch)
async def page_tree(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    parent_id: str | None = Query(None, max_length=36),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    await require_member(db, workspace_id, user.id)
    if parent_id is not None:
        # Validate only the ID: even opening a branch must not fetch its body.
        parent = await db.scalar(
            select(Page.id).where(
                Page.workspace_id == workspace_id,
                Page.id == parent_id,
                Page.deleted_at.is_(None),
            )
        )
        if parent is None:
            raise HTTPException(404, "Page not found")
    return await metadata_batch(db, workspace_id, limit, offset, parent_id=parent_id, tree=True)


@router.get("/page-summaries", response_model=PageBatch)
async def page_summaries(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    await require_member(db, workspace_id, user.id)
    return await metadata_batch(db, workspace_id, limit, offset)


@router.get("/pages", response_model=list[PageOut])
async def list_pages(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    deleted: bool = False,
    limit: int = Query(5000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    await require_member(db, workspace_id, user.id)
    condition = Page.deleted_at.is_not(None) if deleted else Page.deleted_at.is_(None)
    return (
        await db.scalars(
            select(Page)
            .where(Page.workspace_id == workspace_id, condition)
            .order_by(Page.title, Page.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()


@router.post("/pages", response_model=PageOut, status_code=201)
async def create_page(workspace_id: str, data: PageCreate, db: DB, user: CurrentUser):
    return await service.create_page(db, workspace_id, user.id, data)


@router.get("/search")
async def search(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return await search_pages(db, workspace_id, user.id, q, limit, offset)


@router.get("/graph")
async def graph(workspace_id: str, db: DB, user: CurrentUser):
    await require_member(db, workspace_id, user.id)
    pages = (
        await db.execute(
            select(Page.id, Page.title)
            .where(
                Page.workspace_id == workspace_id,
                Page.deleted_at.is_(None),
            )
            .order_by(Page.id)
        )
    ).all()
    active = {page.id for page in pages}
    links = (
        await db.scalars(
            select(PageLink).where(
                PageLink.workspace_id == workspace_id,
                PageLink.state == "resolved",
            )
        )
    ).all()
    return {
        "nodes": [{"id": page.id, "title": page.title} for page in pages],
        "edges": [
            {"source": link.source_id, "target": link.target_id}
            for link in links
            if link.source_id in active and link.target_id in active
        ],
    }


@router.get("/audit")
async def audit_log(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    await require_member(db, workspace_id, user.id, MANAGERS)
    rows = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.workspace_id == workspace_id)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "entity_id": row.entity_id,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/export")
async def export_workspace(
    workspace_id: str,
    db: DB,
    user: CurrentUser,
    format: Literal["markdown", "teamwiki"] = "markdown",
):
    archive = await build_workspace_export(db, workspace_id, user.id, format)
    suffix = ".teamwiki.zip" if format == "teamwiki" else ".zip"
    return Response(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="teamwiki-{workspace_id}{suffix}"'},
    )


@router.get("/pages/{page_id}", response_model=PageOut)
async def get_page(workspace_id: str, page_id: str, db: DB, user: CurrentUser):
    return await service.get_page(db, workspace_id, page_id, user.id)


@router.patch("/pages/{page_id}", response_model=PageOut)
async def update_page(workspace_id: str, page_id: str, data: PageUpdate, db: DB, user: CurrentUser):
    values = data.model_dump(exclude_unset=True)
    values.pop("version")
    if not values:
        raise HTTPException(422, "Provide at least one field to update")
    return await service.mutate_page(db, workspace_id, page_id, user.id, data.version, values)


@router.delete("/pages/{page_id}", response_model=PageOut)
async def delete_page(workspace_id: str, page_id: str, db: DB, user: CurrentUser, version: int = Query(ge=1)):
    return await service.mutate_page(
        db, workspace_id, page_id, user.id, version, {"deleted_at": utcnow()}, action="page.delete"
    )


@router.post("/pages/{page_id}/undelete", response_model=PageOut)
async def undelete_page(workspace_id: str, page_id: str, data: VersionInput, db: DB, user: CurrentUser):
    return await service.mutate_page(
        db,
        workspace_id,
        page_id,
        user.id,
        data.version,
        {"deleted_at": None},
        action="page.undelete",
        include_deleted=True,
    )


@router.get("/pages/{page_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(
    workspace_id: str,
    page_id: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    await require_member(db, workspace_id, user.id)
    await service.require_page(db, workspace_id, page_id, include_deleted=True)
    return (
        await db.scalars(
            select(PageRevision)
            .where(
                PageRevision.workspace_id == workspace_id,
                PageRevision.page_id == page_id,
            )
            .order_by(PageRevision.version.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()


@router.get("/pages/{page_id}/revisions/{revision_version}", response_model=RevisionOut)
async def revision(workspace_id: str, page_id: str, revision_version: int, db: DB, user: CurrentUser):
    await require_member(db, workspace_id, user.id)
    await service.require_page(db, workspace_id, page_id, include_deleted=True)
    result = await db.scalar(
        select(PageRevision).where(
            PageRevision.workspace_id == workspace_id,
            PageRevision.page_id == page_id,
            PageRevision.version == revision_version,
        )
    )
    if result is None:
        raise HTTPException(404, "Revision not found")
    return result


@router.post("/pages/{page_id}/revisions/{revision_version}/restore", response_model=PageOut)
async def restore_revision(
    workspace_id: str, page_id: str, revision_version: int, data: VersionInput, db: DB, user: CurrentUser
):
    return await service.restore_revision(db, workspace_id, page_id, revision_version, user.id, data.version)


@router.get("/pages/{page_id}/revisions/{revision_version}/links", response_model=RevisionLinkPreview)
async def revision_links(
    workspace_id: str, page_id: str, revision_version: int, db: DB, user: CurrentUser
):
    return await preview_revision_links(db, workspace_id, page_id, revision_version, user.id)


@router.get("/pages/{page_id}/links")
async def links(workspace_id: str, page_id: str, db: DB, user: CurrentUser):
    await require_member(db, workspace_id, user.id)
    await service.require_page(db, workspace_id, page_id, include_deleted=True)
    all_pages = (await db.scalars(select(Page).where(Page.workspace_id == workspace_id))).all()
    by_id = {page.id: page for page in all_pages}
    rows = (
        await db.scalars(
            select(PageLink)
            .where(
                PageLink.workspace_id == workspace_id,
                (PageLink.source_id == page_id) | (PageLink.target_id == page_id),
            )
            .order_by(PageLink.raw_target)
        )
    ).all()

    def output(link):
        target, source = by_id.get(link.target_id), by_id.get(link.source_id)
        return {
            "id": link.id,
            "source_id": link.source_id,
            "target_id": link.target_id,
            "source_title": source.title if source else None,
            "target_title": target.title if target else None,
            "raw_target": link.raw_target,
            "state": link.state,
            "source_version": link.source_version,
        }

    return {
        "outgoing": [output(link) for link in rows if link.source_id == page_id],
        "backlinks": [
            output(link)
            for link in rows
            if link.target_id == page_id
            and link.source_id in by_id
            and by_id[link.source_id].deleted_at is None
        ],
    }


@router.get("/pages/{page_id}/export")
async def export_page(workspace_id: str, page_id: str, db: DB, user: CurrentUser):
    page = await service.get_page(db, workspace_id, page_id, user.id)
    return Response(
        page.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{page_id}.md"'},
    )
