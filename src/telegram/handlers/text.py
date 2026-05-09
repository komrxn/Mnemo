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
                    "Проанализируй портрет ВЛАДЕЛЬЦА и верни JSON с двумя полями:\n"
                    '{"facts": ["факт1", "факт2", ...], "aliases": ["Имя1", "Имя2", ...]}\n\n'
                    "facts: 3-7 ключевых фактов о личности САМОГО ВЛАДЕЛЬЦА. "
                    "Только то что явно сказано про него самого. НЕ про его девушку, "
                    "семью, проекты — только про него.\n\n"
                    "aliases: ТОЛЬКО варианты ИМЕНИ ИЛИ НИКА самого владельца "
                    "(полное имя, фамилия, ник, прозвище, английский вариант). "
                    "Имена ДРУГИХ людей (девушки, семьи, коллег) сюда НЕ ВКЛЮЧАЙ "
                    "никогда. Например если в портрете написано 'я Komron, есть "
                    "девушка Даша' — aliases = ['Komron'], НЕ ['Komron', 'Даша']."
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

    # Sanity filter: keep only aliases that look related to the owner_name itself.
    # rapidfuzz.partial_ratio detects subset/transliteration overlap; threshold 60
    # is loose enough to keep "Komron Khakimov" vs "Komron" or "komrxn",
    # but tight enough to drop "Даша" given owner_name="Komron".
    from rapidfuzz import fuzz as _fuzz

    filtered_aliases = [
        a
        for a in aliases
        if isinstance(a, str)
        and (
            _fuzz.partial_ratio(a.lower(), owner_name.lower()) >= 60
            or owner_name.lower() in a.lower()
            or a.lower() in owner_name.lower()
        )
    ]
    if owner_name not in filtered_aliases:
        filtered_aliases.insert(0, owner_name)
    aliases = filtered_aliases

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


