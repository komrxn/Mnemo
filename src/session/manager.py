from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import orjson
import structlog
from pydantic import BaseModel
from redis.asyncio import Redis

from src.config import settings

logger = structlog.get_logger()

# ── key helpers (never inline these strings elsewhere) ────────────────────────

def key_active(user_id: int) -> str:
    return f"session:active:{user_id}"

def key_msgs(session_id: str) -> str:
    return f"session:msgs:{session_id}"

def key_summary(session_id: str) -> str:
    return f"session:summary:{session_id}"

def key_scratch(session_id: str) -> str:
    return f"session:scratch:{session_id}"

def key_profile(user_id: int) -> str:
    return f"user:profile:{user_id}"

def key_cron_overrides(user_id: int) -> str:
    return f"user:cron_overrides:{user_id}"

def key_onboarding(user_id: int) -> str:
    return f"user:onboarding:{user_id}"


# ── TTLs ──────────────────────────────────────────────────────────────────────

_ACTIVE_TTL = 24 * 3600   # 24 h sliding
_MSGS_TTL = 7 * 24 * 3600  # 7 days

# ── models ────────────────────────────────────────────────────────────────────

class ActiveSession(BaseModel):
    session_id: str
    started_at: datetime
    last_msg_at: datetime
    topic: str = ""


class SessionMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    ts: datetime
    kind: Literal["text", "voice", "image"] = "text"
    meta: dict[str, str] = {}


# ── singleton connection ───────────────────────────────────────────────────────

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=False)
    return _redis


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_session_id() -> str:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.tz)
    return f"ses_{datetime.now(tz).strftime('%Y-%m-%d_%H-%M-%S')}"


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def get_active(redis: Redis, user_id: int) -> ActiveSession | None:
    raw = await redis.get(key_active(user_id))
    if raw is None:
        return None
    return ActiveSession.model_validate(orjson.loads(raw))


async def create_session(redis: Redis, user_id: int) -> ActiveSession:
    session = ActiveSession(
        session_id=_make_session_id(),
        started_at=_now(),
        last_msg_at=_now(),
    )
    await redis.set(
        key_active(user_id),
        orjson.dumps(session.model_dump(mode="json")),
        ex=_ACTIVE_TTL,
    )
    logger.info("session opened", session_id=session.session_id, user_id=user_id)
    return session


async def get_or_create(redis: Redis, user_id: int) -> ActiveSession:
    active = await get_active(redis, user_id)
    if active is None:
        active = await create_session(redis, user_id)
    return active


async def touch(redis: Redis, user_id: int, session: ActiveSession, topic: str = "") -> None:
    """Refresh sliding TTL and update last_msg_at."""
    updated = session.model_copy(
        update={"last_msg_at": _now(), "topic": topic or session.topic}
    )
    await redis.set(
        key_active(user_id),
        orjson.dumps(updated.model_dump(mode="json")),
        ex=_ACTIVE_TTL,
    )


async def close_session(redis: Redis, user_id: int) -> ActiveSession | None:
    session = await get_active(redis, user_id)
    if session is None:
        return None
    await redis.delete(key_active(user_id))
    logger.info("session closed", session_id=session.session_id, user_id=user_id)
    return session


async def push_msg(redis: Redis, session_id: str, msg: SessionMessage) -> None:
    await redis.rpush(key_msgs(session_id), orjson.dumps(msg.model_dump(mode="json")))
    await redis.expire(key_msgs(session_id), _MSGS_TTL)


async def get_msgs(redis: Redis, session_id: str) -> list[SessionMessage]:
    raws = await redis.lrange(key_msgs(session_id), 0, -1)
    return [SessionMessage.model_validate(orjson.loads(r)) for r in raws]


async def get_profile(redis: Redis, user_id: int) -> dict[str, object]:
    raw = await redis.get(key_profile(user_id))
    return orjson.loads(raw) if raw else {}


async def update_profile(redis: Redis, user_id: int, patch: dict[str, object]) -> None:
    profile = await get_profile(redis, user_id)
    profile.update(patch)
    await redis.set(key_profile(user_id), orjson.dumps(profile))


async def scan_idle(redis: Redis, timeout_min: int) -> list[tuple[int, ActiveSession]]:
    """Find sessions idle longer than timeout_min minutes."""
    now = _now()
    idle = []
    async for key in redis.scan_iter("session:active:*"):
        raw = await redis.get(key)
        if raw is None:
            continue
        session = ActiveSession.model_validate(orjson.loads(raw))
        last = session.last_msg_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        idle_min = (now - last).total_seconds() / 60
        if idle_min >= timeout_min:
            user_id = int(key.decode().rsplit(":", 1)[-1])
            idle.append((user_id, session))
    return idle
