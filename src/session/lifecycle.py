from __future__ import annotations

import asyncio

import structlog

from src.config import settings
from src.session import manager as session_mgr

logger = structlog.get_logger()


async def _close_one(user_id: int, session: session_mgr.ActiveSession) -> None:
    """Close a single idle session and run the end-of-session pipeline."""
    from src.agent.extractor import run_pipeline
    from src.telegram.bot import get_bot

    redis = await session_mgr.get_redis()

    # Re-check it's still active (another path might have closed it already)
    active = await session_mgr.get_active(redis, user_id)
    if active is None or active.session_id != session.session_id:
        return

    await session_mgr.close_session(redis, user_id)
    msgs = await session_mgr.get_msgs(redis, session.session_id)

    bot = get_bot()

    async def notify(text: str) -> None:
        await bot.send_message(user_id, text)

    summary = await run_pipeline(session, msgs, notify)
    await bot.send_message(user_id, f"Сохранил сессию (таймаут)\n\n{summary}")


async def run_forever() -> None:
    """Background worker: checks for idle sessions every 60 seconds."""
    logger.info("lifecycle worker started", timeout_min=settings.session_idle_timeout_min)
    while True:
        await asyncio.sleep(60)
        try:
            redis = await session_mgr.get_redis()
            idle = await session_mgr.scan_idle(redis, settings.session_idle_timeout_min)
            for user_id, session in idle:
                logger.info("closing idle session", session_id=session.session_id, user_id=user_id)
                _t = asyncio.create_task(_close_one(user_id, session))
                _ = _t
        except Exception as exc:
            logger.error("lifecycle error", error=str(exc))
