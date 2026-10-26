from typing import Annotated

from fastapi import APIRouter, File, Header, UploadFile

from app.api.deps import DB, CurrentUser
from app.schemas.pages import JobOut
from app.services.identity import MANAGERS, require_member
from app.services.imports import MAX_UPLOAD, create_import_job, get_import_job, validate_upload

router = APIRouter(tags=["imports"])


@router.post("/workspaces/{workspace_id}/imports", response_model=JobOut, status_code=202)
async def upload_import(
    workspace_id: str,
    user: CurrentUser,
    db: DB,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
):
    await require_member(db, workspace_id, user.id, MANAGERS)
    try:
        documents = validate_upload(file.filename or "import.md", await file.read(MAX_UPLOAD + 1))
    finally:
        await file.close()
    return await create_import_job(db, workspace_id, user.id, documents, idempotency_key)


@router.get("/workspaces/{workspace_id}/imports/{job_id}", response_model=JobOut)
async def import_status(workspace_id: str, job_id: str, user: CurrentUser, db: DB):
    return await get_import_job(db, workspace_id, job_id, user.id)
