"""At-least-once Celery delivery with transactional, idempotent import consumption."""

import asyncio
import logging

from celery import Celery
from fastapi import HTTPException

from app.core.cache import close_cache
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.imports import (
    consume_import,
    dispatch_outbox,
    record_import_failure,
    recover_stale_jobs,
)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


async def _close_runtime():
    try:
        await close_cache()
    finally:
        await engine.dispose()


celery_app = Celery("teamwiki", broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    enable_utc=True,
    timezone="UTC",
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    broker_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "visibility_timeout": 600,
        "retry_on_timeout": False,
    },
    redis_socket_connect_timeout=2,
    redis_socket_timeout=2,
    task_publish_retry=False,
    beat_schedule={
        "dispatch-import-outbox": {"task": "teamwiki.dispatch_outbox", "schedule": 5.0},
        "recover-lost-import-delivery": {"task": "teamwiki.recover_imports", "schedule": 60.0},
    },
)


async def _execute(job_id: str):
    try:
        async with SessionLocal.begin() as db:
            job = await consume_import(db, job_id)
            return {"id": job.id, "status": job.status}
    finally:
        # Celery uses a fresh loop per call. Never reuse asyncpg pools across loops.
        await _close_runtime()


async def _record_failure(job_id: str, error: Exception, terminal: bool):
    try:
        async with SessionLocal.begin() as db:
            job = await record_import_failure(db, job_id, error, terminal=terminal)
            return job.status if job is not None else "missing"
    finally:
        await _close_runtime()


@celery_app.task(
    bind=True, name="teamwiki.import_job", max_retries=MAX_ATTEMPTS - 1, soft_time_limit=270, time_limit=300
)
def import_job(self, job_id: str):
    try:
        return asyncio.run(_execute(job_id))
    except Exception as error:
        logger.exception("import_attempt_failed", extra={"job_id": job_id})
        terminal = (
            self.request.retries >= MAX_ATTEMPTS - 1
            or isinstance(error, HTTPException)
            and error.status_code in {403, 404, 422}
        )
        state = asyncio.run(_record_failure(job_id, error, terminal))
        if state in {"completed", "failed", "missing"}:
            return {"id": job_id, "status": state}
        raise self.retry(exc=error, countdown=min(60, 2 ** (self.request.retries + 1)))


async def _publish(job_id: str, event_id: str):
    # Stable task IDs make delivery traceable; the database, not Celery IDs, deduplicates consumption.
    await asyncio.to_thread(
        celery_app.send_task,
        "teamwiki.import_job",
        args=[job_id],
        task_id=event_id,
        retry=False,
        ignore_result=True,
    )


async def _dispatch():
    try:
        async with SessionLocal.begin() as db:
            return await dispatch_outbox(db, _publish)
    finally:
        await _close_runtime()


async def _recover():
    try:
        async with SessionLocal.begin() as db:
            return await recover_stale_jobs(db)
    finally:
        await _close_runtime()


@celery_app.task(name="teamwiki.dispatch_outbox")
def publish_outbox():
    return asyncio.run(_dispatch())


@celery_app.task(name="teamwiki.recover_imports")
def recover_imports():
    return asyncio.run(_recover())
