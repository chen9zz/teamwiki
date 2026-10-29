"""Portable archives retain identities without trusting archive metadata."""

import asyncio
import io
import json
import threading
import zipfile
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select

from app.models import AuditLog, ImportJob, Page, PageLink, PageRevision
from app.services.imports import consume_import, create_import_job, validate_upload
from tests.conftest import register, workspace
from tests.test_pages import create_page

A, B = str(uuid4()), str(uuid4())


def entry(key, title, content="", *, parent_key=None, tags=None, links=None):
    return {
        "key": key,
        "path": f"pages/{key}.md",
        "title": title,
        "parent_key": parent_key,
        "tags": tags or [],
        "links": links or [],
        "content": content,
    }


def portable(pages=None, *, manifest=None, extra=()):
    if pages is None:
        pages = [
            entry(A, "Parent", "[[Child]]", links=[{"raw_target": "Child", "target_key": B}]),
            entry(B, "Child", "Text", parent_key=A, tags=["中文", "API"]),
        ]
    manifest = (
        manifest
        if manifest is not None
        else {
            "format": "teamwiki",
            "version": 1,
            "pages": [{key: value for key, value in page.items() if key != "content"} for page in pages],
        }
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("teamwiki.json", manifest if isinstance(manifest, str) else json.dumps(manifest))
        for page in pages:
            archive.writestr(page["path"], page["content"])
        for name, content in extra:
            archive.writestr(name, content)
    return output.getvalue()


def test_portable_manifest_produces_compatible_typed_job_documents():
    documents = validate_upload("workspace.zip", portable())
    assert len(documents) == 2
    assert documents[0]["format"] == "teamwiki-v1"
    assert documents[0]["key"] == A and documents[1]["parent_key"] == A
    assert documents[1]["tags"] == ["中文", "API"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(version=True),
        lambda m: m.update(version=2),
        lambda m: m.update(format="unknown"),
        lambda m: m.update(extra=True),
        lambda m: m["pages"][0].update(key=A.upper()),
        lambda m: m["pages"][0].update(path="../secret.md"),
        lambda m: m["pages"][0].update(parent_key=str(uuid4())),
        lambda m: m["pages"][0].update(parent_key=B),
        lambda m: m["pages"][0].update(title=" "),
        lambda m: m["pages"][0].update(title="x" * 241),
        lambda m: m["pages"][0].update(tags=["duplicate", "duplicate"]),
        lambda m: m["pages"][0].update(tags=[" padded "]),
        lambda m: m["pages"][0].update(links=[{"raw_target": "Hidden", "target_key": B}]),
        lambda m: m["pages"][0].update(links=[{"raw_target": "Child", "target_key": str(uuid4())}]),
        lambda m: m["pages"][0].update(links=[{"raw_target": "Child", "target_key": B, "evil": 1}]),
        lambda m: m["pages"].append(deepcopy(m["pages"][0])),
    ],
)
def test_portable_rejects_ambiguous_malformed_or_foreign_metadata(mutation):
    with zipfile.ZipFile(io.BytesIO(portable())) as archive:
        manifest = json.loads(archive.read("teamwiki.json"))
    mutation(manifest)
    with pytest.raises(HTTPException) as error:
        validate_upload("workspace.zip", portable(manifest=manifest))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "invalid_import"


@pytest.mark.parametrize(
    "payload",
    [
        lambda: portable(manifest='{"format":"teamwiki","format":"teamwiki","version":1,"pages":[]}'),
        lambda: portable(extra=[("extra.md", "hidden")]),
        lambda: portable(extra=[("pages/", "")]),
        lambda: portable(extra=[("teamwiki.json", "{}")]),
        lambda: portable(
            [
                entry(A, "Code", "`[[Child]]`", links=[{"raw_target": "Child", "target_key": B}]),
                entry(B, "Child"),
            ]
        ),
        lambda: portable(
            [
                entry(A, "IDs", f"[[id:{B}]]", links=[{"raw_target": f"id:{B}", "target_key": A}]),
                entry(B, "Child"),
            ]
        ),
    ],
)
@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_portable_rejects_duplicate_json_extra_files_and_forged_links(payload):
    with pytest.raises(HTTPException) as error:
        validate_upload("workspace.zip", payload())
    assert error.value.status_code == 422


