import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

password_hasher = PasswordHasher()
# A dummy hash keeps absent-user password verification on the same expensive path.
_dummy_hash = password_hasher.hash(secrets.token_urlsafe(24))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return password_hasher.verify(password_hash or _dummy_hash, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def session_token(user_id: str, session_id: str, expires_at: datetime) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "jti": session_id,
            "iat": datetime.now(UTC),
            "exp": expires_at,
            "iss": "teamwiki",
            "aud": "teamwiki-browser",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def read_session_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer="teamwiki",
        audience="teamwiki-browser",
        options={"require": ["sub", "jti", "iat", "exp"]},
    )


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)


def aware(value: datetime) -> datetime:
    """SQLite test datetimes omit offsets; production PostgreSQL retains them."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
