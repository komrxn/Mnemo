"""Agent-facing tool: register a pending clarifying-question slot.

When the bot's LLM decides to ask the user a *direct* clarifying question
(e.g. "what's the restaurant called?"), it should call `set_pending_slot`
BEFORE sending the question. The next user message then fills the slot with
the literal answer, bypassing LLM normalization that loses tokens like "БЕК".

See `src/session/slots.py` and `docs/adr/0001-memory-layers.md` for the
end-to-end flow and rationale.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.config import settings
from src.session import slots
from src.tools.registry import ToolDef, get_registry


class SetPendingSlotParams(BaseModel):
    field: Literal["canonical_name", "alias", "fact", "due", "status", "value"] = Field(
        description=(
            "Which field of the entity the answer fills. Use 'canonical_name' "
            "when asking 'what's it called?', 'fact' for a factual detail, "
            "'value' when the field type is unclear."
        ),
    )
    question: str = Field(
        min_length=3,
        max_length=400,
        description="The exact clarifying question you are about to ask the user.",
    )
    entity_hint: str = Field(
        default="",
        max_length=80,
        description=(
            "Short tag identifying the entity this answer belongs to "
            "(e.g. 'family restaurant', 'crypto product'). Used by the "
            "end-of-session extractor to match the slot to an entity."
        ),
    )


async def _set_pending_slot(p: SetPendingSlotParams, session_id: str = "") -> str:
    """Register the pending slot for the single allowed user.

    Returns a short status string the LLM can echo or log. Side effect: writes
    `slot:pending:{user_id}` in Redis with a 10-minute TTL.

    Hard read-before-ask gate: refuses unless `recall` was called within the
    last 120s for this session. The LLM must consult memory before deciding
    that a clarifying question is necessary — otherwise it might ask about
    something the user already told it.
    """
    from src.session.manager import get_redis
    from src.tools.recall import was_recall_done

    if session_id and not await was_recall_done(session_id):
        return (
            "⛔ Перед set_pending_slot ОБЯЗАТЕЛЬНО вызови recall(query=...) "
            "с ключевым словом из текущего вопроса. Юзер мог уже это упомянуть — "
            "если recall найдёт литерал, переспрашивать НЕ нужно. Сделай recall "
            "и повтори set_pending_slot, если действительно ничего не нашлось."
        )

    redis = await get_redis()
    user_id = settings.allowed_user_ids[0]
    slot = await slots.set_pending(
        redis,
        user_id,
        field=p.field,
        question=p.question,
        entity_hint=p.entity_hint,
    )
    return (
        f"pending slot registered: id={slot.slot_id} field={slot.field} "
        f"hint={slot.entity_hint!r}. Now ask the user the question verbatim."
    )


def _register() -> None:
    reg = get_registry()
    reg.register(
        ToolDef(
            name="set_pending_slot",
            description=(
                "Зарегистрируй pending-слот ПЕРЕД тем как задать юзеру прямой "
                "уточняющий вопрос (например 'как называется ресторан?'). "
                "Следующее сообщение юзера будет привязано к этому слоту "
                "литерально, без перефразирования. Используй когда тебе нужно "
                "получить КОНКРЕТНОЕ значение (имя, дату, факт) — особенно "
                "имена собственные, которые легко потерять при нормализации."
            ),
            params_cls=SetPendingSlotParams,
            handler=_set_pending_slot,
        )
    )


_register()
