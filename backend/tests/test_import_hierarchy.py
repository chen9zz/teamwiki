"""ZIP hierarchy is created atomically with identities local to one import job."""

import asyncio
import io
import stat
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import AuditLog, ImportJob, OutboxEvent, Page, PageLink, PageRevision
from app.services.imports import consume_import, create_import_job, record_import_failure, validate_upload
from tests.conftest import register, workspace


def test_directory_title_that_becomes_blank_after_truncation_is_rejected_before_queueing():
    directory = " " * 240 + "x"
    with pytest.raises(HTTPException) as error:
        validate_upload("nested.zip", archive([(f"{directory}/Guide.md", "Guide")]))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "invalid_import"


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    return output.getvalue()


async def queue(client, session_factory, entries):
    user = await register(client)
    wid = (await workspace(client))["id"]
    documents = validate_upload("docs.zip", archive(entries))
    async with session_factory.begin() as db:
        job = await create_import_job(db, wid, user["id"], documents, "hierarchy-request-0001")
        jid = job.id
    return wid, user, jid


async def test_nested_zip_creates_shared_editable_directories_and_document_only_progress(
    client, session_factory
):
    wid, user, jid = await queue(
        client,
        session_factory,
        [
            ("Guides/API/Reference.md", "Reference"),
            ("empty/", ""),
            ("Guides.md", "Root document"),
            ("Guides/Introduction.md", "Introduction"),
            ("Guides/API/", ""),
        ],
    )
    preexisting = await client.post(f"/api/workspaces/{wid}/pages", json={"title": "Guides"})
    assert preexisting.status_code == 201
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        pages = {
            page.import_item_key: page
            for page in await db.scalars(select(Page).where(Page.import_job_id == jid))
        }
        assert set(pages) == {
            "dir:Guides",
            "dir:Guides/API",
            "Guides/API/Reference.md",
            "Guides.md",
            "Guides/Introduction.md",
        }
        guides, api = pages["dir:Guides"], pages["dir:Guides/API"]
        assert guides.parent_id is None and guides.title == "Guides" and guides.content == ""
        assert guides.id != preexisting.json()["id"]
        assert api.parent_id == guides.id and api.title == "API" and api.content == ""
        assert pages["Guides/API/Reference.md"].parent_id == api.id
        assert pages["Guides/Introduction.md"].parent_id == guides.id
        assert pages["Guides.md"].parent_id is None
        assert job.total == job.processed == 3
        assert job.result == [
            pages["Guides/API/Reference.md"].id,
            pages["Guides.md"].id,
            pages["Guides/Introduction.md"].id,
        ]
        result = list(job.result)
        directory_id = guides.id
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 6
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    async with session_factory.begin() as db:
        duplicate = await consume_import(db, jid)
        assert duplicate.status == "completed" and duplicate.attempts == 1
        assert duplicate.result == result
        assert await db.scalar(select(func.count()).select_from(Page)) == 6
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 6
    changed = await client.patch(
        f"/api/workspaces/{wid}/pages/{directory_id}",
        json={"version": 1, "content": "Editable directory notes"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["content"] == "Editable directory notes"


async def test_identical_directory_paths_are_not_shared_across_jobs_or_workspaces(
    client, second_client, session_factory
):
    wid, user, first_id = await queue(client, session_factory, [("Docs/Guide.md", "First")])
    outsider = await register(second_client, "outside@example.com", "Outside")
    other_wid = (await workspace(second_client))["id"]
    async with session_factory.begin() as db:
        second = await create_import_job(
            db,
            wid,
            user["id"],
            [{"path": "Docs/Guide.md", "title": "Guide", "content": "Second"}],
            "hierarchy-request-0002",
        )
        other = await create_import_job(
            db,
            other_wid,
            outsider["id"],
            [{"path": "Docs/Guide.md", "title": "Guide", "content": "Other"}],
            "hierarchy-request-0001",
        )
        second_id, other_id = second.id, other.id
    async with session_factory.begin() as db:
        for jid in (first_id, second_id, other_id):
            await consume_import(db, jid)
        pages = list(await db.scalars(select(Page)))
        directories = [page for page in pages if page.import_item_key == "dir:Docs"]
        assert len(directories) == 3
        assert len({page.id for page in directories}) == 3
        for document in (page for page in pages if page.import_item_key == "Docs/Guide.md"):
            parent = next(page for page in directories if page.id == document.parent_id)
            assert parent.workspace_id == document.workspace_id
            assert parent.import_job_id == document.import_job_id
    status = await second_client.get(f"/api/workspaces/{wid}/imports/{first_id}")
    assert status.status_code == 404


async def test_failure_after_directory_creation_rolls_back_pages_revisions_links_and_audit(
    client, session_factory, monkeypatch
):
    from app.services import pages

    wid, user, jid = await queue(client, session_factory, [("Docs/API/Guide.md", "See [[Guide]]")])
    real_create = pages.create_in_transaction

    async def fail_after_document(*args, **kwargs):
        page = await real_create(*args, **kwargs)
        if kwargs.get("import_item_key") == "Docs/API/Guide.md":
            raise RuntimeError("Failure after directories")
        return page

    monkeypatch.setattr(pages, "create_in_transaction", fail_after_document)
    with pytest.raises(RuntimeError, match="Failure after directories"):
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    async with session_factory.begin() as db:
        job = await record_import_failure(db, jid, RuntimeError("Failure after directories"))
        assert job.status == "queued" and job.processed == 0 and job.result == []
        assert await db.scalar(select(func.count()).select_from(Page)) == 0
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 0
        assert await db.scalar(select(func.count()).select_from(PageLink)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "page.create")
            )
            == 0
        )
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    monkeypatch.setattr(pages, "create_in_transaction", real_create)
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        assert job.status == "completed" and job.attempts == 2 and job.processed == 1
        assert await db.scalar(select(func.count()).select_from(Page)) == 3
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 3


