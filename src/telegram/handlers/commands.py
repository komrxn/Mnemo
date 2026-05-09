from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.session import manager as session_mgr
from src.vault import reader

logger = structlog.get_logger()
router = Router(name="commands")


async def _start_onboarding(user_id: int, redis, message: Message) -> None:
    """Initiate the onboarding flow. Used both for explicit /start and for
    auto-onboarding when an unknown user sends their first message.
    """
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


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    logger.info("start", user_id=user_id)

    redis = await session_mgr.get_redis()

    # Already onboarded? Be polite, don't wipe — even if user typed /start
    # by accident a year later. Real reset requires manual file deletion + ack.
    if reader.note_exists("_meta/portrait.md") and reader.note_exists("_meta/owner.md"):
        await message.answer(
            "жив. Если хочешь начать онбординг с нуля — сначала удали "
            "<code>_meta/portrait.md</code> и <code>_meta/owner.md</code> в Obsidian, "
            "потом снова /start."
        )
        return

    # Reset stale onboarding state (half-finished runs)
    await redis.delete(session_mgr.key_onboarding(user_id))
    await _start_onboarding(user_id, redis, message)


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

    msgs = await session_mgr.get_msgs(redis, session.session_id)

    # Guard against empty/trivial sessions — extracting nothing burns LLM tokens
    user_msgs = [m for m in msgs if m.role == "user"]
    if len(user_msgs) < 1:
        await message.answer("сессия пустая, сохранять нечего")
        return

    await message.answer("обрабатываю сессию...")

    from src.agent.extractor import run_pipeline

    async def notify(text: str) -> None:
        await message.answer(text)

    try:
        summary = await run_pipeline(session, msgs, notify)
        await message.answer(summary)
        logger.info("save complete", session_id=session.session_id, user_id=user_id)
    except Exception as exc:
        logger.error("save failed", session_id=session.session_id, error=str(exc))
        await message.answer(f"⚠ не смог разобрать сессию: {exc}")


@router.message(Command("undo"))
async def cmd_undo(message: Message) -> None:
    """Revert last vault commit — but refuse if it touched onboarding files."""
    from pathlib import Path

    from src.config import settings
    from src.vault import git_ops

    vault = Path(settings.vault_path)
    # Safe-list guard: don't revert onboarding setup files
    try:
        last_files = await git_ops._run(  # type: ignore[attr-defined]
            vault, "diff", "--name-only", "HEAD~1", "HEAD"
        )
    except Exception:
        last_files = ""
    onboarding_markers = ("_meta/owner.md", "_meta/portrait.md")
    if any(marker in last_files for marker in onboarding_markers):
        await message.answer(
            "⛔ последний коммит затрагивает onboarding-файлы (owner/portrait). "
            "Я не буду их откатывать автоматически. Если действительно нужно — "
            "разрули вручную через git."
        )
        return

    result = await git_ops.revert_head(vault)
    await message.answer(result)
    logger.info("undo", result=result)