def test_portable_accepts_four_hundred_pages_and_twenty_levels_but_rejects_next_level():
    keys = [str(uuid4()) for _ in range(400)]
    pages = [
        entry(key, f"Page {index}", parent_key=keys[index - 1] if 0 < index < 20 else None)
        for index, key in enumerate(keys)
    ]
    assert len(validate_upload("workspace.zip", portable(pages))) == 400
    pages[20]["parent_key"] = keys[19]
    with pytest.raises(HTTPException):
        validate_upload("workspace.zip", portable(pages))
    pages[20]["parent_key"] = None
    pages.append(entry(str(uuid4()), "Overflow"))
    with pytest.raises(HTTPException):
        validate_upload("workspace.zip", portable(pages))


async def test_portable_roundtrip_preserves_hierarchy_tags_duplicate_title_and_visible_id_links(
    client, session_factory
):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    target = await create_page(client, source, title="Twin", content="Target")
    source_page = await create_page(client, source, title="Source", content="[[Twin]]")
    await create_page(client, source, title="Twin", content="Other twin")
    content = (
        f"[[Twin]] and [[id:{target['id']}|label]]\n"
        f"`[[id:{target['id']}]]`\n\\[[id:{target['id']}]]\n"
        f"```markdown\n[[id:{target['id']}]]\n```\n"
        f"    [[id:{target['id']}]]\n"
    )
    response = await client.patch(
        f"/api/workspaces/{source}/pages/{source_page['id']}",
        json={"version": 1, "content": content, "parent_id": target["id"], "tags": ["中文", "API"]},
    )
    assert response.status_code == 200
    destination = (await workspace(client, "Destination"))["id"]
    existing = await create_page(client, destination, title="Twin", content="Existing must not capture links")
    async with session_factory() as db:
        exported = await export_workspace(db, source, user["id"], format="teamwiki")
    documents = validate_upload("workspace.zip", exported)
    async with session_factory.begin() as db:
        first = await create_import_job(db, destination, user["id"], documents, "portable-roundtrip-01")
        duplicate = await create_import_job(db, destination, user["id"], documents, "portable-roundtrip-01")
        assert first.id == duplicate.id
        jid = first.id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        result = list(job.result)
        pages = list(await db.scalars(select(Page).where(Page.import_job_id == jid)))
        imported_target = next(page for page in pages if page.content == "Target")
        imported_source = next(page for page in pages if page.title == "Source")
        assert imported_source.parent_id == imported_target.id
        assert imported_source.tags == ["中文", "API"]
        assert f"[[id:{imported_target.id}|label]]" in imported_source.content
        assert imported_source.content.count(f"[[id:{target['id']}]]") == 4
        links = list(await db.scalars(select(PageLink).where(PageLink.source_id == imported_source.id)))
        assert {link.raw_target for link in links} == {"Twin", f"id:{imported_target.id}"}
        assert {link.target_id for link in links} == {imported_target.id}
        assert existing["id"] not in {link.target_id for link in links}
    async with session_factory.begin() as db:
        duplicate = await consume_import(db, jid)
        assert duplicate.result == result and duplicate.attempts == 1
        assert await db.scalar(select(func.count()).select_from(Page).where(Page.import_job_id == jid)) == 3


async def test_portable_final_failure_rolls_back_pages_revisions_links_and_audit(
    client, session_factory, monkeypatch
):
    from app.services import links

    user = await register(client)
    wid = (await workspace(client))["id"]
    documents = validate_upload("portable.zip", portable())
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, wid, user["id"], documents, "portable-rollback-01")).id
    real_reconcile = links.reconcile_workspace

    async def fail_after_links(db, workspace_id):
        await real_reconcile(db, workspace_id)
        raise RuntimeError("after portable links")

    monkeypatch.setattr(links, "reconcile_workspace", fail_after_links)
    with pytest.raises(RuntimeError, match="after portable links"):
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    async with session_factory() as db:
        for model in (Page, PageRevision, PageLink):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "page.create")
            )
            == 0
        )
        job = await db.get(ImportJob, jid)
        assert job.status == "queued" and job.processed == 0 and job.result == []


