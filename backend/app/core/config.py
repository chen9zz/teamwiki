from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://teamwiki:teamwiki@localhost:5432/teamwiki"
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=2, gt=0, le=30)
    db_pool_recycle_seconds: int = Field(default=1800, ge=1)
    db_statement_timeout_ms: int = Field(default=5000, ge=1)
    db_lock_timeout_ms: int = Field(default=2000, ge=1)
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = Field(default=20, ge=1, le=200)
    redis_pool_timeout_seconds: float = Field(default=0.1, gt=0, le=0.2)
    redis_socket_timeout_seconds: float = Field(default=0.15, gt=0, le=2)
    request_max_inflight: int = Field(default=64, ge=1, le=10000)
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    jwt_secret: str = "development-only-change-this-32-character-secret"
    jwt_algorithm: str = "HS256"
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    cookie_name: str = "teamwiki_session"
    cookie_secure: bool | None = None
    allowed_origins: list[str] = []
    invitation_ttl_hours: int = 72
    cache_ttl_seconds: int = 300

    @model_validator(mode="after")
    def validate_production_secret(self):
        if self.environment == "production" and (
            len(self.jwt_secret) < 32
            or self.jwt_secret.lower().startswith(("development-only", "replace-", "change-", "example-"))
        ):
            raise ValueError("Production requires a unique JWT_SECRET of at least 32 characters")
        return self

    @property
    def secure_cookie(self) -> bool:
        return self.environment == "production" or bool(self.cookie_secure)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
