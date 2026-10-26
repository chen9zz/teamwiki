"""Runtime bounds exercised against real pools, event loops and ASGI requests."""

import asyncio
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.core import cache
from app.core.config import Settings, settings
from app.db import session as database
from app.main import app


@pytest.mark.parametrize(
    "field,value",
    [
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("db_pool_timeout_seconds", 0),
        ("db_statement_timeout_ms", 0),
        ("redis_max_connections", 0),
        ("redis_pool_timeout_seconds", 0.21),
        ("redis_socket_timeout_seconds", 0),
        ("request_max_inflight", 0),
    ],
)
def test_runtime_limits_are_validated(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_production_rejects_copyable_example_secret():
    with pytest.raises(ValidationError):
        Settings(environment="production", jwt_secret="replace-this-with-a-random-unique-production-secret")


async def test_bounded_pool_waits_then_reuses_released_connection(tmp_path):
    # SQLite uses a real bounded queue pool for this test; no database services needed.
    engine = database.build_engine(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
            db_pool_size=1,
            db_max_overflow=0,
            db_pool_timeout_seconds=0.05,
        )
    )
    try:
        async with engine.connect() as first:
            await first.execute(text("SELECT 1"))
            with pytest.raises(PoolTimeout):
                async with engine.connect():
                    pass
            waiting = asyncio.create_task(engine.connect().__aenter__())
            await asyncio.sleep(0.01)
            assert not waiting.done()
        second = await asyncio.wait_for(waiting, 0.2)
        try:
            assert await second.scalar(text("SELECT 1")) == 1
        finally:
            await second.close()
    finally:
        await engine.dispose()


async def test_overload_returns_bounded_retryable_response(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting_db():
        entered.set()
        await release.wait()
        yield None

    monkeypatch.setattr(settings, "request_max_inflight", 1)
    app.dependency_overrides[database.get_db] = waiting_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            pending = asyncio.create_task(client.get("/api/workspaces"))
            await asyncio.wait_for(entered.wait(), 1)
            response = await client.get("/api/workspaces")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "1"
            assert len(response.headers["x-request-id"]) == 36
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get("/api/health")).status_code == 200
            release.set()
            assert (await pending).status_code == 401
            assert (await client.get("/api/workspaces")).status_code == 401
    finally:
        release.set()
        app.dependency_overrides.clear()


async def test_metrics_have_bounded_labels_and_hide_dynamic_paths():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.get("/unknown-private-document-title-123")
        await client.get("/api/health")
        response = await client.get("/internal/metrics")
    assert response.status_code == 200
    assert "teamwiki_http_requests_total" in response.text
    assert 'route="/api/health"' in response.text
    assert "unknown-private-document-title-123" not in response.text
    assert "text/plain" in response.headers["content-type"]


