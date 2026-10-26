from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.base import utcnow
from app.models import AuditLog, ImportJob, OutboxEvent, Page, PageLink, PageRevision
from app.services.imports import (
    consume_import,
    create_import_job,
    dispatch_outbox,
    record_import_failure,
    recover_stale_jobs,
)
from tests.conftest import register, workspace

DOCUMENTS = [
    {"path": "Guide.md", "title": "Guide", "content": "See [[Reference]]"},
    {"path": "Reference.md", "title": "Reference", "content": "Reference content"},
]


async def queued(client, session_factory, documents=None):
    user = await register(client)
    wid = (await workspace(client))["id"]
    async with session_factory.begin() as db:
        job = await create_import_job(db, wid, user["id"], documents or DOCUMENTS, "request-key-0001")
        job_id = job.id
    return wid, user, job_id


async def test_import_endpoint_is_idempotent_and_tenant_scoped(client, second_client):
    await register(client)
    wid = (await workspace(client))["id"]
    url = f"/api/workspaces/{wid}/imports"
    files = {"file": ("Guide.md", b"Hello", "text/markdown")}
    headers = {"Idempotency-Key": "browser-request-0001"}
    first = await client.post(url, files=files, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"
    assert "payload" not in first.json()
    repeat = await client.post(url, files=files, headers=headers)
    assert repeat.status_code == 202
    assert repeat.json()["id"] == first.json()["id"]
    changed = await client.post(url, files={"file": ("Guide.md", b"Changed")}, headers=headers)
    assert changed.status_code == 409
    assert (await client.post(url, files=files)).status_code == 422
    await register(second_client, "outsider@example.com", "Outsider")
    status_url = f"{url}/{first.json()['id']}"
    assert (await second_client.get(status_url)).status_code == 404
    assert (await second_client.post(url, files=files, headers=headers)).status_code == 404
    assert (await client.get(status_url)).status_code == 200


async def test_duplicate_consumption_creates_each_page_and_revision_once(client, session_factory):
    wid, user, jid = await queued(client, session_factory)
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        assert job.status == "completed"
        assert job.processed == 2
        result = list(job.result)
    async with session_factory.begin() as db:
        duplicate = await consume_import(db, jid)
        assert duplicate.result == result
        assert duplicate.attempts == 1
        assert await db.scalar(select(func.count()).select_from(Page)) == 2
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 2
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        link = await db.scalar(select(PageLink).where(PageLink.workspace_id == wid))
        assert link.state == "resolved"
        assert link.source_id in result and link.target_id in result
        await record_import_failure(db, jid, RuntimeError("late failure"), terminal=True)
        assert duplicate.status == "completed"
        assert duplicate.error is None


async def test_failed_attempt_rolls_back_all_pages_and_retry_is_atomic(client, session_factory, monkeypatch):
    from app.services import pages

    wid, user, jid = await queued(client, session_factory)
    real_create = pages.create_in_transaction
    calls = 0

    async def failing_creation(*args, **kwargs):
        nonlocal calls
        page = await real_create(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("Injected worker failure")
        return page

    monkeypatch.setattr(pages, "create_in_transaction", failing_creation)
    with pytest.raises(RuntimeError, match="Injected worker failure"):
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    async with session_factory.begin() as db:
        job = await record_import_failure(db, jid, RuntimeError("Injected worker failure"))
        assert job.status == "queued"
        assert job.attempts == 1 and job.error
        assert await db.scalar(select(func.count()).select_from(Page)) == 0
        assert await db.scalar(select(func.count()).select_from(PageRevision)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "page.create")
            )
            == 0
        )
    monkeypatch.setattr(pages, "create_in_transaction", real_create)
    async with session_factory.begin() as db:
        job = await consume_import(db, jid)
        assert job.status == "completed"
        assert job.attempts == 2
        assert job.error is None
        assert len(job.result) == 2


async def test_revoked_importer_cannot_execute_already_queued_work(client, second_client, session_factory):
    await register(client)
    wid = (await workspace(client))["id"]
    invitation = await client.post(
        f"/api/workspaces/{wid}/invitations",
        json={
            "email": "admin@example.com",
            "role": "admin",
        },
    )
    admin = await register(second_client, "admin@example.com", "Admin")
    await second_client.post("/api/invitations/accept", json={"token": invitation.json()["token"]})
    async with session_factory.begin() as db:
        job = await create_import_job(db, wid, admin["id"], DOCUMENTS, "admin-job-0001")
        jid = job.id
    assert (await client.delete(f"/api/workspaces/{wid}/members/{admin['id']}")).status_code == 204
    with pytest.raises(HTTPException) as failure:
        async with session_factory.begin() as db:
            await consume_import(db, jid)
    assert failure.value.status_code == 404
    async with session_factory.begin() as db:
        job = await record_import_failure(db, jid, failure.value, terminal=True)
        assert job.status == "failed"
        assert await db.scalar(select(func.count()).select_from(Page)) == 0


async def test_broker_failure_remains_pending_until_acknowledged_publish(client, session_factory):
    wid, user, jid = await queued(client, session_factory)

    async def unavailable(job_id, event_id):
        raise ConnectionError("Broker unavailable")

    async with session_factory.begin() as db:
        assert await dispatch_outbox(db, unavailable) == 0
    async with session_factory.begin() as db:
        event = await db.scalar(select(OutboxEvent))
        assert event.published_at is None
        assert event.attempts == 1
        assert (await db.get(ImportJob, jid)).status == "queued"

    async def accepted(job_id, event_id):
        # The broker has accepted delivery; consumption is a separate transaction.
        return None

    async with session_factory.begin() as db:
        assert await dispatch_outbox(db, accepted) == 1
    async with session_factory.begin() as db:
        event = await db.scalar(select(OutboxEvent))
        assert event.published_at is not None
        assert event.attempts == 2
        assert await dispatch_outbox(db, accepted) == 0
        await consume_import(db, jid)
        assert await db.scalar(select(func.count()).select_from(Page)) == 2


