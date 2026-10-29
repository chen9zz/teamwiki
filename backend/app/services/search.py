"""Tenant-scoped PostgreSQL search with atomic, bounded application tokenization."""

import asyncio

from fastapi import HTTPException
from sqlalchemy import select, text

from app.models import Page
from app.schemas.pages import PageOut
from app.search_tokenizer_v1 import document_terms, has_chinese, query_terms, warm_tokenizer
from app.services.identity import require_member


async def warm_search_tokenizer():
    await asyncio.to_thread(warm_tokenizer)


async def index_fields(title, content):
    title_tokens, content_tokens, truncated = await asyncio.to_thread(document_terms, title, content)
    return {
        "search_title_tokens": title_tokens,
        "search_content_tokens": content_tokens,
        "search_index_truncated": truncated,
    }


async def search_pages(db, workspace_id, user_id, q, limit=50, offset=0):
    await require_member(db, workspace_id, user_id)
    if not q.strip():
        return []
    if db.bind.dialect.name == "postgresql":
        if has_chinese(q):
            try:
                parsed_query = await asyncio.to_thread(query_terms, q)
            except ValueError as error:
                raise HTTPException(
                    422,
                    detail={"code": "invalid_search_query", "message": str(error)},
                ) from error
            if not parsed_query:
                return []
            # Only literal, application-owned identifiers enter SQL. Query values
            # always use bind parameters and plain terms cannot inject operators.
            vector = "search_tokens_vector"
            query = "plainto_tsquery('simple', :q)"
        else:
            # Keep websearch operators, simple lexemes and original ranking for
            # English searches, including quotation marks and negation.
            parsed_query = q
            vector = "search_vector"
            query = "websearch_to_tsquery('simple', :q)"
        rows = (
            (
                await db.execute(
                    text(f"""
                        SELECT pages.*, ts_rank_cd({vector}, {query}) AS rank
                        FROM pages
                        WHERE workspace_id = :wid AND deleted_at IS NULL
                          AND {vector} @@ {query}
                        ORDER BY rank DESC, updated_at DESC, id
                        LIMIT :limit OFFSET :offset
                    """),
                    {"q": parsed_query, "wid": workspace_id, "limit": limit, "offset": offset},
                )
            )
            .mappings()
            .all()
        )
        return [
            {**PageOut.model_validate(dict(row)).model_dump(mode="json"), "rank": row["rank"]} for row in rows
        ]
    # Isolated SQLite tests only; PostgreSQL remains the supported search runtime.
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
