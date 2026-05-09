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


class FetchUrlParams(BaseModel):
    url: str
    max_chars: int = 8000


# ── handlers ──────────────────────────────────────────────────────────────────


async def _get_datetime(p: GetDatetimeParams, session_id: str = "") -> str:
    tz = ZoneInfo(settings.tz)
    now = datetime.now(tz)
    return now.isoformat()


async def _get_profile(p: GetProfileParams, session_id: str = "") -> str:
    """Read the single-user profile from canonical key (key_profile)."""
    from src.session.manager import get_profile, get_redis

    redis = await get_redis()
    user_id = settings.allowed_user_ids[0]
    profile = await get_profile(redis, user_id)
    return orjson.dumps(profile).decode() if profile else "{}"


async def _update_profile(p: UpdateProfileParams, session_id: str = "") -> str:
    """Update the single-user profile via canonical helper (no parallel keys)."""
    from src.session.manager import get_redis, update_profile

    redis = await get_redis()
    user_id = settings.allowed_user_ids[0]
    await update_profile(redis, user_id, p.patch)
    return f"профиль обновлён: {list(p.patch.keys())}"


async def _request_confirmation(p: RequestConfirmationParams, session_id: str = "") -> str:
    from src.safety.confirmations import request_confirmation

    user_id = settings.allowed_user_ids[0]
    confirmed = await request_confirmation(user_id, p.question)
    return "подтверждено" if confirmed else "отменено пользователем"


async def _fetch_url(p: FetchUrlParams, session_id: str = "") -> str:
    """Fetch a web page and return its main text content (no HTML tags).

    Used when the user mentions a URL or portfolio page and the bot needs
    to read it. Returns plain text truncated to max_chars (default 8000).
    """
    import httpx
    from bs4 import BeautifulSoup

    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; mnemo-bot/1.0)"},
        ) as client:
            r = await client.get(p.url)
            r.raise_for_status()
            html = r.text
    except httpx.HTTPError as exc:
        return f"не смог получить {p.url}: {exc}"

    soup = BeautifulSoup(html, "lxml")
    # Drop noise
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse runs of empty lines
    lines = [ln for ln in text.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)

    if len(cleaned) > p.max_chars:
        cleaned = cleaned[: p.max_chars] + f"\n\n[…truncated to {p.max_chars} chars]"

    title = soup.title.string.strip() if soup.title and soup.title.string else "(no title)"
    return f"URL: {p.url}\nTitle: {title}\n\n{cleaned}"


# ── registration ──────────────────────────────────────────────────────────────


def _register() -> None:
    reg = get_registry()
    reg.register(
        ToolDef(
            name="get_current_datetime",
            description="Текущее время в часовом поясе пользователя",
            params_cls=GetDatetimeParams,
            handler=_get_datetime,
        )
    )
    reg.register(
        ToolDef(
            name="get_user_profile",
            description="Получить профиль пользователя (предпочтения, имя, заметки о себе)",
            params_cls=GetProfileParams,
            handler=_get_profile,
        )
    )
    reg.register(
        ToolDef(
            name="update_user_profile",
            description="Обновить профиль пользователя (мерж-патч)",
            params_cls=UpdateProfileParams,
            handler=_update_profile,
        )
    )
    reg.register(
        ToolDef(
            name="request_user_confirmation",
            description=(
                "Запросить подтверждение у пользователя через inline-кнопки "
                "(для удалений и спорных операций)"
            ),
            params_cls=RequestConfirmationParams,
            handler=_request_confirmation,
        )
    )
    reg.register(
        ToolDef(
            name="fetch_url",
            description=(
                "Скачать веб-страницу и вернуть её текст (без HTML-тегов). "
                "Используй когда юзер кидает ссылку (портфолио, статью, профиль) и "
                "тебе нужно понять что там"
            ),
            params_cls=FetchUrlParams,
            handler=_fetch_url,
        )
    )


_register()
