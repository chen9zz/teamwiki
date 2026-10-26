from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://teamwiki:teamwiki@localhost:5432/teamwiki"
    redis_url: str = "redis://localhost:6379/0"
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
            len(self.jwt_secret) < 32 or self.jwt_secret.startswith("development-only")
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
