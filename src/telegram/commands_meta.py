from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from src.i18n import LANGUAGES, t

logger = structlog.get_logger()

_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "cmd_descriptions.start"),
    ("save", "cmd_descriptions.save"),
    ("undo", "cmd_descriptions.undo"),
    ("settings", "cmd_descriptions.settings"),
)


async def register_bot_commands(bot: Bot) -> None:
    """Publish the slash-command list to Telegram in all supported languages.

    Telegram picks the language based on the client's locale, falling back to
    the default (no language_code) for unsupported locales. We publish the
    default as English so non-ru/en/uz users get a sensible list.
    """
    scope = BotCommandScopeAllPrivateChats()

    for lang in LANGUAGES:
        commands = [
            BotCommand(command=cmd, description=t(desc_key, lang)) for cmd, desc_key in _COMMANDS
        ]
        await bot.set_my_commands(commands, scope=scope, language_code=lang)

    default_commands = [
        BotCommand(command=cmd, description=t(desc_key, "en")) for cmd, desc_key in _COMMANDS
    ]
    await bot.set_my_commands(default_commands, scope=scope)

    logger.info("bot commands registered", languages=LANGUAGES)
