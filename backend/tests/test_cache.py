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


async def test_concurrent_cold_page_reads_fetch_body_once(client, redis_cache, monkeypatch):
    import asyncio

    from app.services import pages

    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid)
    entered, release = asyncio.Event(), asyncio.Event()
    body_fetches = 0
    original = pages.find_page

    async def observe_body_fetch(*args, **kwargs):
        nonlocal body_fetches
        body_fetches += 1
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(pages, "find_page", observe_body_fetch)
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    requests = [asyncio.create_task(client.get(url)) for _ in range(20)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.02)
        release.set()
        responses = await asyncio.wait_for(asyncio.gather(*requests), 3)
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["content"] == "initial" for response in responses)
        assert body_fetches == 1
    finally:
        release.set()
        await asyncio.gather(*requests, return_exceptions=True)


async def test_warm_page_hit_does_not_wait_for_fill_lock(client, redis_cache):
    import asyncio

    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid)
    url = f"/api/workspaces/{wid}/pages/{page['id']}"
    assert (await client.get(url)).status_code == 200
    async with cache.fill_lock(cache.page_key(wid, page["id"], page["version"])):
        response = await asyncio.wait_for(client.get(url), 0.5)
    assert response.status_code == 200