async def test_recovery_requeues_lost_delivery_without_duplicate_pending_events(client, session_factory):
    wid, user, jid = await queued(client, session_factory)
    async with session_factory.begin() as db:
        event = await db.scalar(select(OutboxEvent))
        event.published_at = utcnow() - timedelta(minutes=10)
        job = await db.get(ImportJob, jid)
        job.updated_at = utcnow() - timedelta(minutes=10)
    async with session_factory.begin() as db:
        assert await recover_stale_jobs(db) == 1
    async with session_factory.begin() as db:
        job = await db.get(ImportJob, jid)
        job.updated_at = utcnow() - timedelta(minutes=10)
    async with session_factory.begin() as db:
        assert await recover_stale_jobs(db) == 0
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        await consume_import(db, jid)
    async with session_factory.begin() as db:
        assert await recover_stale_jobs(db) == 0
        assert await db.scalar(select(func.count()).select_from(Page)) == 2


async def test_recovery_and_consumption_use_consistent_lock_order(client, session_factory, monkeypatch):
    """An outbox FK must never invert recovery's locks against a live consumer."""
    import asyncio

    from sqlalchemy import text

    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking and foreign-key locks are required")
    wid, user, jid = await queued(client, session_factory)
    async with session_factory.begin() as db:
        event = await db.scalar(select(OutboxEvent))
        event.published_at = utcnow() - timedelta(minutes=10)
        job = await db.get(ImportJob, jid)
        job.updated_at = utcnow() - timedelta(minutes=10)

    before_recovery_flush = asyncio.Event()
    allow_recovery_flush = asyncio.Event()
    consumer_started = asyncio.Event()
    backend_pids = {}

    async def recover():
        async with session_factory.begin() as db:
            backend_pids["recovery"] = await db.scalar(text("SELECT pg_backend_pid()"))
            real_flush = db.flush

            async def pause_before_flush(*args, **kwargs):
                before_recovery_flush.set()
                await allow_recovery_flush.wait()
                await real_flush(*args, **kwargs)

            monkeypatch.setattr(db, "flush", pause_before_flush)
            return await recover_stale_jobs(db)

    async def consume():
        async with session_factory.begin() as db:
            backend_pids["consumer"] = await db.scalar(text("SELECT pg_backend_pid()"))
            consumer_started.set()
            return (await consume_import(db, jid)).status

    recovery_task = asyncio.create_task(recover())
    consumer_task = None
    try:
        await asyncio.wait_for(before_recovery_flush.wait(), timeout=5)
        consumer_task = asyncio.create_task(consume())
        await asyncio.wait_for(consumer_started.wait(), timeout=5)
        # Observe an actual PostgreSQL wait, so scheduling cannot hide the inversion.
        async with asyncio.timeout(5):
            async with session_factory() as observer:
                while True:
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pids["consumer"]}
                    )
                    if backend_pids["recovery"] in blockers:
                        break
                    await asyncio.sleep(0.02)
        allow_recovery_flush.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(recovery_task, consumer_task, return_exceptions=True), timeout=10
        )
        assert outcomes == [1, "completed"], [repr(outcome) for outcome in outcomes]
    finally:
        allow_recovery_flush.set()
        for task in (recovery_task, consumer_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (recovery_task, consumer_task) if task), return_exceptions=True
        )

    async with session_factory.begin() as db:
        assert (await db.get(ImportJob, jid)).status == "completed"
        assert await db.scalar(select(func.count()).select_from(Page)) == 2
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 2


async def test_recovery_rechecks_candidate_after_concurrent_completion(client, session_factory):
    import asyncio

    from sqlalchemy import text

    from app.services.identity import lock_workspace

    if session_factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking is required")
    wid, user, jid = await queued(client, session_factory)
    async with session_factory.begin() as db:
        event = await db.scalar(select(OutboxEvent))
        event.published_at = utcnow() - timedelta(minutes=10)
        job = await db.get(ImportJob, jid)
        job.updated_at = utcnow() - timedelta(minutes=10)

    recovery_started = asyncio.Event()
    backend_pids = {}

    async def recover():
        async with session_factory.begin() as db:
            backend_pids["recovery"] = await db.scalar(text("SELECT pg_backend_pid()"))
            recovery_started.set()
            return await recover_stale_jobs(db)

    recovery_task = None
    try:
        async with session_factory.begin() as consumer:
            consumer_pid = await consumer.scalar(text("SELECT pg_backend_pid()"))
            await lock_workspace(consumer, wid)
            recovery_task = asyncio.create_task(recover())
            await asyncio.wait_for(recovery_started.wait(), timeout=5)
            async with asyncio.timeout(5):
                async with session_factory() as observer:
                    while True:
                        blockers = await observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pids["recovery"]}
                        )
                        if consumer_pid in blockers:
                            break
                        await asyncio.sleep(0.02)
            assert (await consume_import(consumer, jid)).status == "completed"
        assert await asyncio.wait_for(recovery_task, timeout=5) == 0
    finally:
        if recovery_task is not None and not recovery_task.done():
            recovery_task.cancel()
        if recovery_task is not None:
            await asyncio.gather(recovery_task, return_exceptions=True)

    async with session_factory.begin() as db:
        assert (await db.get(ImportJob, jid)).status == "completed"
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert await db.scalar(select(func.count()).select_from(Page)) == 2
