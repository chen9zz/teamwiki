import asyncio
from collections.abc import Collection
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    aware,
    hash_password,
    invitation_token,
    session_expiry,
    token_digest,
    verify_password,
)
from app.db.base import utcnow
from app.models import AuditLog, AuthSession, Invitation, User, Workspace, WorkspaceMember

MANAGERS = ("owner", "admin")
WRITERS = ("owner", "admin", "editor")


async def require_member(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    roles: Collection[str] | None = None,
) -> WorkspaceMember:
    member = await db.scalar(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise HTTPException(404, "Workspace not found")
    if roles is not None and member.role not in roles:
        raise HTTPException(403, "You do not have permission for this action")
    return member


async def lock_workspace(db: AsyncSession, workspace_id: str, *, shared: bool = False) -> Workspace:
    """Shared for ordinary saves; exclusive for membership/tree/target-identity changes.

    Holding either lock until transaction end prevents permission revocation and
    structural changes from interleaving with an authorized page mutation.
    """
    workspace = await db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update(read=shared)
    )
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


def audit(
    db: AsyncSession,
    workspace_id: str,
    actor_id: str,
    action: str,
    entity_id: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            entity_id=entity_id,
            details=details or {},
        )
    )


async def register_user(db: AsyncSession, email: str, password: str, name: str) -> User:
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, name=name, password_hash=await asyncio.to_thread(hash_password, password))
    db.add(user)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    valid = await asyncio.to_thread(verify_password, password, user.password_hash if user else None)
    if not valid or user is None:
        raise HTTPException(401, "Invalid email or password")
    return user


async def create_session(db: AsyncSession, user: User) -> AuthSession:
    session = AuthSession(user_id=user.id, expires_at=session_expiry())
    db.add(session)
    await db.flush()
    return session


async def list_workspaces(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                WorkspaceMember.workspace_id == Workspace.id,
            )
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.created_at)
        )
    ).all()
    return [{"id": ws.id, "name": ws.name, "role": role} for ws, role in rows]


async def create_workspace(db: AsyncSession, user_id: str, name: str) -> dict:
    workspace = Workspace(name=name, created_by=user_id)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner"))
    audit(db, workspace.id, user_id, "workspace.created", workspace.id, {"name": name})
    await db.flush()
    return {"id": workspace.id, "name": workspace.name, "role": "owner"}


async def list_members(db: AsyncSession, workspace_id: str, user_id: str) -> list[dict]:
    await require_member(db, workspace_id, user_id)
    rows = (
        await db.execute(
            select(User, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                WorkspaceMember.user_id == User.id,
            )
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(User.name)
        )
    ).all()
    return [{"user_id": user.id, "email": user.email, "name": user.name, "role": role} for user, role in rows]


def check_role_change(actor: WorkspaceMember, target_role: str | None, new_role: str | None) -> None:
    if target_role == "owner" or new_role == "owner":
        raise HTTPException(403, "Workspace ownership cannot be changed")
    if actor.role == "admin" and (target_role == "admin" or new_role == "admin"):
        raise HTTPException(403, "Only the owner can manage administrators")


async def update_member(
    db: AsyncSession, workspace_id: str, actor_id: str, target_id: str, new_role: str | None
) -> dict | None:
    await lock_workspace(db, workspace_id)
    actor = await require_member(db, workspace_id, actor_id, MANAGERS)
    target = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target_id,
        )
    )
    if target is None:
        raise HTTPException(404, "Member not found")
    check_role_change(actor, target.role, new_role)
    previous_role = target.role
    if new_role is None:
        await db.delete(target)
        result = None
    else:
        target.role = new_role
        user = await db.get(User, target_id)
        result = {"user_id": target_id, "email": user.email, "name": user.name, "role": new_role}
    audit(
        db,
        workspace_id,
        actor_id,
        "member.removed" if new_role is None else "member.role_changed",
        target_id,
        {"previous_role": previous_role, "role": new_role},
    )
    await db.flush()
    return result


async def create_invitation(
    db: AsyncSession, workspace_id: str, actor_id: str, email: str, role: str
) -> dict:
    await lock_workspace(db, workspace_id)
    actor = await require_member(db, workspace_id, actor_id, MANAGERS)
    check_role_change(actor, None, role)
    member_exists = await db.scalar(
        select(WorkspaceMember.id)
        .join(
            User,
            User.id == WorkspaceMember.user_id,
        )
        .where(WorkspaceMember.workspace_id == workspace_id, User.email == email)
    )
    if member_exists:
        raise HTTPException(409, "This user is already a member")
    token = invitation_token()
    invitation = Invitation(
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=token_digest(token),
        created_by=actor_id,
        expires_at=utcnow() + timedelta(hours=settings.invitation_ttl_hours),
    )
    db.add(invitation)
    await db.flush()
    audit(db, workspace_id, actor_id, "invitation.created", invitation.id, {"email": email, "role": role})
    return {
        "id": invitation.id,
        "workspace_id": workspace_id,
        "email": email,
        "role": role,
        "expires_at": invitation.expires_at,
        "token": token,
    }


async def accept_invitation(db: AsyncSession, user: User, token: str) -> dict:
    invitation = await db.scalar(select(Invitation).where(Invitation.token_hash == token_digest(token)))
    if invitation is None:
        raise HTTPException(400, "Invitation is invalid or expired")
    workspace = await lock_workspace(db, invitation.workspace_id)
    invitation = await db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invitation.accepted_at is not None or aware(invitation.expires_at) <= utcnow():
        raise HTTPException(400, "Invitation is invalid or expired")
    if invitation.email != user.email:
        raise HTTPException(403, "This invitation belongs to a different email address")
    # Previously issued invitations lose their authority when the issuer is removed or demoted.
    issuer = await require_member(db, workspace.id, invitation.created_by, MANAGERS)
    check_role_change(issuer, None, invitation.role)
    member = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is not None:
        raise HTTPException(409, "You are already a member of this workspace")
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = utcnow()
    audit(db, workspace.id, user.id, "invitation.accepted", invitation.id, {"role": invitation.role})
    await db.flush()
    return {"id": workspace.id, "name": workspace.name, "role": invitation.role}
