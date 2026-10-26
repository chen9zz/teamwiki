"""Real request/transaction fixtures; SQLite is only an isolated test fallback."""

import os

os.environ.setdefault("JWT_SECRET", "test-only-secret-32-characters-long")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models as _models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
async def session_factory():
    database_url = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite://")
    options = {"poolclass": StaticPool} if database_url.startswith("sqlite") else {}
    engine = create_async_engine(database_url, **options)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    async def reset_database(create=False):
        async with engine.begin() as connection:
            if database_url.startswith("sqlite"):
                await connection.run_sync(Base.metadata.drop_all)
                if create:
                    await connection.run_sync(Base.metadata.create_all)
            else:
                # CI migrates first; keep PostgreSQL-only generated search columns.
                names = ", ".join(f'"{name}"' for name in Base.metadata.tables)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))

    await reset_database(create=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await reset_database()
    await engine.dispose()


@pytest.fixture
async def client(session_factory):
    async def override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    from app.core.cache import close_cache

    await close_cache()


async def register(client, email="owner@example.com", name="Owner"):
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple",
            "name": name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


async def workspace(client, name="Research"):
    response = await client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def second_client(client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        yield other