async def test_failure_during_final_link_reconciliation_rolls_back_the_whole_hierarchy(
    client, session_factory, monkeypatch
):
    from app.services import links

    wid, user, jid = await queue(
        client, session_factory, [("Docs/Guide.md", "See [[Reference]]"), ("Docs/Reference.md", "Reference")]
    )
    reconcile = links.reconcile_workspace

    async def fail_after_indexing(db, workspace_id):
        await reconcile(db, workspace_id)
        raise RuntimeError("Failure after link indexing")

    monkeypatch.setattr(links, "reconcile_workspace", fail_after_indexing)
    with pytest.raises(RuntimeError, match="Failure after link indexing"):
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    async with session_factory.begin() as db:
        for model in (Page, PageRevision, PageLink):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        job = await db.get(ImportJob, jid)
        assert job.status == "queued" and job.processed == 0 and job.result == []
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1


async def test_parallel_duplicate_delivery_creates_the_hierarchy_once(client, session_factory):
    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking is required")
    wid, user, jid = await queue(client, session_factory, [("Docs/API/Guide.md", "Hello")])

    async def consume():
        async with session_factory.begin() as db:
            job = await consume_import(db, jid)
            return job.status, list(job.result)

    first, duplicate = await asyncio.wait_for(asyncio.gather(consume(), consume()), timeout=10)
    assert first == duplicate and first[0] == "completed" and len(first[1]) == 1
    async with session_factory.begin() as db:
        assert (await db.get(ImportJob, jid)).attempts == 1
        assert await db.scalar(select(func.count()).select_from(Page)) == 3
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 3


async def test_maximum_import_creates_four_hundred_pages_but_reports_two_hundred_documents(
    client, session_factory
):
    wid, user, jid = await queue(
        client, session_factory, [(f"dir-{number}/Guide.md", "Hello") for number in range(200)]
    )
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        assert job.total == job.processed == len(job.result) == 200
        assert await db.scalar(select(func.count()).select_from(Page)) == 400
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 400


async def test_normalized_paths_define_directory_keys_and_parents(client, session_factory):
    wid, user, jid = await queue(client, session_factory, [("./Docs//API\\Guide.md", "Hello")])
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        pages = {page.import_item_key: page for page in await db.scalars(select(Page))}
        assert set(pages) == {"dir:Docs", "dir:Docs/API", "Docs/API/Guide.md"}
        assert pages["dir:Docs/API"].parent_id == pages["dir:Docs"].id
        assert pages["Docs/API/Guide.md"].parent_id == pages["dir:Docs/API"].id
        assert job.result == [pages["Docs/API/Guide.md"].id]


