#!/usr/bin/env python3
"""Print the real PostgreSQL plan for tenant-scoped, ranked full-text search."""

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main(args):
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as db:
        if args.force_index:
            await db.execute(text("SET LOCAL enable_seqscan = off"))
        result = await db.execute(
            text("""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT pages.*, ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query)) AS rank
            FROM pages
            WHERE workspace_id = :workspace_id AND deleted_at IS NULL
              AND search_vector @@ websearch_to_tsquery('simple', :query)
            ORDER BY rank DESC, updated_at DESC, id LIMIT 50 OFFSET 0
        """),
            {"workspace_id": args.workspace, "query": args.query},
        )
        print("\n".join(result.scalars()))
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--query", default="TeamWiki")
    parser.add_argument(
        "--force-index",
        action="store_true",
        help="Diagnostic only: show index option even for tiny demo tables",
    )
    asyncio.run(main(parser.parse_args()))