async def test_export_denies_other_tenant_and_preserves_flat_default(client, second_client, session_factory):
    from app.services.exports import export_workspace

    owner = await register(client)
    wid = (await workspace(client))["id"]
    await create_page(client, wid, title="../../Unsafe", content="content")
    outsider = await register(second_client, "outsider@example.com")
    async with session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await export_workspace(db, wid, outsider["id"], format="teamwiki")
        assert error.value.status_code == 404
    async with session_factory() as db:
        data = await export_workspace(db, wid, owner["id"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.namelist()) == 1 and "/" not in archive.namelist()[0]
        assert archive.read(archive.namelist()[0]) == b"content"


async def test_export_preflight_rejects_before_materializing_bodies(client, session_factory):
    from app.services.exports import export_workspace

    owner = await register(client)
    wid = (await workspace(client))["id"]
    async with session_factory.begin() as db:
        db.add_all(
            [
                Page(
                    workspace_id=wid,
                    title=f"Page {index}",
                    content="",
                    created_by=owner["id"],
                    updated_by=owner["id"],
                )
                for index in range(401)
            ]
        )
    queries = []
    engine = session_factory.kw["bind"]

    def capture(connection, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with session_factory() as db:
            with pytest.raises(HTTPException) as error:
                await export_workspace(db, wid, owner["id"], format="teamwiki")
        assert error.value.status_code == 413
        assert all("sum(" in query.lower() for query in queries if "pages.content" in query)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_export_compression_uses_worker_thread_and_rejects_saturated_admission(
    client, session_factory, monkeypatch
):
    from app.services import exports

    user = await register(client)
    wid = (await workspace(client))["id"]
    await create_page(client, wid, title="Page", content="Text")
    original = zipfile.ZipFile.writestr
    main_thread, threads = threading.get_ident(), []

    def record_thread(self, *args, **kwargs):
        threads.append(threading.get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", record_thread)
    async with session_factory() as db:
        await exports.export_workspace(db, wid, user["id"], format="teamwiki")
    assert threads and all(thread != main_thread for thread in threads)
    for _ in range(exports.EXPORT_CONCURRENCY):
        assert exports._export_admission.acquire(blocking=False)
    try:
        async with session_factory() as db:
            with pytest.raises(HTTPException) as error:
                await exports.export_workspace(db, wid, user["id"], format="teamwiki")
        assert error.value.status_code == 503 and error.value.headers["Retry-After"]
    finally:
        for _ in range(exports.EXPORT_CONCURRENCY):
            exports._export_admission.release()


async def test_postgresql_export_snapshot_stays_coherent_during_concurrent_rename(client, session_factory):
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.exports import export_workspace

    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL REPEATABLE READ snapshot required")
    user = await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid, title="Before", content="Before body")
    preflight, resume = asyncio.Event(), asyncio.Event()
    original_execute = AsyncSession.execute

    async def paused_execute(self, statement, *args, **kwargs):
        result = await original_execute(self, statement, *args, **kwargs)
        if "sum(" in str(statement).lower() and "pages" in str(statement).lower():
            preflight.set()
            await resume.wait()
        return result

    with patch.object(AsyncSession, "execute", paused_execute):
        async with session_factory() as db:
            exporting = asyncio.create_task(export_workspace(db, wid, user["id"], format="teamwiki"))
            await asyncio.wait_for(preflight.wait(), 5)
            changed = await client.patch(
                f"/api/workspaces/{wid}/pages/{page['id']}",
                json={"version": 1, "title": "After", "content": "After body"},
            )
            assert changed.status_code == 200
            resume.set()
            data = await exporting
    documents = validate_upload("snapshot.zip", data)
    assert documents[0]["title"] == "Before" and documents[0]["content"] == "Before body"


async def test_unarchived_explicit_ids_become_consistently_unresolved_even_when_destination_has_that_id(
    client, session_factory
):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    destination = (await workspace(client, "Destination"))["id"]
    existing = await create_page(client, destination, title="Foreign identity", content="Destination")
    raw = existing["id"]
    content = f"[[id:{raw}|alias]] `[[id:{raw}]]`\n\\[[id:{raw}]]\n"
    await create_page(client, source, title="One", content=content)
    await create_page(client, source, title="Two", content=f"[[id:{raw}]]")
    async with session_factory() as db:
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
    documents = validate_upload("portable.zip", archive)
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, destination, user["id"], documents, "external-identity-01")).id
    async with session_factory.begin() as db:
        await consume_import(db, jid)
        imported = list(await db.scalars(select(Page).where(Page.import_job_id == jid)))
        links = list(
            await db.scalars(select(PageLink).where(PageLink.source_id.in_([page.id for page in imported])))
        )
        assert len(links) == 2
        assert len({link.raw_target for link in links}) == 1
        assert links[0].raw_target != f"id:{raw}"
        assert all(link.target_id is None and link.state == "unresolved" for link in links)
        one = next(page for page in imported if page.title == "One")
        assert f"[[{links[0].raw_target}|alias]]" in one.content
        assert f"`[[id:{raw}]]`" in one.content and f"\\[[id:{raw}]]" in one.content


