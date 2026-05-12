from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.config import settings
from src.telegram.handlers.commands import router as commands_router
from src.telegram.handlers.photo import router as photo_router
from src.telegram.handlers.text import router as text_router
from src.telegram.handlers.voice import router as voice_router

logger = structlog.get_logger()

_bot: Bot | None = None

_confirm_router = Router(name="confirmations")


def get_bot() -> Bot:
    assert _bot is not None, "bot not initialized yet"
    return _bot


class WhitelistMiddleware(BaseMiddleware):
    """Silently drops updates from users not in ALLOWED_USER_IDS."""

    def __init__(self, allowed_ids: list[int]) -> None:
        self._allowed: frozenset[int] = frozenset(allowed_ids)
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id not in self._allowed:
            return None
        return await handler(event, data)


@_confirm_router.callback_query(F.data.startswith("confirm:"))
async def handle_confirm_callback(callback: CallbackQuery) -> None:
    """Handle inline keyboard confirmation buttons."""
    if callback.data is None or callback.from_user is None:
        return
    # data format: "confirm:<correlation_id>:yes|no"
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, correlation_id, answer = parts
    confirmed = answer == "yes"

    from src.i18n import t
    from src.safety.confirmations import publish_answer
    from src.session import manager as session_mgr

    await publish_answer(correlation_id, confirmed)

    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, callback.from_user.id)
    ui_lang = session_mgr.get_ui_language(profile)
    label = t("confirm.confirmed" if confirmed else "confirm.cancelled", ui_lang)

    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(label)
    logger.info("confirmation answered", correlation_id=correlation_id, confirmed=confirmed)


def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    global _bot
    _bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.update.outer_middleware(WhitelistMiddleware(settings.allowed_user_ids))
    # Order matters: confirmations and commands before media, then catch-all text
    dp.include_router(_confirm_router)
    dp.include_router(commands_router)
    dp.include_router(voice_router)
    dp.include_router(photo_router)
    dp.include_router(text_router)
    return _bot, dp