@pytest.mark.parametrize(
    "entries",
    [
        [("Docs/a.md", "a"), ("docs/b.md", "b")],
        [("docs/API/a.md", "a"), ("docs/api/b.md", "b")],
        [("DOCS/", ""), ("docs/a.md", "a")],
        [("../unused/", ""), ("guide.md", "a")],
        [("/unused/", ""), ("guide.md", "a")],
        [("C:/unused/", ""), ("guide.md", "a")],
        [("safe\\..\\unused/", ""), ("guide.md", "a")],
        [("docs/\x01control/a.md", "a")],
        [("docs/   /a.md", "a")],
    ],
)
def test_unsafe_and_case_ambiguous_directory_paths_are_rejected(entries):
    with pytest.raises(HTTPException) as failure:
        validate_upload("docs.zip", archive(entries))
    assert failure.value.status_code == 422


def test_directory_symlinks_are_rejected_even_when_empty():
    directory = zipfile.ZipInfo("linked/")
    directory.create_system = 3
    directory.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(HTTPException) as failure:
        validate_upload("docs.zip", archive([(directory, "target"), ("Guide.md", "Hello")]))
    assert failure.value.status_code == 422


def test_embedded_nul_in_zip_directory_name_cannot_be_hidden_by_zipfile_normalization():
    payload = archive([("safe/xevil/Guide.md", "Hidden"), ("Guide.md", "Hello")])
    payload = payload.replace(b"safe/xevil/Guide.md", b"safe/\x00evil/Guide.md")
    with pytest.raises(HTTPException) as failure:
        validate_upload("docs.zip", payload)
    assert failure.value.status_code == 422


def test_directory_depth_accepts_twenty_levels_and_rejects_twenty_one():
    depth_twenty = "/".join(["level"] * 20) + "/Guide.md"
    documents = validate_upload("docs.zip", archive([(depth_twenty, "Hello")]))
    assert documents[0]["path"] == depth_twenty
    with pytest.raises(HTTPException) as failure:
        validate_upload("docs.zip", archive([("extra/" + depth_twenty, "Hello")]))
    assert failure.value.status_code == 422


def test_directory_and_total_page_limits_accept_four_hundred_pages_and_reject_extra_directory():
    maximum = [(f"dir-{number}/Guide.md", "Hello") for number in range(200)]
    assert len(validate_upload("docs.zip", archive(maximum))) == 200
    # Still 200 Markdown files, but this adds a 201st generated directory.
    maximum[0] = ("outer/dir-0/Guide.md", "Hello")
    with pytest.raises(HTTPException) as failure:
        validate_upload("docs.zip", archive(maximum))
    assert failure.value.status_code == 422


async def test_invalid_legacy_payload_depth_is_rejected_before_creating_any_pages(client, session_factory):
    wid, user, jid = await queue(client, session_factory, [("Guide.md", "Hello")])
    async with session_factory.begin() as db:
        job = await db.get(ImportJob, jid)
        job.payload = [{"path": "/".join(["deep"] * 21) + "/Guide.md", "title": "Guide", "content": "Hello"}]
    with pytest.raises(HTTPException) as failure:
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    assert failure.value.status_code == 422
    async with session_factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Page)) == 0


async def test_old_flat_document_payload_stays_at_root(client, session_factory):
    user = await register(client)
    wid = (await workspace(client))["id"]
    async with session_factory.begin() as db:
        job = await create_import_job(
            db,
            wid,
            user["id"],
            [{"path": "Guide.md", "title": "Custom title", "content": "Legacy"}],
            "legacy-request-0001",
        )
        jid = job.id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        page = await db.scalar(select(Page).where(Page.id == job.result[0]))
        assert page.parent_id is None and page.title == "Custom title" and page.content == "Legacy"
        assert job.total == job.processed == 1
        assert await db.scalar(select(func.count()).select_from(Page)) == 1
