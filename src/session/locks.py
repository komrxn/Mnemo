"""Per-user distributed lock — serializes message processing per user.

Closes Group D (race conditions): without this, lifecycle.scan_idle can close
a session while process_input still works on it; voice+text handlers can race
on session.msgs append; topic-shift detector can fire twice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from redis.asyncio.lock import Lock

from src.session.manager import get_redis

logger = structlog.get_logger()


def _key(user_id: int) -> str:
    return f"user:lock:{user_id}"


@asynccontextmanager
async def user_lock(user_id: int, timeout: float = 60.0) -> AsyncIterator[Lock]:
    """Acquire an exclusive lock for `user_id`. Blocks until acquired or timeout.

    timeout — max time the lock can be held (auto-release safety).
    """
    redis = await get_redis()
    lock = redis.lock(_key(user_id), timeout=timeout, blocking_timeout=timeout)
    acquired = await lock.acquire()
    if not acquired:
        logger.warning("user_lock acquisition timed out", user_id=user_id)
        raise RuntimeError(f"could not acquire user_lock for {user_id}")
    try:
        yield lock
    finally:
        try:
            await lock.release()
        except Exception as exc:
            logger.warning("user_lock release failed", user_id=user_id, error=str(exc))
