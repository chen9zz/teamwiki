from fastapi import APIRouter, Response

from app.api.deps import DB, CurrentUser
from app.schemas.identity import (
    InvitationAccept,
    InvitationInput,
    InvitationOutput,
    MemberOutput,
    MemberUpdate,
    WorkspaceInput,
    WorkspaceOutput,
)
from app.services import identity

router = APIRouter(tags=["workspaces"])


@router.get("/workspaces", response_model=list[WorkspaceOutput])
async def list_workspaces(user: CurrentUser, db: DB):
    return await identity.list_workspaces(db, user.id)


@router.post("/workspaces", response_model=WorkspaceOutput, status_code=201)
async def create_workspace(payload: WorkspaceInput, user: CurrentUser, db: DB):
    return await identity.create_workspace(db, user.id, payload.name)


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOutput])
async def list_members(workspace_id: str, user: CurrentUser, db: DB):
    return await identity.list_members(db, workspace_id, user.id)


@router.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=MemberOutput)
async def update_member(workspace_id: str, user_id: str, payload: MemberUpdate, user: CurrentUser, db: DB):
    return await identity.update_member(db, workspace_id, user.id, user_id, payload.role)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(workspace_id: str, user_id: str, user: CurrentUser, db: DB):
    await identity.update_member(db, workspace_id, user.id, user_id, None)
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/invitations", response_model=InvitationOutput, status_code=201)
async def create_invitation(workspace_id: str, payload: InvitationInput, user: CurrentUser, db: DB):
    return await identity.create_invitation(db, workspace_id, user.id, payload.email, payload.role)


@router.post("/invitations/accept", response_model=WorkspaceOutput)
async def accept_invitation(payload: InvitationAccept, user: CurrentUser, db: DB):
    return await identity.accept_invitation(db, user, payload.token)
