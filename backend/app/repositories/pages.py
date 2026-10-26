"""Tenant-scoped persistence; authorization is applied by the service layer."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Page


async def find_page(db: AsyncSession, workspace_id: str, page_id: str, include_deleted=False):
    query = select(Page).where(Page.id == page_id, Page.workspace_id == workspace_id)
    if not include_deleted:
        query = query.where(Page.deleted_at.is_(None))
    return await db.scalar(query.execution_options(populate_existing=True))


async def compare_and_swap(
    db: AsyncSession, workspace_id: str, page_id: str, expected_version: int, values: dict
) -> bool:
    result = await db.execute(
        update(Page)
        .where(
            Page.id == page_id,
            Page.workspace_id == workspace_id,
            Page.version == expected_version,
        )
        .values(**values, version=Page.version + 1)
        .returning(Page.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None