async def _refine_owner_from_dialog(
    messages: list[dict[str, Any]],
    profile: dict[str, object],
) -> None:
    """Pass 2 of owner.md creation: enrich the placeholder using the FULL onboarding
    dialog (all turns, all clarifications), not just the first portrait.

    Closes a long-standing bug: owner.md previously took facts only from the
    initial user message, ignoring everything the agent extracted via follow-up
    questions, fetched URLs, or vision-described screenshots.
    """
    owner_name = str(profile.get("owner_name", "Владелец"))
    client = agent_loop.get_client()

    # Compress dialog into a single transcript (skip system messages)
    transcript_parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        transcript_parts.append(f"{role.upper()}: {content[:2000]}")
    transcript = "\n\n".join(transcript_parts[-30:])  # cap to last 30 turns

    if not transcript:
        return

    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {
                "role": "system",
                "content": (
                    "Проанализируй диалог онбординга и верни JSON с двумя полями: "
                    '{"facts": ["факт1", "факт2", ...], "aliases": ["Имя1", ...]}\n\n'
                    "facts: 5-10 КЛЮЧЕВЫХ фактов о владельце ИЗ ВСЕГО ДИАЛОГА — "
                    "то что юзер реально сказал про себя за все реплики. "
                    "Конкретно (даты, названия), а не общё. НЕ про девушку, семью, "
                    "проекты по отдельности — только про САМОГО владельца как личность.\n\n"
                    "aliases: ТОЛЬКО варианты ИМЕНИ владельца. Имена других людей НЕ ВКЛЮЧАЙ."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Имя владельца: {owner_name}\n\n"
                    f"Полный диалог онбординга:\n\n{transcript}"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    import json

    raw_json = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw_json)
    except Exception:
        return

    facts: list[str] = data.get("facts", []) or []
    aliases_raw: list[str] = data.get("aliases", []) or []

    # Filter aliases to those resembling owner_name (drop other people's names)
    from rapidfuzz import fuzz as _fuzz

    filtered = [
        a
        for a in aliases_raw
        if isinstance(a, str)
        and (
            _fuzz.partial_ratio(a.lower(), owner_name.lower()) >= 60
            or owner_name.lower() in a.lower()
            or a.lower() in owner_name.lower()
        )
    ]
    if owner_name not in filtered:
        filtered.insert(0, owner_name)

    body = "\n".join(f"- {f}" for f in facts) if facts else owner_name
    fm: dict[str, object] = {"type": "person", "is_owner": True, "aliases": filtered}

    from src.vault import writer as vault_writer

    try:
        await vault_writer.write_note("_meta/owner.md", body, fm)
        logger.info("owner.md refined from full dialog", facts=len(facts))
    except Exception as exc:
        logger.warning("owner.md refine failed", error=str(exc))


def _build_language_instruction(lang: str) -> str:
    """Strict language directive injected into prompts that create vault notes.

    Closes Root #3 — vault language inconsistency. With this instruction, LLM
    is forced to use one language for canonical_name/aliases/themes/etc.
    """
    if lang == "ru":
        return (
            "# 🌐 Язык vault: РУССКИЙ\n\n"
            "ВСЕ canonical_name, aliases, themes, body — на русском. "
            "Если в портрете упоминаются английские термины (LegAI, GPT-5.4, "
            "Claude) — оставляй их латиницей как имя собственное. Но имена тем, "
            "места работы, концепты переводи на русский: "
            "❌ `cybersecurity` → ✅ `кибербезопасность`; "
            "❌ `ambition` → ✅ `карьерные амбиции`; "
            "❌ `china` → ✅ `китай`."
        )
    if lang == "en":
        return (
            "# 🌐 Vault language: ENGLISH\n\n"
            "ALL canonical_name, aliases, themes, body — in English. "
            "Russian names of people / cities stay in original (Anya, Tashkent), "
            "but concepts and topics — translate: "
            "❌ `карьерные-амбиции` → ✅ `career-ambition`; "
            "❌ `образование` → ✅ `education`."
        )
    return (
        "# 🌐 Vault language: MIXED\n\n"
        "Допускается смесь языков, но избегай дублирования одной сущности на "
        "разных языках (НЕ создавай и `ai`, и `искусственный-интеллект` как две "
        "темы — выбери одну, остальное в aliases)."
    )


_ONBOARDING_DONE_SENTINEL = "[ONBOARDING_DONE]"
_ONBOARDING_MAX_TURNS = 10  # 2-4 expected, hard cap to prevent runaway


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
    owner_name = str(profile.get("owner_name", "Владелец"))

    # Step 1: write owner.md placeholder FIRST so agent can anchor its tree to it.
    # Facts will be filled in later from the FULL dialog (not just initial portrait).
    from src.vault import writer as vault_writer

    try:
        await vault_writer.write_note(
            "_meta/owner.md",
            owner_name,
            {"type": "person", "is_owner": True, "aliases": [owner_name]},
        )
        # Persist owner_path early so other code can reference it
        from src.session import manager as _sm

        _redis = await _sm.get_redis()
        await _sm.update_profile(
            _redis, user_id, {"owner_path": "_meta/owner.md", "owner_name": owner_name}
        )
    except Exception as exc:
        logger.warning("owner placeholder failed", error=str(exc))

    # Step 2: archive raw portrait
    try:
        await vault_writer.write_note("_meta/portrait.md", portrait, {"type": "inbox"})
    except Exception as exc:
        logger.warning("portrait save failed", error=str(exc))

    # Step 3: build initial agent prompt + inject vault map
    from src.vault.vault_map import build_vault_map

    bot_name = str(profile.get("bot_name", "Ассистент"))
    personality = str(profile.get("personality", ""))
    owner_name = str(profile.get("owner_name", "Владелец"))
    base_system = prompts.render(
        "onboarding",
        bot_name=bot_name,
        personality=personality,
        owner_name=owner_name,
        owner_path="_meta/owner.md",
        vault_language=str(profile.get("vault_language", "mixed")),
    )
    vault_map = build_vault_map()
    lang = str(profile.get("vault_language", "mixed"))
    lang_instruction = _build_language_instruction(lang)
    system = f"{base_system}\n\n---\n\n{lang_instruction}\n\n---\n\n{vault_map}"
    # The first user message is just the raw portrait. The system prompt
    # already explains everything: dialog mode, plain language, when to wrap up.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": portrait},
    ]

    text, is_done = await _run_onboarding_turn(messages)
    await reply_fn(text)

    # Track full dialog for later refine (always append assistant turn)
    messages.append({"role": "assistant", "content": text})

    if is_done:
        # Refine owner.md from FULL dialog (not just initial portrait)
        try:
            await _refine_owner_from_dialog(messages, profile)
        except Exception as exc:
            logger.warning("owner refine after onboarding failed", error=str(exc))
        await _finalize_onboarding()
        return

    # Agent has a clarifying question — save state for follow-up turns
    redis = await session_mgr.get_redis()
    key = session_mgr.key_onboarding(user_id)
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
        await redis.set(key, orjson.dumps({"state": "step_vault_language"}), ex=86400)
        await reply_fn(
            f"Отлично, <b>{owner_name}</b>! Ещё один вопрос — <b>на каком языке "
            "вести твои заметки</b>?\n\n"
            "1 — <b>Русский</b> (имена и заметки на русском)\n"
            "2 — <b>English</b> (everything in English)\n"
            "3 — <b>Mixed</b> (как удобнее в моменте)\n\n"
            "Это важно: чтобы граф не превратился в кашу из разных языков."
        )
        return True

    if step == "step_vault_language":
        raw = content.strip().lower()
        lang_map = {
            "1": "ru",
            "русский": "ru",
            "ru": "ru",
            "rus": "ru",
            "2": "en",
            "english": "en",
            "en": "en",
            "eng": "en",
            "3": "mixed",
            "mixed": "mixed",
            "смешанный": "mixed",
        }
        vault_language = lang_map.get(raw, "mixed")
        await session_mgr.update_profile(
            redis, user_id, {"vault_language": vault_language}
        )
        await redis.set(key, orjson.dumps({"state": "awaiting_portrait"}), ex=86400)
        lang_label = {"ru": "русский", "en": "English", "mixed": "mixed"}[vault_language]
        await reply_fn(
            f"Окей, язык записей: <b>{lang_label}</b>. И последнее — "
            "<b>расскажи о себе</b>:\n\n"
            "Кто ты, чем занимаешься, какие проекты и люди важны, "
            "интересы, ценности, цели. Чем подробнее — тем умнее буду с первого дня."
        )
        return True

    if step == "awaiting_portrait":
        # First portrait — go straight into a discovery dialog. No "plan" step,
        # no confirmation. The agent will ask a couple of clarifying questions,
        # then create the notes silently when it has enough info.
        redis2 = await session_mgr.get_redis()
        profile = await session_mgr.get_profile(redis2, user_id)
        await _run_onboarding_execute(content, profile, reply_fn)
        return True

    if step == "agent_question":
        # Owner is answering the agent's clarifying question — resume the loop
        saved_messages: list[dict[str, Any]] = state.get("messages", [])
        portrait = state.get("portrait", "")
        turn_count = int(state.get("turn_count", 0))

        saved_messages.append({"role": "user", "content": content})

        text, is_done = await _run_onboarding_turn(saved_messages)
        await reply_fn(text)

        # Track full dialog for refinement (always append assistant turn)
        saved_messages.append({"role": "assistant", "content": text})

        if is_done:
            await redis.delete(key)
            try:
                profile = await session_mgr.get_profile(redis, user_id)
                await _refine_owner_from_dialog(saved_messages, profile)
            except Exception as exc:
                logger.warning("owner refine after onboarding failed", error=str(exc))
            await _finalize_onboarding()
            return True

        if turn_count + 1 >= _ONBOARDING_MAX_TURNS:
            # Agent still has questions but we hit the limit — finalize forcibly
            logger.warning("onboarding hit max turns, ending forcibly", user_id=user_id)
            await redis.delete(key)
            await reply_fn("Заверши то что начал — детали можем уточнить позже в обычном диалоге.")
            try:
                profile = await session_mgr.get_profile(redis, user_id)
                await _refine_owner_from_dialog(saved_messages, profile)
            except Exception as exc:
                logger.warning("owner refine after onboarding failed", error=str(exc))
            await _finalize_onboarding()
            return True
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


