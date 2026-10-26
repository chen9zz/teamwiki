from typing import Annotated

from fastapi import Depends, HTTPException, Request
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import aware, read_session_token
from app.db.base import utcnow
from app.db.session import get_db
from app.models import AuthSession, User

DB = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def get_current_user(request: Request, db: DB) -> User:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(401, "Authentication required")
    try:
        claims = read_session_token(token)
    except InvalidTokenError:
        raise HTTPException(401, "Session expired or invalid") from None
    session = await db.get(AuthSession, claims["jti"])
    if (
        session is None
        or session.user_id != claims["sub"]
        or session.revoked_at is not None
        or aware(session.expires_at) <= utcnow()
    ):
        raise HTTPException(401, "Session expired or invalid")
    user = await db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Session expired or invalid")
    request.state.auth_session = session
    return user


CurrentUser = Annotated[User, Depends(get_current_user, scope="function")]
