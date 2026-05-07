from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.session import manager as session_mgr
from src.vault import reader

logger = structlog.get_logger()
router = Router(name="commands")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    logger.info("start", user_id=user_id)

    if reader.note_exists("_meta/portrait.md"):
        await message.answer("жив")
        return

    # First run: initiate onboarding
    redis = await session_mgr.get_redis()
    import orjson
    await redis.set(
        session_mgr.key_onboarding(user_id),
        orjson.dumps({"state": "step_bot_name"}),
        ex=86400,
    )
    await message.answer(
        "Привет! Я твой персональный AI-ассистент с долгосрочной памятью.\n\n"
        "<b>Как ты хочешь меня называть?</b>\n"
        "Например: Макс, Ася, Мнемо — или просто Ассистент."
    )


@router.message(Command("save"))
async def cmd_save(message: Message) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    redis = await session_mgr.get_redis()
    session = await session_mgr.close_session(redis, user_id)

    if session is None:
        await message.answer("нет активной сессии")
        return

    await message.answer("обрабатываю сессию...")

    msgs = await session_mgr.get_msgs(redis, session.session_id)

    from src.agent.extractor import run_pipeline

    async def notify(text: str) -> None:
        await message.answer(text)

    summary = await run_pipeline(session, msgs, notify)
    await message.answer(summary)
    logger.info("save complete", session_id=session.session_id, user_id=user_id)


@router.message(Command("undo"))
async def cmd_undo(message: Message) -> None:
    from pathlib import Path
    from src.config import settings
    from src.vault import git_ops

    result = await git_ops.revert_head(Path(settings.vault_path))
    await message.answer(result)
    logger.info("undo", result=result)
