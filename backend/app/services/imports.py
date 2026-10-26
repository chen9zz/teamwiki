"""Bounded import validation and transactional, idempotent job consumption."""

import io
import stat
import zipfile
from pathlib import PurePosixPath

from fastapi import HTTPException

MAX_UPLOAD = 8 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
MAX_FILES = 200


def _invalid(message):
    raise HTTPException(422, detail={"code": "invalid_import", "message": message})


def _path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or ":" in normalized or "\x00" in normalized:
        _invalid("Unsafe archive path")
    if len(normalized) > 1024 or not path.name or path.suffix.lower() not in {".md", ".markdown"}:
        _invalid("Only Markdown files are supported")
    return path


def _document(name: str, data: bytes) -> dict:
    path = _path(name)
    if len(data) > MAX_FILE:
        _invalid("Markdown file exceeds 2 MiB")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        _invalid("Markdown must be UTF-8")
    if "\x00" in content or len(content) > 1_000_000:
        _invalid("Invalid Markdown content or content too long")
    return {"path": str(path), "title": path.stem[:240], "content": content}


def validate_upload(filename: str, data: bytes) -> list[dict]:
    if not data or len(data) > MAX_UPLOAD:
        _invalid("Upload must contain data and be no larger than 8 MiB")
    if not filename.lower().endswith(".zip"):
        return [_document(filename, data)]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES * 2:
                _invalid("Too many archive entries")
            files = [entry for entry in entries if not entry.is_dir()]
            if not files or len(files) > MAX_FILES:
                _invalid("Archive must contain between 1 and 200 Markdown files")
            if sum(entry.file_size for entry in files) > MAX_EXPANDED:
                _invalid("Expanded archive exceeds 16 MiB")
            paths = set()
            documents = []
            for entry in files:
                path = _path(entry.filename)
                if str(path).casefold() in paths:
                    _invalid("Duplicate archive paths")
                paths.add(str(path).casefold())
                if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    _invalid("Symlinks and encrypted files are not supported")
                if entry.file_size > MAX_FILE or entry.file_size > max(1, entry.compress_size) * 100:
                    _invalid("Unsafe compression ratio or oversized Markdown file")
                with archive.open(entry) as stream:
                    body = stream.read(MAX_FILE + 1)
                documents.append(_document(str(path), body))
            return documents
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
        _invalid("Invalid or unsupported ZIP archive")


