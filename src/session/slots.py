"""Slot-binding for clarifying questions (M2 of memory-layers plan).

When the bot asks the user a *direct* question ("what's the restaurant called?"),
the next user message must reach the LLM with a literal binding — not get re-
paraphrased into a description like "ресторан (семейный)" that drops the actual
name ("БЕК"). See `docs/adr/0001-memory-layers.md` for the read-path rationale.

Flow:

1. Agent decides to ask a clarifying question → calls `set_pending_slot` tool.
2. The Redis key `slot:pending:{user_id}` stores a single `PendingSlot`.
3. Bot sends the question to the user.
4. Next user message arrives → handler calls `consume_pending`, which:
     - reads pending,
     - records the literal answer in `slot:filled:{session_id}` list,
     - clears pending.
5. Before the LLM call, handler injects a SLOT_FILLED system note so the agent
   sees the literal in this turn's context.
6. At session end, `extractor.extract` includes filled slots in its system
   prompt, instructing the LLM to honor literals when constructing entities.

The Redis layer is the source of truth; the LLM-side prompt is best-effort
nudge. Structural guarantee that "БЕК" survives even if the LLM ignores the
nudge comes from M4 (proper-noun validator).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import orjson
import structlog
from pydantic import BaseModel
from redis.asyncio import Redis

logger = structlog.get_logger()

# ── keys ──────────────────────────────────────────────────────────────────────


def key_pending(user_id: int) -> str:
    """Single pending slot per user (the bot can only ask one question at a time)."""
    return f"slot:pending:{user_id}"


def key_filled(session_id: str) -> str:
    """Append-only list of filled slots for a session (consumed by extractor)."""
    return f"slot:filled:{session_id}"


# ── TTLs ──────────────────────────────────────────────────────────────────────

_PENDING_TTL_SEC = 600  # 10 min — user gets bored, abandoned slots expire
_FILLED_TTL_SEC = 7 * 24 * 3600  # 7 days — matches msgs TTL upper bound

# ── models ────────────────────────────────────────────────────────────────────

SlotField = Literal[
    "canonical_name",
    "alias",
    "fact",
    "due",
    "status",
    "value",
]


class PendingSlot(BaseModel):
    """A clarifying question the bot is currently waiting on an answer for."""

    slot_id: str
    field: SlotField
    question: str
    entity_hint: str = ""  # short tag like "family restaurant" — used by extractor
    asked_at: datetime


class FilledSlot(BaseModel):
    """A pending slot that the user answered. Literal text preserved."""

    slot_id: str
    field: SlotField
    question: str
    entity_hint: str
    literal_value: str
    filled_at: datetime


# ── pending API ───────────────────────────────────────────────────────────────


async def set_pending(
    redis: Redis,
    user_id: int,
    *,
    field: SlotField,
    question: str,
    entity_hint: str = "",
) -> PendingSlot:
    """Register a clarifying question. Overwrites any prior pending slot."""
    slot = PendingSlot(
        slot_id=uuid.uuid4().hex[:8],
        field=field,
        question=question.strip(),
        entity_hint=entity_hint.strip(),
        asked_at=datetime.now(UTC),
    )
    await redis.set(
        key_pending(user_id),
        orjson.dumps(slot.model_dump(mode="json")),
        ex=_PENDING_TTL_SEC,
    )
    logger.info(
        "slot pending set",
        slot_id=slot.slot_id,
        field=field,
        entity_hint=entity_hint,
        user_id=user_id,
    )
    return slot


async def get_pending(redis: Redis, user_id: int) -> PendingSlot | None:
    raw = await redis.get(key_pending(user_id))
    if raw is None:
        return None
    return PendingSlot.model_validate(orjson.loads(raw))


async def clear_pending(redis: Redis, user_id: int) -> None:
    await redis.delete(key_pending(user_id))


# ── consume → filled ──────────────────────────────────────────────────────────


def _looks_like_direct_answer(text: str) -> bool:
    """Heuristic: does this user reply look like an answer rather than a counter-question?

    True for short-to-medium plain text. False for messages that themselves end
    in `?` or open with a counter-question marker. Kept simple — overshooting
    here is acceptable: an over-eager bind just produces a FilledSlot the
    extractor can interpret as `literal_value="хз"`, which is correct.
    """
    s = text.strip()
    if not s or len(s) > 500:
        return False
    if s.endswith("?"):
        return False
    return True


async def consume_pending(
    redis: Redis,
    user_id: int,
    session_id: str,
    user_message: str,
) -> FilledSlot | None:
    """If a pending slot exists and the message looks like an answer, fill it.

    Returns the FilledSlot for downstream injection (system note for current
    turn's LLM call), or None if no slot was pending / message was not a direct
    answer.

    The filled slot is also persisted to `slot:filled:{session_id}` so the
    end-of-session extractor can honor literals when constructing entities.
    """
    pending = await get_pending(redis, user_id)
    if pending is None:
        return None
    if not _looks_like_direct_answer(user_message):
        # Keep pending — user might still answer in a follow-up turn.
        return None

    filled = FilledSlot(
        slot_id=pending.slot_id,
        field=pending.field,
        question=pending.question,
        entity_hint=pending.entity_hint,
        literal_value=user_message.strip(),
        filled_at=datetime.now(UTC),
    )

    await redis.rpush(
        key_filled(session_id),
        orjson.dumps(filled.model_dump(mode="json")),
    )
    await redis.expire(key_filled(session_id), _FILLED_TTL_SEC)
    await clear_pending(redis, user_id)

    logger.info(
        "slot filled",
        slot_id=filled.slot_id,
        field=filled.field,
        entity_hint=filled.entity_hint,
        literal=filled.literal_value[:80],
        session_id=session_id,
    )
    return filled


async def list_filled(redis: Redis, session_id: str) -> list[FilledSlot]:
    raws = await redis.lrange(key_filled(session_id), 0, -1)
    return [FilledSlot.model_validate(orjson.loads(r)) for r in raws]


async def clear_filled(redis: Redis, session_id: str) -> None:
    await redis.delete(key_filled(session_id))


# ── prompt formatters ─────────────────────────────────────────────────────────


def format_filled_for_prompt(filled: FilledSlot, lang: str = "ru") -> str:
    """Render a single FilledSlot as a system-message block for the next LLM turn.

    Used by handlers/text.py right after consume_pending — injects the literal
    so the agent reads "user just answered '...' to the question '...'" before
    deciding what to do this turn.
    """
    headers = {
        "ru": "ОТВЕТ ЮЗЕРА НА ТВОЙ ПРЯМОЙ ВОПРОС",
        "en": "USER'S DIRECT ANSWER TO YOUR QUESTION",
        "uz": "FOYDALANUVCHINING TO'G'RIDAN-TO'G'RI JAVOBI",
    }
    rules = {
        "ru": (
            "Используй literal_value БУКВАЛЬНО. Не перефразируй, не нормализуй, "
            "не отбрасывай токены (особенно имена собственные капсом)."
        ),
        "en": (
            "Use literal_value VERBATIM. Do not paraphrase, normalize, or drop "
            "tokens (especially uppercase proper nouns)."
        ),
        "uz": (
            "literal_value ni AYNAN ishlat. Boshqacha qilib aytma, normallashtirma, "
            "tokenlarni (ayniqsa katta harfli atoqli otlarni) tashlamasdan ishlat."
        ),
    }
    header = headers.get(lang, headers["ru"])
    rule = rules.get(lang, rules["ru"])
    hint_block = f"entity_hint: {filled.entity_hint}\n" if filled.entity_hint else ""
    return (
        f"[{header}]\n"
        f"{hint_block}"
        f"field: {filled.field}\n"
        f"question: {filled.question}\n"
        f'literal_value: "{filled.literal_value}"\n'
        f"\n{rule}"
    )


def format_filled_list_for_extraction(filled: list[FilledSlot], lang: str = "ru") -> str:
    """Render all filled slots for inclusion in the session-end extractor prompt.

    Empty list → empty string. Otherwise a block telling the extraction LLM to
    use these literals when constructing the matching entities.
    """
    if not filled:
        return ""
    headers = {
        "ru": "СЛОТЫ С БУКВАЛЬНЫМИ ОТВЕТАМИ ЮЗЕРА",
        "en": "SLOTS WITH USER'S LITERAL ANSWERS",
        "uz": "FOYDALANUVCHINING AYNIY JAVOBLARI BO'YICHA SLOTLAR",
    }
    rules = {
        "ru": (
            "Когда строишь entities, для каждой сущности соответствующей entity_hint "
            "используй literal_value БУКВАЛЬНО в указанном field. "
            "Не перефразируй, не нормализуй, не выкидывай токены."
        ),
        "en": (
            "When building entities, for each entity matching entity_hint use "
            "literal_value VERBATIM in the named field. Do not paraphrase, "
            "normalize, or drop tokens."
        ),
        "uz": (
            "Entitylar tuzayotganda, entity_hint ga mos har bir entity uchun "
            "literal_value ni ko'rsatilgan fieldda AYNAN ishlat."
        ),
    }
    lines = [f"# {headers.get(lang, headers['ru'])}\n"]
    for f in filled:
        hint = f.entity_hint or "(no hint)"
        lines.append(
            f"- entity_hint={hint!r}, field={f.field}, "
            f'value="{f.literal_value}" (asked: "{f.question[:60]}")'
        )
    lines.append("")
    lines.append(rules.get(lang, rules["ru"]))
    return "\n".join(lines)