async def test_empty_workspace_portable_archive_can_be_imported(client, session_factory):
    from app.services.exports import export_workspace

    user = await register(client)
    wid = (await workspace(client))["id"]
    async with session_factory() as db:
        archive = await export_workspace(db, wid, user["id"], format="teamwiki")
    documents = validate_upload("empty.zip", archive)
    assert documents == []
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, wid, user["id"], documents, "portable-empty-01")).id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        assert job.status == "completed" and job.total == job.processed == 0


async def test_portable_duplicate_delivery_is_serialized_once_on_postgresql(client, session_factory):
    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking required")
    user = await register(client)
    wid = (await workspace(client))["id"]
    documents = validate_upload("portable.zip", portable())
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, wid, user["id"], documents, "portable-parallel-01")).id

    async def consume():
        async with session_factory.begin() as db:
            job = await consume_import(db, jid)
            return job.attempts, list(job.result)

    first, duplicate = await asyncio.wait_for(asyncio.gather(consume(), consume()), 10)
    assert first == duplicate and first[0] == 1 and len(first[1]) == 2
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Page).where(Page.import_job_id == jid)) == 2
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda documents: documents[0].update(format="markdown"),
        lambda documents: documents[1].update(parent_key=str(uuid4())),
        lambda documents: documents[0].update(links=[{"raw_target": "fake", "target_key": B}]),
        lambda documents: documents[0].update(content="x" * 1_000_001),
    ],
)
async def test_worker_revalidates_persisted_portable_payload_before_writes(client, session_factory, mutate):
    user = await register(client)
    wid = (await workspace(client))["id"]
    documents = validate_upload("portable.zip", portable())
    async with session_factory.begin() as db:
        jid = (await create_import_job(db, wid, user["id"], documents, "portable-tampered-01")).id
    mutate(documents)
    async with session_factory.begin() as db:
        job = await db.get(ImportJob, jid)
        job.payload = documents
    with pytest.raises(HTTPException) as error:
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    assert error.value.status_code == 422
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Page)) == 0


async def test_export_cancel_keeps_admission_until_compression_thread_finishes(
    client, session_factory, monkeypatch
):
    from app.services import exports

    user = await register(client)
    wid = (await workspace(client))["id"]
    started, finish = threading.Event(), threading.Event()
    original = exports._compress
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def blocked_compression(*args):
        started.set()
        finish.wait(5)
        try:
            return original(*args)
        finally:
            loop.call_soon_threadsafe(completed.set)

    monkeypatch.setattr(exports, "_compress", blocked_compression)
    async with session_factory() as db:
        exporting = asyncio.create_task(exports.export_workspace(db, wid, user["id"], format="teamwiki"))
        await asyncio.to_thread(started.wait, 5)
        exporting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await exporting
    assert exports._export_admission.acquire(blocking=False)
    try:
        assert not exports._export_admission.acquire(blocking=False)
    finally:
        exports._export_admission.release()
        finish.set()
    await asyncio.wait_for(completed.wait(), 5)
    # Let the compression Task's completion callback release admission.
    for _ in range(10):
        await asyncio.sleep(0)
    for _ in range(exports.EXPORT_CONCURRENCY):
        assert exports._export_admission.acquire(blocking=False)
    for _ in range(exports.EXPORT_CONCURRENCY):
        exports._export_admission.release()


