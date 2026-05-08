from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import orjson
import structlog
from aiogram import Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from src.agent import loop as agent_loop
from src.agent import prompts
from src.config import settings
from src.session import manager as session_mgr
from src.tools.registry import get_registry

logger = structlog.get_logger()
router = Router(name="text")

_PERSONALITY_MAP = {
    "1": "Дружелюбный и тёплый — поддерживающий, позитивный, создаёт комфорт в диалоге",
    "2": "Строгий и по делу — минимум лирики, только факты и действия",
    "3": "Немного саркастичный — остроумный, иногда подкалывает, но всегда по существу",
    "4": "Как ментор — задаёт вопросы, помогает думать самостоятельно, не даёт готовых ответов",
}


# ── onboarding helpers ────────────────────────────────────────────────────────


def _is_affirmative(text: str) -> bool:
    low = text.strip().lower()
    affirmatives = (
        "да",
        "ок",
        "ok",
        "yes",
        "конечно",
        "давай",
        "поехали",
        "верно",
        "согласен",
        "согласна",
        "именно",
        "+",
        "👍",
    )
    return any(w in low for w in affirmatives)


async def _run_onboarding_plan(portrait: str, profile: dict[str, object]) -> str:
    """Call LLM with onboarding prompt to get a plan (no tools, just text)."""
    bot_name = str(profile.get("bot_name", "Ассистент"))
    personality = str(profile.get("personality", ""))
    system = prompts.render("onboarding", bot_name=bot_name, personality=personality)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Текст портрета:\n\n{portrait}\n\n"
                "Составь план создания заметок (только план, не создавай ничего)."
            ),
        },
    ]
    client = agent_loop.get_client()

    response = await client.chat.completions.create(
        model=settings.openai_model_main,
        messages=messages,
    )
    return response.choices[0].message.content or ""


async def _create_owner_note(
    portrait: str,
    profile: dict[str, object],
) -> None:
    """Create _meta/owner.md — the owner anchor node."""
    owner_name = str(profile.get("owner_name", "Владелец"))
    client = agent_loop.get_client()

    # LLM extracts key facts + aliases from portrait
    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {
                "role": "system",
                "content": (
                    "Проанализируй портрет пользователя и верни JSON с двумя полями:\n"
                    '{"facts": ["факт1", "факт2", ...], "aliases": ["Имя1", "Имя2", ...]}\n'
                    "facts: 3-7 ключевых фактов о личности (только явно сказанное).\n"
                    "aliases: все варианты имени/ника из текста, включая переданное имя."
                ),
            },
            {"role": "user", "content": f"Имя владельца: {owner_name}\n\nПортрет:\n{portrait}"},
        ],
        response_format={"type": "json_object"},
    )
    import json

    raw_json = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw_json)
    except Exception:
        data = {}

    facts: list[str] = data.get("facts", [])
    aliases: list[str] = data.get("aliases", [owner_name])
    if owner_name not in aliases:
        aliases.insert(0, owner_name)

    body = "\n".join(f"- {f}" for f in facts) if facts else owner_name
    fm: dict[str, object] = {
        "type": "person",
        "is_owner": True,
        "aliases": aliases,
    }

    from src.vault import writer as vault_writer

    try:
        await vault_writer.write_note("_meta/owner.md", body, fm)
    except Exception as exc:
        logger.warning("owner note save failed", error=str(exc))
        return

    # Persist owner info in profile
    from src.session import manager as session_mgr2

    redis = await session_mgr2.get_redis()
    user_id = int(str(profile.get("user_id", settings.allowed_user_ids[0])))
    await session_mgr2.update_profile(
        redis, user_id, {"owner_path": "_meta/owner.md", "owner_name": owner_name}
    )


_ONBOARDING_DONE_SENTINEL = "[ONBOARDING_DONE]"
_ONBOARDING_MAX_TURNS = 5


async def _run_onboarding_turn(
    messages: list[dict[str, Any]],
) -> tuple[str, bool]:
    """Run one turn of the onboarding agent loop.

    Returns (assistant_text, is_done). is_done is True when the agent emitted
    the [ONBOARDING_DONE] sentinel — meaning the initial graph is built.
    """
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args)

    result = await agent_loop.run_chat(messages, registry.openai_specs(), dispatch, max_rounds=30)
    is_done = _ONBOARDING_DONE_SENTINEL in result
    text = result.replace(_ONBOARDING_DONE_SENTINEL, "").strip()
    return text, is_done


async def _finalize_onboarding() -> None:
    """Bootstrap default scheduled tasks (called once after [ONBOARDING_DONE])."""
    try:
        from src.scheduler.defaults import bootstrap_defaults

        bootstrap_defaults()
        logger.info("default tasks bootstrapped after onboarding")
    except Exception as exc:
        logger.warning("default tasks bootstrap failed", error=str(exc))


