from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
from pydantic import BaseModel

from src.config import settings
from src.tools.registry import ToolDef, get_registry


class RequestConfirmationParams(BaseModel):
    question: str


# ── param models ──────────────────────────────────────────────────────────────

class GetDatetimeParams(BaseModel):
    pass


class GetProfileParams(BaseModel):
    pass


class UpdateProfileParams(BaseModel):
    patch: dict[str, Any]


# ── handlers ──────────────────────────────────────────────────────────────────

async def _get_datetime(p: GetDatetimeParams, session_id: str = "") -> str:
    tz = ZoneInfo(settings.tz)
    now = datetime.now(tz)
    return now.isoformat()


async def _get_profile(p: GetProfileParams, session_id: str = "") -> str:
    from src.session.manager import get_redis
    redis = await get_redis()
    # user_id not available here — agent should call this without context needed
    # We store a single-user profile under a well-known key
    raw = await redis.get("user:profile:main")
    if raw is None:
        return "{}"
    return orjson.loads(raw).__str__()


async def _update_profile(p: UpdateProfileParams, session_id: str = "") -> str:
    from src.session.manager import get_redis
    import orjson as _orjson
    redis = await get_redis()
    raw = await redis.get("user:profile:main")
    profile: dict[str, Any] = _orjson.loads(raw) if raw else {}
    profile.update(p.patch)
    await redis.set("user:profile:main", _orjson.dumps(profile))
    return f"профиль обновлён: {list(p.patch.keys())}"


async def _request_confirmation(p: RequestConfirmationParams, session_id: str = "") -> str:
    from src.safety.confirmations import request_confirmation
    user_id = settings.allowed_user_ids[0]
    confirmed = await request_confirmation(user_id, p.question)
    return "подтверждено" if confirmed else "отменено пользователем"


# ── registration ──────────────────────────────────────────────────────────────

def _register() -> None:
    reg = get_registry()
    reg.register(ToolDef(
        name="get_current_datetime",
        description="Текущее время в часовом поясе пользователя",
        params_cls=GetDatetimeParams,
        handler=_get_datetime,
    ))
    reg.register(ToolDef(
        name="get_user_profile",
        description="Получить профиль пользователя (предпочтения, имя, заметки о себе)",
        params_cls=GetProfileParams,
        handler=_get_profile,
    ))
    reg.register(ToolDef(
        name="update_user_profile",
        description="Обновить профиль пользователя (мерж-патч)",
        params_cls=UpdateProfileParams,
        handler=_update_profile,
    ))
    reg.register(ToolDef(
        name="request_user_confirmation",
        description="Запросить подтверждение у пользователя через inline-кнопки (для удалений и спорных операций)",
        params_cls=RequestConfirmationParams,
        handler=_request_confirmation,
    ))


_register()
