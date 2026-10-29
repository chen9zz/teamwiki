"""Reversible 0003 backfill, using an explicitly separate disposable database."""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[1]


async def migrate(url, target, direction="upgrade"):
    env = {**os.environ, "DATABASE_URL": url}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        direction,
        target,
        cwd=BACKEND,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    assert process.returncode == 0, stdout.decode()


@pytest.mark.skipif(
    not os.getenv("MIGRATION_TEST_DATABASE_URL"), reason="Needs separate disposable migration DB"
)
async def test_search_migration_backfills_without_losing_pages_and_downgrades():
    url = os.environ["MIGRATION_TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    await migrate(url, "base", "downgrade")
    await migrate(url, "0002_page_tree_order")
    uid, wid, pid = str(uuid4()), str(uuid4()), str(uuid4())
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            INSERT INTO users(id,email,name,password_hash,created_at)
            VALUES (:uid,'migration@example.com','Owner','unused',now())
        """),
            {"uid": uid},
        )
        await connection.execute(
            text("""
            INSERT INTO workspaces(id,name,created_by,created_at) VALUES (:wid,'Archive',:uid,now())
        """),
            {"wid": wid, "uid": uid},
        )
        await connection.execute(
            text("""
            INSERT INTO pages(id,workspace_id,title,content,tags,version,
                              created_at,updated_at,created_by,updated_by)
            VALUES (:pid,:wid,'中文搜索','既有知识支持中文搜索 FastAPI','[]',1,now(),now(),:uid,:uid)
        """),
            {"pid": pid, "wid": wid, "uid": uid},
        )
    await migrate(url, "head")
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
            SELECT title,content,version,search_content_tokens,search_index_truncated,
                   search_tokens_vector @@ plainto_tsquery('simple','中文 搜索 fastapi') AS matched
            FROM pages WHERE id=:pid
        """),
                    {"pid": pid},
                )
            )
            .mappings()
            .one()
        )
        assert row["title"] == "中文搜索" and row["content"] == "既有知识支持中文搜索 FastAPI"
        assert row["version"] == 1 and row["matched"] is True
        assert not row["search_index_truncated"]
        assert "知识" in row["search_content_tokens"].split()
        indexes = (
            (await connection.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='pages'")))
            .scalars()
            .all()
        )
        assert "ix_pages_search_tokens_vector" in indexes
    await migrate(url, "0002_page_tree_order", "downgrade")
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT content FROM pages WHERE id=:pid"), {"pid": pid})
            == "既有知识支持中文搜索 FastAPI"
        )
        assert await connection.scalar(
            text("SELECT search_vector @@ websearch_to_tsquery('simple','FastAPI') FROM pages WHERE id=:pid"),
            {"pid": pid},
        )
        columns = (
            (
                await connection.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name='pages'")
                )
            )
            .scalars()
            .all()
        )
        assert "search_content_tokens" not in columns
    await migrate(url, "head")
    async with engine.connect() as connection:
        assert await connection.scalar(
            text(
                "SELECT search_tokens_vector @@ plainto_tsquery('simple','中文 搜索') "
                "FROM pages WHERE id=:pid"
            ),
            {"pid": pid},
        )
    await engine.dispose()
