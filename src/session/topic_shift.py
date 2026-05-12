from __future__ import annotations

import structlog
from pydantic import BaseModel

from src.agent import prompts
from src.agent.loop import get_client
from src.config import settings
from src.session.manager import ActiveSession, SessionMessage, get_redis

logger = structlog.get_logger()

_CACHE_TTL = 60  # seconds — don't check more than once per minute per session

# Question-mark variants that should suppress topic-shift detection:
# regular ASCII `?` plus CJK fullwidth and Arabic forms (for completeness).
_QUESTION_MARKS = ("?", "？", "؟")
# Trailing characters we strip before checking — emojis, spaces, ellipses are
# common after a question and shouldn't hide the underlying `?`.
_TRAILING_STRIP = " \t\n.…»\"'»🙂🤔😅😊😉"


def _last_assistant_asked_question(recent_msgs: list[SessionMessage]) -> bool:
    """True iff the most recent assistant message ends with a question mark.

    Walks backward through `recent_msgs` (which may end with a user message
    that triggered the check) to find the last assistant turn.
    """
    for m in reversed(recent_msgs):
        if m.role != "assistant":
            continue
        tail = m.content.rstrip(_TRAILING_STRIP)
        return tail.endswith(_QUESTION_MARKS)
    return False


async def _slot_pending_for_session() -> bool:
    """Best-effort check: is there a pending slot for the single allowed user?

    Single-user invariant — the bot always serves `allowed_user_ids[0]`. If
    Redis is down or slots module errors, return False so we don't suppress
    forever on infra hiccups.
    """
    try:
        from src.config import settings
        from src.session import slots

        redis = await get_redis()
        pending = await slots.get_pending(redis, settings.allowed_user_ids[0])
        return pending is not None
    except Exception as exc:
        logger.warning("slot pending check failed", error=str(exc))
        return False


class _ShiftResult(BaseModel):
    shift: bool
    new_topic: str = ""


async def detect(
    session: ActiveSession,
    recent_msgs: list[SessionMessage],
    new_content: str,
    notes_lang: str = "ru",
) -> tuple[bool, str]:
    """Check if the new message represents a topic shift. Returns (shifted, new_topic).

    Returns (False, "") if:
    - session has < 4 messages (too little context)
    - this session was already checked within the last 60 seconds
    - the bot's last message ended with a question or a pending slot is open
      (Q&A in progress — don't split the answer into a new session)
    """
    if len(recent_msgs) < 4:
        return False, ""

    # Suppress shift detection while the bot is waiting on a direct answer.
    # Two signals: (1) last assistant message ends with `?` or `？` (CJK),
    # (2) a `set_pending_slot` was registered. Either is sufficient.
    if _last_assistant_asked_question(recent_msgs):
        return False, ""
    if await _slot_pending_for_session():
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
        lang=notes_lang,
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
