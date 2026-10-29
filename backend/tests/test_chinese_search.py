"""Search behavior and atomic source indexing (PostgreSQL is the production engine)."""

import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Page
from app.schemas.pages import PageCreate, PageOut
from app.services import pages
from tests.conftest import register, workspace
from tests.test_pages import create_page


def test_chinese_search_normalizes_two_character_and_mixed_terms():
    # A missing/replaced segmenter must lose the independently expected search terms.
    from app.search_tokenizer_v1 import document_terms, query_terms

    title, body, truncated = document_terms("团队知识库", "欢迎使用中文搜索，FastAPI 支持多人协作。")
    assert {"中文", "搜索", "fastapi", "协作"} <= set(body.split())
    assert {"团队", "知识", "知识库"} <= set(title.split())
    assert set(query_terms("中文搜索 ＦａｓｔＡＰＩ").split()) <= set(body.split())
    assert not truncated


def test_search_document_budget_skips_long_lexemes_and_bounds_large_documents():
    from app.search_tokenizer_v1 import document_terms

    title, body, truncated = document_terms("中文搜索", "x" * 3000 + " 中文搜索 " + "正文内容 " * 200_000)
    assert truncated
    assert {"中文", "搜索"} <= set(body.split())
    assert max(len(term.encode("utf-8")) for term in (title + " " + body).split()) <= 128
    assert len(body.split()) <= 8192
    assert len(body.encode("utf-8")) <= 96 * 1024


async def test_index_fields_are_atomic_on_create_save_history_restore_and_preallocated_id(
    client, session_factory
):
    user = await register(client)
    wid = (await workspace(client))["id"]
    predetermined_id = str(uuid4())
    async with session_factory.begin() as db:
        page = await pages.create_in_transaction(
            db, wid, user["id"], PageCreate(title="中文搜索", content="初始知识"), page_id=predetermined_id
        )
        assert page.id == predetermined_id
        assert "知识" in page.search_content_tokens.split()
        assert "搜索" in page.search_title_tokens.split()
        assert "search_content_tokens" not in PageOut.model_validate(page).model_dump()
    async with session_factory.begin() as db:
        saved = await pages.mutate_page(db, wid, predetermined_id, user["id"], 1, {"content": "更新协作"})
        assert "协作" in saved.search_content_tokens.split()
        assert "知识" not in saved.search_content_tokens.split()
    async with session_factory.begin() as db:
        restored = await pages.restore_revision(db, wid, predetermined_id, 1, user["id"], 2)
        assert "知识" in restored.search_content_tokens.split()
        assert "协作" not in restored.search_content_tokens.split()
    with pytest.raises(RuntimeError):
        async with session_factory.begin() as db:
            await pages.mutate_page(db, wid, predetermined_id, user["id"], 3, {"content": "回滚不可搜索"})
            raise RuntimeError("roll back source and tokens together")
    async with session_factory() as db:
        stored = await db.scalar(select(Page).where(Page.id == predetermined_id))
        assert stored.content == "初始知识"
        assert "回滚" not in stored.search_content_tokens.split()


async def test_search_service_checks_membership_and_never_leaks_internal_tokens(
    client, second_client, session_factory
):
    user = await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid, title="知识团队", content="中文搜索 FastAPI")
    outsider = await register(second_client, "search-outsider@example.com")
    from app.services.search import search_pages

    async with session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await search_pages(db, wid, outsider["id"], "中文")
        assert error.value.status_code == 404
        results = await search_pages(db, wid, user["id"], "中文")
        assert [row["id"] for row in results] == [page["id"]]
        assert "rank" in results[0]
        assert "search_content_tokens" not in results[0]
        assert await search_pages(db, wid, user["id"], "   ") == []


PG_ONLY = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="Chinese GIN search requires PostgreSQL",
)