async def test_api_pool_exhaustion_returns_503(tmp_path):
    engine = database.build_engine(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'http.db'}",
            db_pool_size=1,
            db_max_overflow=0,
            db_pool_timeout_seconds=0.03,
        )
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine)

    async def pooled_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[database.get_db] = pooled_db
    try:
        async with engine.connect():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.get("/api/ready")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "1"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.skipif(not (os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")), reason="Requires real Redis")
async def test_cache_reuses_connections_then_rebuilds_after_close(monkeypatch):
    monkeypatch.setattr(
        settings,
        "redis_url",
        os.getenv("TEST_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/15")),
    )
    await cache.close_cache()
    try:
        async with cache.cache_client() as first:
            await first.set("teamwiki-runtime-lifecycle", "one", ex=10)
            connection_id = await first.client_id()
        async with cache.cache_client() as second:
            assert second is first
            assert await second.client_id() == connection_id
            assert await second.get("teamwiki-runtime-lifecycle") == "one"
        await cache.close_cache()
        async with cache.cache_client() as third:
            assert third is not first
            assert await third.client_id() != connection_id
            await third.delete("teamwiki-runtime-lifecycle")
    finally:
        await cache.close_cache()


@pytest.mark.skipif(not (os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")), reason="Requires real Redis")
async def test_cache_exhaustion_has_short_wait_and_recovers(monkeypatch):
    monkeypatch.setattr(
        settings,
        "redis_url",
        os.getenv("TEST_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/15")),
    )
    monkeypatch.setattr(settings, "redis_max_connections", 1)
    monkeypatch.setattr(settings, "redis_pool_timeout_seconds", 0.05)
    await cache.close_cache()
    try:
        async with cache.cache_client() as client:
            held = await client.connection_pool.get_connection()
            start = time.perf_counter()
            assert await cache.read("pool-pressure") is None
            assert 0.03 < time.perf_counter() - start < 0.3
            await client.connection_pool.release(held)
            assert await client.ping()
    finally:
        await cache.close_cache()


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"), reason="Requires real PostgreSQL"
)
async def test_postgres_capacity_deadlines_and_sql_errors_are_distinguished():
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = database.build_engine(
        Settings(
            database_url=os.environ["TEST_DATABASE_URL"],
            db_pool_size=1,
            db_max_overflow=0,
            db_pool_timeout_seconds=0.05,
            db_statement_timeout_ms=40,
            db_lock_timeout_ms=20,
        )
    )
    factory = async_sessionmaker(engine)
    query = "SELECT pg_sleep(0.2)"

    async def query_db():
        async with factory() as db:
            await db.execute(text(query))
            yield db

    try:
        async with engine.connect() as held:
            assert await held.scalar(text("SHOW application_name")) == "teamwiki"
            assert await held.scalar(text("SHOW statement_timeout")) == "40ms"
            assert await held.scalar(text("SHOW lock_timeout")) == "20ms"
            with pytest.raises(PoolTimeout):
                async with engine.connect():
                    pass
        app.dependency_overrides[database.get_db] = query_db
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
        ) as client:
            start = time.perf_counter()
            response = await client.get("/api/workspaces")
            assert response.status_code == 503, response.text
            assert response.headers["retry-after"] == "1"
            assert len(response.headers["x-request-id"]) == 36
            assert time.perf_counter() - start < 0.3
            # SQL errors stay visible as errors instead of misleading clients into retry loops.
            query = "SELECT teamwiki_intentionally_nonexistent_function()"
            assert (await client.get("/api/workspaces")).status_code == 500
            metrics = (await client.get("/internal/metrics")).text
            assert 'route="/workspaces",status="500"' in metrics
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"), reason="Requires real PostgreSQL"
)
async def test_postgres_lock_wait_returns_retryable_503():
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = database.build_engine(
        Settings(
            database_url=os.environ["TEST_DATABASE_URL"],
            db_pool_size=2,
            db_max_overflow=0,
            db_statement_timeout_ms=1000,
            db_lock_timeout_ms=30,
        )
    )
    factory = async_sessionmaker(engine)

    async def locked_db():
        async with factory() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(8753094281)"))
            yield db

    app.dependency_overrides[database.get_db] = locked_db
    try:
        async with engine.begin() as held:
            await held.execute(text("SELECT pg_advisory_xact_lock(8753094281)"))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.get("/api/workspaces")
                assert response.status_code == 503, response.text
                assert response.headers["retry-after"] == "1"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_unresponsive_redis_does_not_retry_or_stall_requests(monkeypatch):
    connected, release = asyncio.Event(), asyncio.Event()

    async def stalled_server(reader, writer):
        connected.set()
        await release.wait()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(stalled_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{port}/0")
    monkeypatch.setattr(settings, "redis_socket_timeout_seconds", 0.03)
    await cache.close_cache()
    try:
        start = time.perf_counter()
        assert await cache.read("unresponsive") is None
        assert connected.is_set()
        assert 0.02 < time.perf_counter() - start < 0.3
    finally:
        release.set()
        await cache.close_cache()
        server.close()
        await server.wait_closed()


@pytest.mark.skipif(not (os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")), reason="Requires real Redis")
async def test_application_lifespan_closes_cache_pool(monkeypatch):
    monkeypatch.setattr(
        settings,
        "redis_url",
        os.getenv("TEST_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/15")),
    )
    await cache.close_cache()
    try:
        async with app.router.lifespan_context(app):
            async with cache.cache_client() as first:
                first_id = await first.client_id()
        async with cache.cache_client() as second:
            assert second is not first
            assert await second.client_id() != first_id
    finally:
        await cache.close_cache()


def test_metrics_aggregate_two_workers_and_drop_dead_live_gauges(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    env = dict(
        os.environ,
        PROMETHEUS_MULTIPROC_DIR=str(tmp_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
    )
    script = """
import sys
from app.core.metrics import HTTP_INFLIGHT, HTTP_REQUESTS
HTTP_REQUESTS.labels('GET', '/api/health', '200').inc(int(sys.argv[1]))
HTTP_INFLIGHT.inc()
print('ready', flush=True)
sys.stdin.readline()
"""
    render = "from app.core.metrics import render_metrics; print(render_metrics()[0].decode())"
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, count],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for count in ("2", "3")
    ]
    try:
        for process in workers:
            assert process.stdout.readline().strip() == "ready"
        output = subprocess.run(
            [sys.executable, "-c", render], env=env, capture_output=True, text=True, check=True, timeout=5
        ).stdout
        assert 'teamwiki_http_requests_total{method="GET",route="/api/health",status="200"} 5.0' in output
        assert "teamwiki_http_inflight 2.0" in output
        assert 'pid="' not in output
        # A killed worker cannot run lifespan cleanup. A later scrape drops its stale live gauge.
        for process in workers:
            process.terminate()
            process.wait(timeout=5)
        output = subprocess.run(
            [sys.executable, "-c", render], env=env, capture_output=True, text=True, check=True, timeout=5
        ).stdout
        assert "teamwiki_http_inflight 0.0" in output
        assert 'teamwiki_http_requests_total{method="GET",route="/api/health",status="200"} 5.0' in output
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("failure", ["refused", "dns"])
async def test_refused_database_connection_returns_retryable_503(failure, monkeypatch):
    import socket

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import session_expiry, session_token

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    host = "127.0.0.1"
    if failure == "dns":
        host = "teamwiki-db-unavailable.invalid"
        resolve = socket.getaddrinfo

        def unavailable_name(name, *args, **kwargs):
            if name == host:
                raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
            return resolve(name, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", unavailable_name)
    engine = database.build_engine(
        Settings(
            database_url=f"postgresql+asyncpg://teamwiki@{host}:{port}/unavailable",
            db_pool_timeout_seconds=0.2,
        )
    )
    factory = async_sessionmaker(engine)

    async def unavailable_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[database.get_db] = unavailable_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
        ) as client:
            token = session_token("missing-user", "missing-session", session_expiry())
            response = await client.get(
                "/api/workspaces",
                headers={"Cookie": f"{settings.cookie_name}={token}"},
            )
        assert response.status_code == 503, response.text
        assert response.headers["retry-after"] == "1"
        assert len(response.headers["x-request-id"]) == 36
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def test_connection_timeout_is_capacity_failure_but_local_file_error_is_not():
    import builtins

    assert database.capacity_failure_reason(builtins.TimeoutError()) == "connect_timeout"
    assert database.capacity_failure_reason(ConnectionRefusedError()) == "unavailable"
    assert database.capacity_failure_reason(FileNotFoundError()) is None
