from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest

from app.core import cache
from tests.conftest import register, workspace
from tests.test_pages import create_page


@pytest.fixture
async def redis_cache(monkeypatch):
    server = fakeredis.FakeServer()

    @asynccontextmanager
    async def fake_client():
        async with fakeredis.aioredis.FakeRedis(server=server, decode_responses=True) as client:
            yield client

    monkeypatch.setattr(cache, "cache_client", fake_client)
    return fake_client


async def test_cache_namespaces_and_stale_fill_cannot_override_new_version(client, redis_cache):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid)
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    assert (await client.get(url)).json()["content"] == "initial"
    old_key = cache.page_key(wid, page["id"], 1)
    async with redis_cache() as redis:
        assert await redis.get(old_key)
    assert (await client.patch(url, json={"version": 1, "content": "new"})).status_code == 200
    async with redis_cache() as redis:
        assert await redis.get(old_key) is None
    # Simulate delayed pre-update reader filling old cache after invalidation.
    await cache.write(old_key, page)
    assert (await client.get(url)).json()["content"] == "new"
    assert (await client.delete(url + "?version=2")).status_code == 200
    assert (await client.get(url)).status_code == 404


async def test_cache_does_not_bypass_removed_membership(client, second_client, redis_cache):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid)
    member = await register(second_client, "reader@example.com")
    invite = (
        await client.post(
            f"/api/workspaces/{wid}/invitations", json={"email": member["email"], "role": "viewer"}
        )
    ).json()
    await second_client.post("/api/invitations/accept", json={"token": invite["token"]})
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    assert (await second_client.get(url)).status_code == 200
    assert (await client.delete(f"/api/workspaces/{wid}/members/{member['id']}")).status_code == 204
    assert (await second_client.get(url)).status_code == 404


async def test_cache_outage_degrades_to_database(client, monkeypatch):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid)

    @asynccontextmanager
    async def unavailable():
        raise OSError("connection refused")
        yield

    monkeypatch.setattr(cache, "cache_client", unavailable)
    response = await client.get(f"/api/workspaces/{wid}/pages/{page['id']}")
    assert response.status_code == 200
    assert response.json()["content"] == "initial"