@PG_ONLY
async def test_postgres_chinese_long_phrase_mixed_search_title_weight_and_delete(client, session_factory):
    user = await register(client)
    wid = (await workspace(client))["id"]
    first = await create_page(client, wid, title="中文搜索协作", content="FastAPI 团队知识库")
    second = await create_page(client, wid, title="其他指南", content="团队知识库支持中文搜索协作 FastAPI")
    from app.services.search import search_pages

    async with session_factory() as db:
        results = await search_pages(db, wid, user["id"], "中文搜索")
        assert [r["id"] for r in results] == [first["id"], second["id"]]
        assert results[0]["rank"] > results[1]["rank"]
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "团队知识库支持中文搜索协作")] == [
            second["id"]
        ]
        assert {r["id"] for r in await search_pages(db, wid, user["id"], "中文 FastAPI")} == {
            first["id"],
            second["id"],
        }
    assert (await client.delete(f"/api/workspaces/{wid}/pages/{first['id']}?version=1")).status_code == 200
    async with session_factory() as db:
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "中文")] == [second["id"]]


async def test_tokenization_runs_outside_async_event_loop(monkeypatch):
    import threading

    from app.search_tokenizer_v1 import TOKENIZER
    from app.services.search import index_fields, warm_search_tokenizer

    await warm_search_tokenizer()
    original = TOKENIZER.cut_for_search
    observed_threads = []

    def observed_cut(*args, **kwargs):
        observed_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(TOKENIZER, "cut_for_search", observed_cut)
    fields = await index_fields("团队", "中文搜索 " * 10000)
    assert fields["search_index_truncated"]
    assert observed_threads and all(thread != threading.get_ident() for thread in observed_threads)


@PG_ONLY
async def test_postgres_search_save_restore_import_and_tenant_scope(client, session_factory):
    from app.services.imports import consume_import, create_import_job
    from app.services.search import search_pages

    user = await register(client)
    wid = (await workspace(client))["id"]
    other_wid = (await workspace(client, "Foreign"))["id"]
    page = await create_page(client, wid, title="项目文档", content="初始中文知识")
    await create_page(client, other_wid, title="不可泄漏", content="初始中文知识")
    async with session_factory.begin() as db:
        job = await create_import_job(
            db,
            wid,
            user["id"],
            [{"path": "导入.md", "title": "导入指南", "content": "事务导入知识"}],
            "chinese-search-import-01",
        )
        jid = job.id
    async with session_factory.begin() as db:
        imported = await consume_import(db, jid)
        imported_id = imported.result[0]
    async with session_factory() as db:
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "初始中文知识")] == [page["id"]]
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "事务导入知识")] == [imported_id]
    assert (
        await client.patch(
            f"/api/workspaces/{wid}/pages/{page['id']}", json={"version": 1, "content": "更新多人协作"}
        )
    ).status_code == 200
    async with session_factory() as db:
        assert await search_pages(db, wid, user["id"], "初始中文知识") == []
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "多人协作")] == [page["id"]]
    assert (
        await client.post(
            f"/api/workspaces/{wid}/pages/{page['id']}/revisions/1/restore", json={"version": 2}
        )
    ).status_code == 200
    async with session_factory() as db:
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "初始中文知识")] == [page["id"]]
        assert await search_pages(db, wid, user["id"], "多人协作") == []


@PG_ONLY
async def test_postgres_english_long_document_tail_and_websearch_operators_stay_compatible(
    client, session_factory
):
    from app.services.search import search_pages

    user = await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(
        client,
        wid,
        title="Long manual",
        content="concurrency control " + "ordinary text " * 12000 + "finaltailkeyword",
    )
    await create_page(client, wid, title="Short", content="concurrency eventual consistency")
    async with session_factory() as db:
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "finaltailkeyword")] == [page["id"]]
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "concurrency -eventual")] == [
            page["id"]
        ]
        assert [r["id"] for r in await search_pages(db, wid, user["id"], '"concurrency control"')] == [
            page["id"]
        ]