async def _run_onboarding_execute(
    portrait: str,
    profile: dict[str, object],
    reply_fn: Callable[[str], Awaitable[None]],
) -> None:
    """Kick off multi-turn onboarding: owner+portrait first, then agent builds tree.

    The agent may return mid-flight asking the owner a clarifying question
    (just plain text, no tool call). When that happens, we save state in Redis
    and the next user message resumes via _handle_onboarding(state="agent_question").
    """
    user_id = int(str(profile.get("user_id", settings.allowed_user_ids[0])))

    # Step 1: write owner.md FIRST so agent can anchor its tree to it
    try:
        await _create_owner_note(portrait, profile)
    except Exception as exc:
        logger.warning("owner note creation failed", error=str(exc))

    # Step 2: archive raw portrait
    from src.vault import writer as vault_writer

    try:
        await vault_writer.write_note("_meta/portrait.md", portrait, {"type": "inbox"})
    except Exception as exc:
        logger.warning("portrait save failed", error=str(exc))

    # Step 3: build initial agent prompt
    bot_name = str(profile.get("bot_name", "Ассистент"))
    personality = str(profile.get("personality", ""))
    owner_name = str(profile.get("owner_name", "Владелец"))
    system = prompts.render(
        "onboarding",
        bot_name=bot_name,
        personality=personality,
        owner_name=owner_name,
        owner_path="_meta/owner.md",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Текст портрета:\n\n{portrait}\n\n"
                "Построй начальное древо знаний согласно инструкциям в системном промпте. "
                "Если что-то неясно или двусмысленно — спроси меня прямо текстом без tool-call. "
                "Когда закончишь — финальное сообщение должно содержать "
                f"{_ONBOARDING_DONE_SENTINEL}."
            ),
        },
    ]

    text, is_done = await _run_onboarding_turn(messages)
    await reply_fn(text)

    if is_done:
        await _finalize_onboarding()
        return

    # Agent has a clarifying question — save state for follow-up turns
    redis = await session_mgr.get_redis()
    key = session_mgr.key_onboarding(user_id)
    messages.append({"role": "assistant", "content": text})
    await redis.set(
        key,
        orjson.dumps(
            {
                "state": "agent_question",
                "messages": messages,
                "portrait": portrait,
                "turn_count": 1,
            }
        ),
        ex=86400,
    )


async def _handle_onboarding(
    user_id: int,
    content: str,
    reply_fn: Callable[[str], Awaitable[None]],
) -> bool:
    """Returns True if this message was handled as onboarding."""
    redis = await session_mgr.get_redis()
    key = session_mgr.key_onboarding(user_id)
    raw = await redis.get(key)
    if raw is None:
        return False

    state = orjson.loads(raw)
    step = state["state"]

    if step == "step_bot_name":
        bot_name = content.strip() or "Ассистент"
        await session_mgr.update_profile(redis, user_id, {"bot_name": bot_name})
        await redis.set(key, orjson.dumps({"state": "step_personality"}), ex=86400)
        await reply_fn(
            f"Буду <b>{bot_name}</b>! Теперь выбери <b>стиль общения</b>:\n\n"
            "1 — Дружелюбный и тёплый\n"
            "2 — Строгий и по делу\n"
            "3 — Немного саркастичный\n"
            "4 — Как ментор (задаёт вопросы, направляет)\n"
            "5 — Свой стиль (просто напиши)"
        )
        return True

    if step == "step_personality":
        raw_input = content.strip()
        personality = _PERSONALITY_MAP.get(raw_input, raw_input)
        await session_mgr.update_profile(redis, user_id, {"personality": personality})
        await redis.set(key, orjson.dumps({"state": "step_owner_name"}), ex=86400)
        await reply_fn(
            "Понял! Теперь — <b>как тебя зовут?</b>\n\n"
            "Имя или ник — чтобы я к тебе обращался правильно."
        )
        return True

    if step == "step_owner_name":
        owner_name = content.strip() or "Владелец"
        await session_mgr.update_profile(
            redis, user_id, {"owner_name": owner_name, "user_id": user_id}
        )
        await redis.set(key, orjson.dumps({"state": "awaiting_portrait"}), ex=86400)
        await reply_fn(
            f"Отлично, <b>{owner_name}</b>! И последнее — <b>расскажи о себе</b>:\n\n"
            "Кто ты, чем занимаешься, какие проекты и люди важны, "
            "интересы, ценности, цели. Чем подробнее — тем умнее буду с первого дня."
        )
        return True

    if step == "awaiting_portrait":
        await reply_fn("анализирую портрет...")
        redis2 = await session_mgr.get_redis()
        profile = await session_mgr.get_profile(redis2, user_id)
        plan = await _run_onboarding_plan(content, profile)
        await redis.set(
            key,
            orjson.dumps({"state": "awaiting_confirm", "portrait": content}),
            ex=3600,
        )
        await reply_fn(f"{plan}\n\nПодтвердить?")
        return True

    if step == "awaiting_confirm":
        portrait = state.get("portrait", "")
        if _is_affirmative(content):
            await reply_fn("создаю заметки, это займёт минуту...")
            await redis.delete(key)
            redis2 = await session_mgr.get_redis()
            profile = await session_mgr.get_profile(redis2, user_id)
            await _run_onboarding_execute(portrait, profile, reply_fn)
        else:
            await redis.delete(key)
            await reply_fn("онбординг отменён. Когда будешь готов, напиши /start.")
        return True

    if step == "agent_question":
        # Owner is answering the agent's clarifying question — resume the loop
        saved_messages: list[dict[str, Any]] = state.get("messages", [])
        portrait = state.get("portrait", "")
        turn_count = int(state.get("turn_count", 0))

        saved_messages.append({"role": "user", "content": content})

        text, is_done = await _run_onboarding_turn(saved_messages)
        await reply_fn(text)

        if is_done:
            await redis.delete(key)
            await _finalize_onboarding()
            return True

        if turn_count + 1 >= _ONBOARDING_MAX_TURNS:
            # Agent still has questions but we hit the limit — finalize forcibly
            logger.warning("onboarding hit max turns, ending forcibly", user_id=user_id)
            await redis.delete(key)
            await reply_fn("Заверши то что начал — детали можем уточнить позже в обычном диалоге.")
            await _finalize_onboarding()
            return True

        saved_messages.append({"role": "assistant", "content": text})
        await redis.set(
            key,
            orjson.dumps(
                {
                    "state": "agent_question",
                    "messages": saved_messages,
                    "portrait": portrait,
                    "turn_count": turn_count + 1,
                }
            ),
            ex=86400,
        )
        return True

    return False


