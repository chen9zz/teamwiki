"""HTTP adapters only: page transaction logic lives in services/pages.py."""

import io
import re
import zipfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select, text

from app.api.deps import DB, CurrentUser
from app.db.base import utcnow
from app.models import AuditLog, Page, PageLink, PageRevision
from app.schemas.pages import PageCreate, PageOut, PageUpdate, RevisionOut, VersionInput
from app.services import pages as service
from app.services.identity import MANAGERS, require_member

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["pages"])


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
    await require_member(db, workspace_id, user.id)
    if not q.strip():
        return []
    if db.bind.dialect.name == "postgresql":
        # Literal SQL references the migration-owned generated TSVECTOR column.
        rows = (
            (
                await db.execute(
                    text("""
            SELECT pages.*, ts_rank_cd(search_vector, websearch_to_tsquery('simple', :q)) AS rank
            FROM pages
            WHERE workspace_id = :wid AND deleted_at IS NULL
              AND search_vector @@ websearch_to_tsquery('simple', :q)
            ORDER BY rank DESC, updated_at DESC, id
            LIMIT :limit OFFSET :offset
        """),
                    {"q": q, "wid": workspace_id, "limit": limit, "offset": offset},
                )
            )
            .mappings()
            .all()
        )
        return [
            {**PageOut.model_validate(dict(row)).model_dump(mode="json"), "rank": row["rank"]} for row in rows
        ]
    # Isolated SQLite tests only; PostgreSQL is the supported runtime.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    matches = (
        await db.scalars(
            select(Page)
            .where(
                Page.workspace_id == workspace_id,
                Page.deleted_at.is_(None),
                Page.title.ilike(f"%{escaped}%", escape="\\")
                | Page.content.ilike(f"%{escaped}%", escape="\\"),
            )
            .order_by(Page.updated_at.desc(), Page.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [{**PageOut.model_validate(page).model_dump(mode="json"), "rank": 1.0} for page in matches]


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
async def export_workspace(workspace_id: str, db: DB, user: CurrentUser):
    await require_member(db, workspace_id, user.id)
    pages = (
        await db.scalars(
            select(Page)
            .where(Page.workspace_id == workspace_id, Page.deleted_at.is_(None))
            .order_by(Page.id)
            .limit(2001)
        )
    ).all()
    if len(pages) > 2000 or sum(len(page.content.encode("utf-8")) for page in pages) > 32 * 1024 * 1024:
        raise HTTPException(413, "Workspace export is limited to 2000 pages or 32 MiB")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for page in pages:
            safe_title = re.sub(r"[^\w -]", "", page.title)[:80].strip() or "page"
            archive.writestr(f"{safe_title}-{page.id}.md", page.content)
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="teamwiki-{workspace_id}.zip"',
        },
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