_COALESCE_DEBOUNCE_SEC = 1.5

# Minimum content length to trigger LightRAG recall — short messages like
# "ок" / "да" don't need context fetched.
_RECALL_MIN_CHARS = 15
_RECALL_MAX_CHARS = 1500  # truncate the recall context injected into the prompt


async def _fetch_recall_context(user_message: str) -> str:
    """Retrieve relevant chunks from LightRAG for this message.

    Returns empty string if message too short, retrieval fails, or vault is
    empty. Never raises — recall is best-effort enrichment.
    """
    if len(user_message) < _RECALL_MIN_CHARS:
        return ""
    try:
        from src.lightrag_svc.client import query as kg_query

        ctx = await kg_query(
            user_message[:500],  # cap query length
            mode="mix",
            only_need_context=True,
            top_k=5,
        )
        if not ctx or not ctx.strip():
            return ""
        if len(ctx) > _RECALL_MAX_CHARS:
            ctx = ctx[:_RECALL_MAX_CHARS] + "\n\n[…recall truncated]"
        return ctx
    except Exception as exc:
        logger.warning("recall fetch failed", error=str(exc))
        return ""


async def _coalesce_text_message(
    user_id: int,
    content: str,
    reply_fn: Callable[[str], Awaitable[None]],
) -> None:
    """Buffer rapid-fire user messages and process them together.

    If the user sends multiple text messages within DEBOUNCE_SEC, only the
    last handler to wake up actually responds — and it sees ALL the messages
    joined as one. Earlier handlers exit silently. This avoids the case where
    the bot starts replying to msg #1 while msg #2 is already in the queue.
    """
    redis = await session_mgr.get_redis()
    buf_key = session_mgr.key_pending_buffer(user_id)
    tok_key = session_mgr.key_pending_token(user_id)

    await redis.rpush(buf_key, content.encode("utf-8"))
    my_token = await redis.incr(tok_key)
    # Token expires together with the buffer — keep state lifetime bounded
    await redis.expire(tok_key, 60)
    await redis.expire(buf_key, 60)

    await asyncio.sleep(_COALESCE_DEBOUNCE_SEC)

    raw_token = await redis.get(tok_key)
    current_token = int(raw_token) if raw_token else 0
    if current_token != my_token:
        return  # a newer message arrived; that handler will drain & reply

    # I'm the last one within the debounce window — drain and process
    raw_items = await redis.lrange(buf_key, 0, -1)
    await redis.delete(buf_key)
    await redis.delete(tok_key)

    if not raw_items:
        return

    combined = "\n".join(item.decode("utf-8") for item in raw_items).strip()
    if not combined:
        return

    await process_input(user_id, combined, reply_fn, kind="text")


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

    # Auto-onboarding gate: if this user has no portrait yet AND no onboarding
    # state, initiate the onboarding flow rather than dropping them into a
    # blank chat with an unprimed agent. Closes the bug where any first
    # message besides /start landed in process_input with empty profile.
    from src.vault import reader as _reader

    if not _reader.note_exists("_meta/portrait.md"):
        existing_state = await redis.get(session_mgr.key_onboarding(user_id))
        if existing_state is None:
            from src.telegram.handlers.commands import _start_onboarding

            logger.info("auto-onboarding triggered (no portrait)", user_id=user_id)

            class _AnswerShim:
                """Adapter so _start_onboarding can use reply_fn instead of message.answer."""

                async def answer(self, text: str, **_kw: Any) -> None:
                    await reply_fn(text)

            await _start_onboarding(user_id, redis, _AnswerShim())  # type: ignore[arg-type]
            return

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

    # Auto-recall: pre-fetch relevant context from LightRAG so the agent ALWAYS
    # sees what the brain already knows about this topic, even if the user
    # didn't ask explicitly. Uses only_need_context=True (no LLM call inside).
    recall_ctx = await _fetch_recall_context(content)

    openai_msgs = agent_loop.build_messages(system_prompt, profile, history, content)
    if recall_ctx:
        # Insert recall as a system message right after the main system prompt
        openai_msgs.insert(
            1,
            {
                "role": "system",
                "content": (
                    "Контекст из твоей долгосрочной памяти (релевантные прошлые "
                    f"заметки):\n\n{recall_ctx}\n\n"
                    "Используй это чтобы не игнорировать прошлый опыт юзера. "
                    "Не цитируй дословно — просто помни."
                ),
            },
        )
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args, session_id=session.session_id)

    reply = await agent_loop.run_chat(openai_msgs, registry.openai_specs(), dispatch)

    # Truncate replies that exceed Telegram's 4096 char limit (BadRequest otherwise)
    if len(reply) > 4000:
        reply = reply[:3950] + "\n\n[…ответ обрезан, слишком длинный для Telegram]"

    # Reply fingerprint — block accidental duplicate sends within last 5 replies
    import hashlib

    fp = hashlib.sha256(reply.encode("utf-8")).hexdigest()[:16].encode()
    fp_key = f"user:reply_fp:{user_id}"
    recent = await redis.lrange(fp_key, 0, 4)
    if fp in recent:
        logger.warning(
            "duplicate reply suppressed",
            user_id=user_id,
            session_id=session.session_id,
            preview=reply[:100],
        )
        return
    await redis.lpush(fp_key, fp)
    await redis.ltrim(fp_key, 0, 4)
    await redis.expire(fp_key, 3600)

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
        await _coalesce_text_message(
            message.from_user.id,
            message.text.strip(),
            message.answer,
        )