# ── topic shift helpers ───────────────────────────────────────────────────────


async def _maybe_shift_session(
    user_id: int,
    content: str,
    session: session_mgr.ActiveSession,
    history: list[session_mgr.SessionMessage],
    reply_fn: Callable[[str], Awaitable[None]],
) -> session_mgr.ActiveSession:
    """Check for topic shift; if detected, close old session and open a new one."""
    from src.session.topic_shift import detect

    try:
        shifted, new_topic = await detect(session, history, content)
    except Exception as exc:
        logger.warning("topic shift check failed", error=str(exc))
        return session

    if not shifted:
        return session

    await reply_fn("вижу смену темы — сохраняю предыдущую сессию...")
    redis = await session_mgr.get_redis()
    closed = await session_mgr.close_session(redis, user_id)
    if closed:
        msgs = await session_mgr.get_msgs(redis, closed.session_id)
        _t = asyncio.create_task(_run_pipeline_bg(closed, msgs, reply_fn))
        _ = _t

    new_session = await session_mgr.create_session(redis, user_id)
    if new_topic:
        await session_mgr.touch(redis, user_id, new_session, topic=new_topic)
        new_session = new_session.model_copy(update={"topic": new_topic})
    return new_session


async def _run_pipeline_bg(
    session: session_mgr.ActiveSession,
    msgs: list[session_mgr.SessionMessage],
    notify: Callable[[str], Awaitable[None]],
) -> None:
    from src.agent.extractor import run_pipeline

    try:
        summary = await run_pipeline(session, msgs, notify)
        await notify(summary)
    except Exception as exc:
        logger.error("pipeline bg failed", session_id=session.session_id, error=str(exc))


# ── main processing ───────────────────────────────────────────────────────────


async def process_input(
    user_id: int,
    content: str,
    reply_fn: Callable[[str], Awaitable[None]],
    kind: Literal["text", "voice", "image"] = "text",
    meta: dict[str, str] | None = None,
) -> None:
    """Core message processing: session → agent loop → store → reply."""
    if kind == "text" and await _handle_onboarding(user_id, content, reply_fn):
        return

    redis = await session_mgr.get_redis()
    session = await session_mgr.get_or_create(redis, user_id)

    history_msgs = await session_mgr.get_msgs(redis, session.session_id)

    if kind == "text":
        session = await _maybe_shift_session(user_id, content, session, history_msgs, reply_fn)
        history_msgs = await session_mgr.get_msgs(redis, session.session_id)

    await session_mgr.push_msg(
        redis,
        session.session_id,
        session_mgr.SessionMessage(
            role="user",
            content=content,
            ts=datetime.now(UTC),
            kind=kind,
            meta=meta or {},
        ),
    )
    await session_mgr.touch(redis, user_id, session)

    profile = await session_mgr.get_profile(redis, user_id)

    history = [{"role": m.role, "content": m.content} for m in history_msgs[-30:]]

    bot_name = str(profile.get("bot_name", "Ассистент"))
    personality = str(profile.get("personality", ""))
    system_prompt = prompts.render("system", bot_name=bot_name, personality=personality)
    openai_msgs = agent_loop.build_messages(system_prompt, profile, history, content)
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args, session_id=session.session_id)

    reply = await agent_loop.run_chat(openai_msgs, registry.openai_specs(), dispatch)

    await session_mgr.push_msg(
        redis,
        session.session_id,
        session_mgr.SessionMessage(
            role="assistant",
            content=reply,
            ts=datetime.now(UTC),
        ),
    )

    await reply_fn(reply)
    logger.info("replied", user_id=user_id, session_id=session.session_id, kind=kind)


@router.message()
async def handle_text(message: Message) -> None:
    if not message.text or not message.from_user:
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.from_user.id):
        await process_input(
            message.from_user.id,
            message.text.strip(),
            message.answer,
        )
