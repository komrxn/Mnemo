from __future__ import annotations

import asyncio
import uuid

import structlog

from src.session.manager import get_redis

logger = structlog.get_logger()

_TIMEOUT = 300  # 5 minutes


def _channel(correlation_id: str) -> str:
    return f"confirm:{correlation_id}"


async def request_confirmation(user_id: int, question: str) -> bool:
    """Send inline keyboard to user; block until confirmed/cancelled or timeout.

    Returns True if user confirmed, False otherwise.
    """
    from src.telegram.bot import get_bot
    from src.telegram.keyboards import confirm_keyboard

    correlation_id = uuid.uuid4().hex[:12]
    bot = get_bot()

    await bot.send_message(
        user_id,
        question,
        reply_markup=confirm_keyboard(correlation_id),
    )

    redis = await get_redis()
    pubsub = redis.pubsub()
    channel = _channel(correlation_id)
    await pubsub.subscribe(channel)
    logger.debug("awaiting confirmation", correlation_id=correlation_id)

    try:
        deadline = asyncio.get_event_loop().time() + _TIMEOUT
        async for message in pubsub.listen():
            if asyncio.get_event_loop().time() > deadline:
                break
            if message["type"] == "message":
                answer = message["data"]
                return answer == b"yes"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[attr-defined]

    logger.info("confirmation timed out", correlation_id=correlation_id)
    return False


async def publish_answer(correlation_id: str, confirmed: bool) -> None:
    """Called by callback handler when user taps inline button."""
    redis = await get_redis()
    channel = _channel(correlation_id)
    value = b"yes" if confirmed else b"no"
    await redis.publish(channel, value)
