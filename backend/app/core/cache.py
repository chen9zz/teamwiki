"""Immutable version-addressed document cache; the database gates every read.

A delayed reader can refill an old key after eviction. Because readers discover
current version/deletion from PostgreSQL first, that key is never used again.
"""

import asyncio
import json
import logging
import random
import weakref
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)
_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def page_key(workspace_id: str, page_id: str, version: int) -> str:
    return f"tw:{workspace_id}:page:{page_id}:v:{version}"


@asynccontextmanager
async def cache_client():
    async with Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=0.15, socket_timeout=0.15
    ) as client:
        yield client


async def read(key):
    try:
        async with cache_client() as client:
            value = await client.get(key)
        return json.loads(value) if value else None
    except (RedisError, OSError, ValueError):
        logger.debug("cache_read_unavailable")
        return None


async def write(key, value):
    try:
        async with cache_client() as client:
            await client.set(key, json.dumps(value), ex=settings.cache_ttl_seconds + random.randint(0, 30))
    except (RedisError, OSError):
        logger.debug("cache_write_unavailable")


async def invalidate(workspace_id: str, page_id: str, version: int):
    try:
        async with cache_client() as client:
            await client.delete(page_key(workspace_id, page_id, version))
    except (RedisError, OSError):
        logger.debug("cache_invalidation_unavailable")


def fill_lock(key):
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