async def create_import_job(db, workspace_id: str, user_id: str, documents: list[dict], idempotency_key: str):
    from sqlalchemy import select

    from app.models import ImportJob, OutboxEvent
    from app.services.identity import MANAGERS, audit, lock_workspace, require_member

    if not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(422, "Idempotency-Key must contain between 8 and 128 characters")
    await lock_workspace(db, workspace_id)
    await require_member(db, workspace_id, user_id, MANAGERS)
    existing = await db.scalar(
        select(ImportJob).where(
            ImportJob.workspace_id == workspace_id,
            ImportJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.payload != documents:
            raise HTTPException(409, "This Idempotency-Key was already used for a different upload")
        return existing
    job = ImportJob(
        workspace_id=workspace_id,
        created_by=user_id,
        payload=documents,
        total=len(documents),
        idempotency_key=idempotency_key,
    )
    db.add(job)
    await db.flush()
    db.add(OutboxEvent(workspace_id=workspace_id, kind="import.requested", payload={"job_id": job.id}))
    audit(db, workspace_id, user_id, "import.queued", job.id, {"total": len(documents)})
    await db.flush()
    return job


async def get_import_job(db, workspace_id: str, job_id: str, user_id: str):
    from sqlalchemy import select

    from app.models import ImportJob
    from app.services.identity import require_member

    await require_member(db, workspace_id, user_id)
    job = await db.scalar(
        select(ImportJob).where(
            ImportJob.workspace_id == workspace_id,
            ImportJob.id == job_id,
        )
    )
    if job is None:
        raise HTTPException(404, "Import job not found")
    return job


async def consume_import(db, job_id: str):
    """Consume once in the caller's transaction; failures roll back in full.

    Progress is committed atomically from queued/0 to completed/N. Intermediate
    per-file progress stays inside the transaction and is never a partial import.
    """
    from sqlalchemy import select

    from app.db.base import utcnow
    from app.models import ImportJob, Page
    from app.schemas.pages import PageCreate
    from app.services.identity import MANAGERS, audit, lock_workspace, require_member
    from app.services.links import reconcile_workspace
    from app.services.pages import create_in_transaction

    workspace_id = await db.scalar(select(ImportJob.workspace_id).where(ImportJob.id == job_id))
    if workspace_id is None:
        raise HTTPException(404, "Import job not found")
    # Every operation needing both locks takes the workspace first, avoiding inversions.
    await lock_workspace(db, workspace_id)
    job = await db.scalar(
        select(ImportJob)
        .where(ImportJob.id == job_id, ImportJob.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(404, "Import job not found")
    if job.status in {"completed", "failed"}:
        return job
    await require_member(db, workspace_id, job.created_by, MANAGERS)
    job.status = "running"
    job.attempts += 1
    job.error = None
    job.updated_at = utcnow()
    await db.flush()
    result = []
    for document in job.payload:
        existing = await db.scalar(
            select(Page).where(
                Page.workspace_id == workspace_id,
                Page.import_job_id == job.id,
                Page.import_item_key == document["path"],
            )
        )
        if existing is None:
            page = await create_in_transaction(
                db,
                workspace_id,
                job.created_by,
                PageCreate(title=document["title"], content=document["content"]),
                import_job_id=job.id,
                import_item_key=document["path"],
                index_links=False,
            )
        else:
            page = existing
        result.append(page.id)
        job.processed = len(result)
    await reconcile_workspace(db, workspace_id)
    job.result = result
    job.status = "completed"
    job.updated_at = utcnow()
    audit(db, workspace_id, job.created_by, "import.completed", job.id, {"processed": len(result)})
    await db.flush()
    return job


async def record_import_failure(db, job_id: str, error: Exception, terminal: bool = False):
    """Separate transaction after rollback; a late failure never replaces completion."""
    from sqlalchemy import select

    from app.db.base import utcnow
    from app.models import ImportJob
    from app.services.identity import lock_workspace

    workspace_id = await db.scalar(select(ImportJob.workspace_id).where(ImportJob.id == job_id))
    if workspace_id is None:
        return None
    await lock_workspace(db, workspace_id)
    job = await db.scalar(
        select(ImportJob)
        .where(ImportJob.id == job_id, ImportJob.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status in {"completed", "failed"}:
        return job
    job.attempts += 1
    job.status = "failed" if terminal or job.attempts >= 3 else "queued"
    job.processed = 0
    job.result = []
    job.error = (
        str(error.detail)
        if isinstance(error, HTTPException)
        else f"{type(error).__name__}: import could not be completed"
    )[:1000]
    job.updated_at = utcnow()
    await db.flush()
    return job


async def dispatch_outbox(db, publish_callable, limit: int = 100) -> int:
    """Publish committed events at least once; only broker acknowledgement marks them sent."""
    import inspect
    import logging

    from sqlalchemy import select

    from app.db.base import utcnow
    from app.models import OutboxEvent

    events = list(
        (
            await db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.kind == "import.requested",
                )
                .order_by(OutboxEvent.created_at)
                .limit(max(1, min(limit, 1000)))
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    published = 0
    for event in events:
        event.attempts += 1
        try:
            acknowledgement = publish_callable(event.payload["job_id"], event.id)
            if inspect.isawaitable(acknowledgement):
                await acknowledgement
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Outbox publication failed event=%s error=%s", event.id, type(error).__name__
            )
            continue
        event.published_at = utcnow()
        published += 1
    await db.flush()
    return published


async def recover_stale_jobs(db, stale_after_seconds: int = 300) -> int:
    """Recover lost deliveries using the same workspace-before-job lock order as consumers."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.base import utcnow
    from app.models import ImportJob, OutboxEvent
    from app.services.identity import lock_workspace

    now = utcnow()
    cutoff = now - timedelta(seconds=stale_after_seconds)
    stale = (
        ImportJob.status.in_(["queued", "running"]),
        ImportJob.updated_at < cutoff,
    )
    # Candidate discovery must take no job locks: inserting an outbox event also
    # locks its workspace through the FK. Consumers already lock workspace first.
    candidates = (
        await db.execute(
            select(ImportJob.workspace_id, ImportJob.id)
            .where(*stale)
            .order_by(ImportJob.workspace_id, ImportJob.id)
            .limit(100)
        )
    ).all()
    recovered = 0
    for workspace_id, job_id in candidates:
        try:
            await lock_workspace(db, workspace_id)
        except HTTPException as error:
            if error.status_code == 404:
                continue  # A workspace deleted after discovery no longer needs recovery.
            raise
        job = await db.scalar(
            select(ImportJob)
            .where(ImportJob.workspace_id == workspace_id, ImportJob.id == job_id, *stale)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        # A consumer can finish or refresh the candidate while discovery waits.
        if job is None:
            continue
        pending = await db.scalar(
            select(OutboxEvent.id)
            .where(
                OutboxEvent.workspace_id == job.workspace_id,
                OutboxEvent.kind == "import.requested",
                OutboxEvent.published_at.is_(None),
                OutboxEvent.payload["job_id"].as_string() == job.id,
            )
            .limit(1)
        )
        job.updated_at = now
        if pending is not None:
            continue
        db.add(
            OutboxEvent(workspace_id=job.workspace_id, kind="import.requested", payload={"job_id": job.id})
        )
        job.status = "queued"
        recovered += 1
    await db.flush()
    return recovered