@pytest.mark.parametrize(
    "pages",
    [
        lambda: [entry(A, "Too many characters", "x" * 1_000_001)],
        lambda: [entry(A, "Too many bytes", "中" * 700_000)],
        lambda: [entry(str(uuid4()), f"Page {i}", "中" * 600_000) for i in range(10)],
    ],
)
def test_portable_expansion_and_per_body_limits_are_enforced(pages):
    with pytest.raises(HTTPException) as error:
        validate_upload("portable.zip", portable(pages()))
    assert error.value.status_code == 422


def test_portable_manifest_size_is_bounded():
    with pytest.raises(HTTPException) as error:
        validate_upload("portable.zip", portable(manifest=" " * (1024 * 1024 + 1)))
    assert error.value.status_code == 422


def test_portable_invalid_deflate_is_rejected_as_client_error():
    payload = bytearray(portable())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("teamwiki.json")
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    payload[offset : offset + 2] = b"\xff\xff"
    with pytest.raises(HTTPException) as error:
        validate_upload("portable.zip", bytes(payload))
    assert error.value.status_code == 422


async def test_portable_export_bounds_relationship_metadata_before_materialization(client, session_factory):
    from app.services.exports import export_workspace

    user = await register(client)
    wid = (await workspace(client))["id"]
    # Many distinct short references are valid Markdown within a single page;
    # their identity metadata must still fit the 1 MiB portable manifest limit.
    content = " ".join(f"[[Target|{number}]] [[Missing{number}]]" for number in range(16000))
    target = await create_page(client, wid, title="Target", content="")
    source = await create_page(client, wid, title="Source", content=content)
    async with session_factory.begin() as db:
        db.add_all(
            [
                PageLink(
                    workspace_id=wid,
                    source_id=source["id"],
                    target_id=target["id"],
                    raw_target=f"Missing{number}",
                    state="resolved",
                    source_version=1,
                )
                for number in range(16000)
            ]
        )
    queries = []
    engine = session_factory.kw["bind"]

    def capture(connection, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with session_factory() as db:
            with pytest.raises(HTTPException) as error:
                await export_workspace(db, wid, user["id"], format="teamwiki")
        assert error.value.status_code == 413
        assert all("sum(" in query.lower() for query in queries if "page_links.raw_target" in query)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_portable_output_zip_limit_releases_export_admission(client, session_factory):
    import base64
    import os

    from app.services import exports

    user = await register(client)
    wid = (await workspace(client))["id"]
    # 12.5 MiB UTF-8 text compresses to about 9.4 MiB: input limits pass,
    # while the complete downloadable ZIP must be rejected at 8 MiB.
    async with session_factory.begin() as db:
        db.add_all(
            [
                Page(
                    workspace_id=wid,
                    title=f"Page {number}",
                    content=base64.b64encode(os.urandom(375_000)).decode(),
                    created_by=user["id"],
                    updated_by=user["id"],
                )
                for number in range(25)
            ]
        )
    async with session_factory() as db:
        with pytest.raises(HTTPException) as error:
            await exports.export_workspace(db, wid, user["id"], format="teamwiki")
    assert error.value.status_code == 413
    for _ in range(exports.EXPORT_CONCURRENCY):
        assert exports._export_admission.acquire(blocking=False)
    for _ in range(exports.EXPORT_CONCURRENCY):
        exports._export_admission.release()


@pytest.mark.parametrize(
    "content",
    [
        lambda raw: f"~~~md\n[[id:{raw}]]\n~~~\n[[ id:{raw} | label ]]",
        lambda raw: f"``[[id:{raw}]]`` [[id:{raw}]]",
        lambda raw: f"\t[[id:{raw}]]\n[[id:{raw}]]",
        lambda raw: f"````md\n[[id:{raw}]]\n`````\n[[id:{raw}]]",
    ],
)
def test_visible_id_rewrite_preserves_fences_inline_indentation_and_whitespace(content):
    from app.services.archive_format import remap_visible_ids

    old, new = str(uuid4()), str(uuid4())
    body = content(old)
    rewritten = remap_visible_ids(body, {old: new})
    assert rewritten.count(old) == 1 and rewritten.count(new) == 1
    assert rewritten.replace(new, old) == body


@pytest.mark.parametrize("variant", [str.upper, lambda value: value.replace("-", "")])
async def test_noncanonical_explicit_id_spelling_stays_unresolved_after_roundtrip(
    client, session_factory, variant
):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    target = await create_page(client, source, title="Target", content="Target")
    raw = variant(target["id"])
    source_page = await create_page(client, source, title="Source", content=f"[[id:{raw}]]")
    async with session_factory() as db:
        original = await db.scalar(select(PageLink).where(PageLink.source_id == source_page["id"]))
        assert original.target_id is None and original.state == "unresolved"
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
    destination = (await workspace(client, "Destination"))["id"]
    async with session_factory.begin() as db:
        job = await create_import_job(
            db, destination, user["id"], validate_upload("portable.zip", archive), "noncanonical-id-01"
        )
        jid = job.id
    async with session_factory.begin() as db:
        await consume_import(db, jid)
        imported = await db.scalar(select(Page).where(Page.import_job_id == jid, Page.title == "Source"))
        link = await db.scalar(select(PageLink).where(PageLink.source_id == imported.id))
        assert link.target_id is None and link.state == "unresolved"
        assert link.raw_target != f"id:{raw}"


@pytest.mark.parametrize("boundary", ["body", "link"])
async def test_compact_uuid_roundtrip_preserves_maximum_body_and_link_lengths(
    client, session_factory, boundary
):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    compact = uuid4().hex
    if boundary == "body":
        content = f"[[id:{compact}]]"
        content += "x" * (1_000_000 - len(content))
    else:
        target = " " * (1024 - len(f"id:{compact}")) + f"id:{compact}"
        content = f"[[{target}]]"
    original = await create_page(client, source, title="Boundary", content=content)
    async with session_factory() as db:
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
        link = await db.scalar(select(PageLink).where(PageLink.source_id == original["id"]))
        assert link.state == "unresolved"
    destination = (await workspace(client, "Destination"))["id"]
    async with session_factory.begin() as db:
        job = await create_import_job(
            db, destination, user["id"], validate_upload("portable.zip", archive), "maximum-id-boundary-01"
        )
        jid = job.id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        page = await db.get(Page, job.result[0])
        assert len(page.content) == len(content)
        assert len(page.content.encode()) == len(content.encode())
        link = await db.scalar(select(PageLink).where(PageLink.source_id == page.id))
        assert link is not None and link.target_id is None and link.state == "unresolved"
        assert link.raw_target != f"id:{compact}"


@pytest.mark.parametrize("separator", ["\r", "\v", "\u2028"])
async def test_id_link_across_non_lf_line_and_code_cannot_capture_destination(
    client, session_factory, separator
):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    destination = (await workspace(client, "Destination"))["id"]
    existing = await create_page(client, destination, title="Existing", content="")
    raw = existing["id"]
    content = f"[[id:{raw}{separator}```md\nexample\n```\n]]"
    original = await create_page(client, source, title="Mixed line endings", content=content)
    async with session_factory() as db:
        before = await db.scalar(select(PageLink).where(PageLink.source_id == original["id"]))
        assert before.state == "unresolved" and before.target_id is None
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
    async with session_factory.begin() as db:
        job = await create_import_job(
            db, destination, user["id"], validate_upload("portable.zip", archive), "mixed-line-endings-01"
        )
        jid = job.id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        page = await db.get(Page, job.result[0])
        link = await db.scalar(select(PageLink).where(PageLink.source_id == page.id))
        assert link.state == "unresolved" and link.target_id is None
        assert link.raw_target != f"id:{raw}"
        assert page.content.replace(link.raw_target[3:], raw) == content


@pytest.mark.parametrize(
    "content",
    [
        "[[id:`example`" + "0" * 31 + "]]",
        "[[id:+" + "0" * 31 + "]]",
        "[[id:0x" + "0" * 30 + "]]",
    ],
)
def test_permissive_integer_uuid_spellings_and_synthetic_code_spaces_stay_literal(content):
    from app.services.archive_format import explicit_uuid_targets, remap_visible_ids

    assert explicit_uuid_targets(content) == set()
    assert remap_visible_ids(content, {}) == content


async def test_multiline_inline_code_uuid_cannot_capture_destination(client, session_factory):
    from app.services.exports import export_workspace

    user = await register(client)
    source = (await workspace(client, "Source"))["id"]
    destination = (await workspace(client, "Destination"))["id"]
    existing = await create_page(client, destination, title="Existing", content="")
    raw = existing["id"]
    content = f"[[id:{raw}`example\ncode`]]"
    await create_page(client, source, title="Inline example", content=content)
    async with session_factory() as db:
        archive = await export_workspace(db, source, user["id"], format="teamwiki")
    async with session_factory.begin() as db:
        job = await create_import_job(
            db, destination, user["id"], validate_upload("portable.zip", archive), "inline-line-endings-01"
        )
        jid = job.id
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        page = await db.get(Page, job.result[0])
        link = await db.scalar(select(PageLink).where(PageLink.source_id == page.id))
        assert link.state == "unresolved" and link.target_id is None
        assert link.raw_target != f"id:{raw}"
        assert page.content.replace(link.raw_target[3:], raw) == content


@pytest.mark.parametrize(
    "spelling",
    [
        lambda value: value.replace("-", ""),
        str.upper,
        lambda value: "{" + value + "}",
        lambda value: "{{" + value.upper() + "}}",
        lambda value: "urn:uuid:" + value,
        lambda value: "uuid:urn:" + value.replace("-", ""),
        lambda value: value.replace("-", "")[:8] + "-" + value.replace("-", "")[8:],
        lambda value: "-" + value.replace("-", "") + "-",
    ],
)
def test_unbound_uuid_remapping_preserves_uuid_length_punctuation_and_case(spelling):
    import re
    from uuid import UUID

    from app.services.archive_format import unbound_uuid_spelling

    original = spelling("abcdefab-cdef-4abc-9def-abcdefabcdef")
    replacement = "fedcbafe-dcba-4fed-8cba-fedcbafedcba"
    migrated = unbound_uuid_spelling(original, replacement)
    assert len(migrated) == len(original) and len(migrated.encode()) == len(original.encode())
    assert re.sub("[a-fA-F0-9]", "H", migrated) == re.sub("[a-fA-F0-9]", "H", original)
    assert UUID(migrated) == UUID(replacement)
    if original.isupper():
        assert migrated.isupper()


@pytest.mark.parametrize(
    "content",
    [
        "[[id:`example`" + "0" * 31 + "]]",
        "[[id:00000000-0000-4000-8000-000000000000`example\ncode`]]",
        "[[id:00000000-0000-4000-8000-000000000000\r```md\nexample\n```\n]]",
        "\\[[id:00000000-0000-4000-8000-000000000000]]",
        "~~~markdown\n[[id:00000000-0000-4000-8000-000000000000]]\n~~~~\ntext",
    ],
)
def test_shared_visible_offsets_match_existing_parser_and_original_characters(content):
    from array import array

    from app.services.links import _without_code

    expected = _without_code(content)
    visible, offsets = _without_code(content, with_offsets=True)
    assert visible == expected and len(offsets) == len(visible)
    assert isinstance(offsets, array) and offsets.itemsize == 4
    assert all(
        character == content[position] if position >= 0 else character == " "
        for character, position in zip(visible, offsets)
    )