@PG_ONLY
async def test_postgres_large_chinese_vector_is_bounded_and_source_intact(client, session_factory):
    from sqlalchemy import text

    from app.services.search import search_pages

    user = await register(client)
    wid = (await workspace(client))["id"]
    content = "中文搜索 " * 190000 + "末尾词语"
    page = await create_page(client, wid, title="边界中文", content=content)
    assert page["content"] == content
    async with session_factory() as db:
        row = (
            (
                await db.execute(
                    text("""
            SELECT search_index_truncated, pg_column_size(search_tokens_vector) AS bytes,
                   octet_length(search_content_tokens) AS token_bytes
            FROM pages WHERE id=:id
        """),
                    {"id": page["id"]},
                )
            )
            .mappings()
            .one()
        )
        assert row["search_index_truncated"] and row["bytes"] < 1024 * 1024
        assert row["token_bytes"] <= 96 * 1024
        assert [r["id"] for r in await search_pages(db, wid, user["id"], "中文搜索")] == [page["id"]]
        assert await search_pages(db, wid, user["id"], "末尾词语") == []


@PG_ONLY
async def test_postgres_portable_import_searches_remapped_pages_and_updates(client, session_factory):
    from app.services.exports import export_workspace
    from app.services.imports import consume_import, create_import_job, validate_upload
    from app.services.search import search_pages

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    destination = (await workspace(client, "Destination"))["id"]
    original = await create_page(client, source, title="中文团队", content="可移植归档知识")
    async with session_factory() as db:
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
    documents = validate_upload("portable.zip", archive)
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, destination, user["id"], documents, "chinese-portable-01")).id
    async with session_factory.begin() as db:
        imported_id = (await consume_import(db, jid)).result[0]
        assert imported_id != original["id"]
        assert [r["id"] for r in await search_pages(db, destination, user["id"], "可移植归档知识")] == [
            imported_id
        ]
    async with session_factory.begin() as db:
        await pages.mutate_page(db, destination, imported_id, user["id"], 1, {"content": "导入以后更新"})
        assert await search_pages(db, destination, user["id"], "可移植归档知识") == []
        assert [r["id"] for r in await search_pages(db, destination, user["id"], "以后更新")] == [imported_id]


@PG_ONLY
async def test_postgres_chinese_rank_ties_have_stable_id_pagination(client, session_factory):
    from sqlalchemy import text

    from app.services.search import search_pages

    user = await register(client)
    wid = (await workspace(client))["id"]
    ids = [(await create_page(client, wid, title="中文分页", content="相同内容"))["id"] for _ in range(4)]
    async with session_factory.begin() as db:
        await db.execute(
            text("UPDATE pages SET updated_at='2026-01-01 00:00:00+00' WHERE workspace_id=:wid"), {"wid": wid}
        )
    async with session_factory() as db:
        first = await search_pages(db, wid, user["id"], "中文分页", limit=2)
        second = await search_pages(db, wid, user["id"], "中文分页", limit=2, offset=2)
        assert [r["id"] for r in first + second] == sorted(ids)


@pytest.mark.parametrize("query", ["中文 " + "x" * 129, "中文 " + "Ｆ" * 129])
def test_query_rejects_terms_that_would_be_omitted_from_index(query):
    from app.search_tokenizer_v1 import query_terms

    with pytest.raises(ValueError, match="index limit"):
        query_terms(query)


@PG_ONLY
async def test_postgres_api_rejects_partial_query_instead_of_broadening_results(client, session_factory):
    from app.services.search import search_pages

    user = await register(client)
    wid = (await workspace(client))["id"]
    await create_page(client, wid, title="中文", content="Only the short query term exists here")
    query = "中文 " + "x" * 129
    async with session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await search_pages(db, wid, user["id"], query)
        assert error.value.status_code == 422
        assert error.value.detail["code"] == "invalid_search_query"
    response = await client.get(f"/api/workspaces/{wid}/search", params={"q": query})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_search_query"
