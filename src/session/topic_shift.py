from __future__ import annotations

import structlog
from pydantic import BaseModel

from src.agent import prompts
from src.agent.loop import get_client
from src.config import settings
from src.session.manager import ActiveSession, SessionMessage, get_redis

logger = structlog.get_logger()

_CACHE_TTL = 60  # seconds — don't check more than once per minute per session


class _ShiftResult(BaseModel):
    shift: bool
    new_topic: str = ""


async def detect(
    session: ActiveSession,
    recent_msgs: list[SessionMessage],
    new_content: str,
) -> tuple[bool, str]:
    """Check if the new message represents a topic shift. Returns (shifted, new_topic).

    Returns (False, "") if:
    - session has < 4 messages (too little context)
    - this session was already checked within the last 60 seconds
    """
    if len(recent_msgs) < 4:
        return False, ""

    redis = await get_redis()
    cache_key = f"session:topic_check:{session.session_id}"
    if await redis.get(cache_key):
        return False, ""

    # Mark as checked (even before the call so parallel requests don't duplicate)
    await redis.set(cache_key, b"1", ex=_CACHE_TTL)

    last_five = "\n".join(f"{m.role.upper()}: {m.content[:200]}" for m in recent_msgs[-5:])
    system = prompts.render(
        "topic_shift",
        recent_messages=last_five,
        new_message=new_content[:500],
    )

    client = get_client()
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_fast,
        messages=[{"role": "user", "content": system}],
        response_format=_ShiftResult,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        return False, ""

    if parsed.shift:
        logger.info(
            "topic shift detected",
            session_id=session.session_id,
            new_topic=parsed.new_topic,
        )
    return parsed.shift, parsed.new_topic
