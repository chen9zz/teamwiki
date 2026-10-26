from fastapi import APIRouter, Request, Response

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.core.security import session_token
from app.db.base import utcnow
from app.schemas.identity import AuthOutput, Credentials, RegisterInput, UserOutput
from app.services import identity

router = APIRouter(prefix="/auth", tags=["authentication"])


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=AuthOutput, status_code=201)
async def register(payload: RegisterInput, response: Response, db: DB):
    user = await identity.register_user(db, payload.email, payload.password, payload.name)
    session = await identity.create_session(db, user)
    set_session_cookie(response, session_token(user.id, session.id, session.expires_at))
    return {"user": user}


@router.post("/login", response_model=AuthOutput)
async def login(payload: Credentials, response: Response, db: DB):
    user = await identity.authenticate(db, payload.email, payload.password)
    session = await identity.create_session(db, user)
    set_session_cookie(response, session_token(user.id, session.id, session.expires_at))
    return {"user": user}


@router.get("/me", response_model=UserOutput)
async def me(user: CurrentUser):
    return user


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, user: CurrentUser, db: DB):
    request.state.auth_session.revoked_at = utcnow()
    await db.flush()
    response.delete_cookie(
        settings.cookie_name, path="/", httponly=True, secure=settings.secure_cookie, samesite="lax"
    )
