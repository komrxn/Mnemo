from __future__ import annotations

from urllib.parse import urlparse

import structlog
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import settings

logger = structlog.get_logger()

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    assert _scheduler is not None, "scheduler not initialized"
    return _scheduler


def create_scheduler() -> AsyncIOScheduler:
    global _scheduler
    url = urlparse(settings.redis_url)
    host = url.hostname or "localhost"
    port = url.port or 6379
    db = int((url.path or "/0").lstrip("/") or "0")

    jobstore = RedisJobStore(host=host, port=port, db=db)
    _scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.tz,
    )
    logger.info("scheduler created", host=host, port=port, db=db)
    return _scheduler
