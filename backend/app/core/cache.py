"""Immutable version-addressed document cache; the database gates every read.

A delayed reader can refill an old key after eviction. Because readers discover
current version/deletion from PostgreSQL first, that key is never used again.
"""

import asyncio
import json
import logging
import os
import random
import weakref
from contextlib import asynccontextmanager

from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.metrics import CACHE_COMMANDS

logger = logging.getLogger(__name__)
_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def page_key(workspace_id: str, page_id: str, version: int) -> str:
    return f"tw:{workspace_id}:page:{page_id}:v:{version}"


@asynccontextmanager
async def cache_client():
    # Celery forks and creates fresh event loops. Never share a pool across either.
    loop = asyncio.get_running_loop()
    pid = os.getpid()
    entry = _clients.get(loop)
    if entry is None or entry[0] != pid:
        pool = BlockingConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            timeout=settings.redis_pool_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
        entry = (pid, Redis(connection_pool=pool))
        _clients[loop] = entry
    yield entry[1]


async def close_cache():
    """Close this event loop's client before its owner shuts the loop down."""
    entry = _clients.pop(asyncio.get_running_loop(), None)
    if entry is not None and entry[0] == os.getpid():
        await entry[1].aclose()
        await entry[1].connection_pool.aclose()


async def read(key):
    try:
        async with cache_client() as client:
            value = await client.get(key)
        result = json.loads(value) if value else None
        CACHE_COMMANDS.labels("get", "hit" if result is not None else "miss").inc()
        return result
    except (RedisError, OSError, ValueError):
        CACHE_COMMANDS.labels("get", "unavailable").inc()
        logger.debug("cache_read_unavailable")
        return None


async def write(key, value):
    try:
        async with cache_client() as client:
            await client.set(key, json.dumps(value), ex=settings.cache_ttl_seconds + random.randint(0, 30))
        CACHE_COMMANDS.labels("set", "ok").inc()
    except (RedisError, OSError):
        CACHE_COMMANDS.labels("set", "unavailable").inc()
        logger.debug("cache_write_unavailable")


async def invalidate(workspace_id: str, page_id: str, version: int):
    try:
        async with cache_client() as client:
            await client.delete(page_key(workspace_id, page_id, version))
        CACHE_COMMANDS.labels("delete", "ok").inc()
    except (RedisError, OSError):
        CACHE_COMMANDS.labels("delete", "unavailable").inc()
        logger.debug("cache_invalidation_unavailable")


def fill_lock(key):
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
