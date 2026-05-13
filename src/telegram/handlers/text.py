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
from src.i18n import t
from src.session import manager as session_mgr
from src.tools.registry import get_registry

logger = structlog.get_logger()
router = Router(name="text")

_PERSONALITY_KEYS = {
    "1": "personality.friendly",
    "2": "personality.direct",
    "3": "personality.sarcastic",
    "4": "personality.mentor",
}


def _resolve_personality(raw_input: str, ui_lang: str) -> str:
    """Map digit 1-4 to localized personality description, or pass through custom text."""
    key = _PERSONALITY_KEYS.get(raw_input.strip())
    return t(key, ui_lang) if key else raw_input.strip()


_OWNER_EXTRACT_PROMPTS = {
    "ru": (
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
    "en": (
        "Analyze the OWNER's portrait and return JSON with two fields:\n"
        '{"facts": ["fact1", "fact2", ...], "aliases": ["Name1", "Name2", ...]}\n\n'
        "facts: 3-7 key facts about the OWNER's own personality. "
        "Only what's explicitly said about themselves. NOT about their "
        "girlfriend, family, projects — only about them.\n\n"
        "aliases: ONLY variants of the owner's NAME or nickname "
        "(full name, surname, nick, English variant). Names of OTHER people "
        "(girlfriend, family, colleagues) NEVER go here. "
        "E.g. if portrait says 'I'm Komron, dating Dasha' — "
        "aliases = ['Komron'], NOT ['Komron', 'Dasha']."
    ),
    "uz": (
        "EGA portretini tahlil qil va ikkita maydonli JSON qaytar:\n"
        '{"facts": ["fakt1", "fakt2", ...], "aliases": ["Ism1", "Ism2", ...]}\n\n'
        "facts: EGA shaxsiyati haqida 3-7 ta asosiy fakt. "
        "Faqat o'zi haqida aniq aytilgan narsa. Qiz do'sti, oilasi, "
        "loyihalari haqida EMAS — faqat egasi haqida.\n\n"
        "aliases: FAQAT egasining ISMI yoki taxallusi variantlari "
        "(to'liq ism, familiya, taxallus, inglizcha variant). BOSHQA "
        "odamlarning (qiz do'sti, oila, hamkasblar) ismlari bu yerga "
        "HECH QACHON kirmaydi. Masalan portretda 'men Komron, qiz "
        "do'stim Dasha' bo'lsa — aliases = ['Komron'], NOT ['Komron', 'Dasha']."
    ),
}

_OWNER_USER_LABEL = {
    "ru": ("Имя владельца", "Портрет"),
    "en": ("Owner name", "Portrait"),
    "uz": ("Ega ismi", "Portret"),
}

_OWNER_REFINE_PROMPTS = {
    "ru": (
        "Проанализируй диалог онбординга и верни JSON с двумя полями: "
        '{"facts": ["факт1", "факт2", ...], "aliases": ["Имя1", ...]}\n\n'
        "facts: 5-10 КЛЮЧЕВЫХ фактов о владельце ИЗ ВСЕГО ДИАЛОГА — "
        "то что юзер реально сказал про себя за все реплики. "
        "Конкретно (даты, названия), а не общё. НЕ про девушку, семью, "
        "проекты по отдельности — только про САМОГО владельца как личность.\n\n"
        "aliases: ТОЛЬКО варианты ИМЕНИ владельца. Имена других людей НЕ ВКЛЮЧАЙ."
    ),
    "en": (
        "Analyze the onboarding dialog and return JSON with two fields: "
        '{"facts": ["fact1", "fact2", ...], "aliases": ["Name1", ...]}\n\n'
        "facts: 5-10 KEY facts about the owner FROM THE WHOLE DIALOG — "
        "what the user actually said about themselves across all turns. "
        "Concrete (dates, names), not generic. NOT about girlfriend, family, "
        "projects separately — only about the OWNER themselves as a person.\n\n"
        "aliases: ONLY variants of the OWNER's name. Names of other people NOT included."
    ),
    "uz": (
        "Onboarding muloqotini tahlil qil va ikkita maydonli JSON qaytar: "
        '{"facts": ["fakt1", "fakt2", ...], "aliases": ["Ism1", ...]}\n\n'
        "facts: BUTUN MULOQOTDAN ega haqida 5-10 ta ASOSIY fakt — "
        "foydalanuvchi barcha replikalarida o'zi haqida aytgan narsalar. "
        "Aniq (sanalar, nomlar), umumiy emas. Qiz do'sti, oila, loyihalar "
        "alohida emas — faqat EGA shaxsiyati haqida.\n\n"
        "aliases: FAQAT EGA ismining variantlari. Boshqa odamlarning ismlari KIRMAYDI."
    ),
}

_OWNER_REFINE_LABEL = {
    "ru": ("Имя владельца", "Полный диалог онбординга"),
    "en": ("Owner name", "Full onboarding dialog"),
    "uz": ("Ega ismi", "To'liq onboarding muloqoti"),
}


# ── onboarding helpers ────────────────────────────────────────────────────────


async def _create_owner_note(
    portrait: str,
    profile: dict[str, object],
) -> None:
    """Create _meta/owner.md — the owner anchor node."""
    owner_name = str(profile.get("owner_name", "Owner"))
    notes_lang = session_mgr.get_notes_language(profile)
    client = agent_loop.get_client()

    sys_prompt = _OWNER_EXTRACT_PROMPTS.get(notes_lang, _OWNER_EXTRACT_PROMPTS["ru"])
    owner_label, portrait_label = _OWNER_USER_LABEL.get(notes_lang, _OWNER_USER_LABEL["ru"])

    # LLM extracts key facts + aliases from portrait
    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": f"{owner_label}: {owner_name}\n\n{portrait_label}:\n{portrait}",
            },
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
    owner_name = str(profile.get("owner_name", "Owner"))
    notes_lang = session_mgr.get_notes_language(profile)
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

    sys_prompt = _OWNER_REFINE_PROMPTS.get(notes_lang, _OWNER_REFINE_PROMPTS["ru"])
    owner_label, dialog_label = _OWNER_REFINE_LABEL.get(notes_lang, _OWNER_REFINE_LABEL["ru"])

    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": f"{owner_label}: {owner_name}\n\n{dialog_label}:\n\n{transcript}",
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


_DIALOG_LANG_DIRECTIVES: dict[str, str] = {
    "ru": (
        "# 💬 ЯЗЫК ОТВЕТА — РУССКИЙ. ЭТО ПРИОРИТЕТ #1.\n\n"
        "ВСЕ твои ответы юзеру — на русском языке. Не на узбекском, не на "
        "английском. На русском.\n\n"
        "Это правило перевешивает ВСЁ остальное:\n"
        "- Если описание твоей личности (`personality`) написано на другом "
        "языке — игнорируй язык personality, бери только смысл, отвечай по-русски.\n"
        "- Если предыдущие сообщения в истории были на другом языке — НЕ "
        "зеркаль их язык. История могла быть до переключения /lang.\n"
        "- Если язык vault (notes_language) отличается — он управляет ТОЛЬКО "
        "содержимым заметок, не диалогом."
    ),
    "en": (
        "# 💬 REPLY LANGUAGE — ENGLISH. PRIORITY #1.\n\n"
        "ALL your replies to the user — in English. Not Uzbek, not Russian. "
        "English.\n\n"
        "This rule overrides everything else:\n"
        "- If the `personality` description is in another language — ignore "
        "the language of personality, take only meaning, reply in English.\n"
        "- If prior messages in history are in another language — do NOT "
        "mirror their language. History may predate a /lang switch.\n"
        "- Vault language (notes_language) controls only note contents, "
        "not dialog."
    ),
    "uz": (
        "# 💬 JAVOB TILI — O'ZBEKCHA (lotin). USTUVORLIK #1.\n\n"
        "Foydalanuvchiga BARCHA javoblaring — o'zbek tilida (lotin yozuvida). "
        "Ruscha emas, inglizcha emas. O'zbekcha.\n\n"
        "Bu qoida hamma narsadan ustun:\n"
        "- Agar shaxsiyat tavsifi (`personality`) boshqa tilda bo'lsa — "
        "tilini e'tiborga olma, faqat ma'nosini ol, o'zbekcha javob ber.\n"
        "- Agar tarixda oldingi xabarlar boshqa tilda bo'lsa — ularning "
        "tilini takrorlama. Tarix /lang almashtirishdan oldin bo'lishi mumkin.\n"
        "- Vault tili (notes_language) faqat yozuvlar mazmunini boshqaradi, "
        "muloqotni emas."
    ),
}


_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "ru": (
        "# 🌐 Язык vault: РУССКИЙ\n\n"
        "ВСЕ canonical_name, aliases, themes, body — на русском. "
        "Если упоминаются английские термины (LegAI, GPT-5.4, Claude) — "
        "оставляй их латиницей как имя собственное. Но имена тем, места "
        "работы, концепты переводи на русский: "
        "❌ `cybersecurity` → ✅ `кибербезопасность`; "
        "❌ `ambition` → ✅ `карьерные амбиции`; "
        "❌ `china` → ✅ `китай`."
    ),
    "en": (
        "# 🌐 Vault language: ENGLISH\n\n"
        "ALL canonical_name, aliases, themes, body — in English. "
        "Russian/Uzbek names of people and cities stay in original (Anya, "
        "Toshkent), but concepts and topics — translate: "
        "❌ `карьерные-амбиции` → ✅ `career-ambition`; "
        "❌ `образование` → ✅ `education`."
    ),
    "uz": (
        "# 🌐 Vault tili: O'ZBEKCHA (lotin)\n\n"
        "BARCHA canonical_name, aliases, themes, body — o'zbek tilida (lotin "
        "yozuvida). Boshqa tildagi atoqli otlar (LegAI, GPT-5.4, Anya) "
        "asl shaklida qoldiriladi, lekin tushuncha va mavzular tarjima "
        "qilinadi: "
        "❌ `cybersecurity` → ✅ `kiberxavfsizlik`; "
        "❌ `образование` → ✅ `ta'lim`."
    ),
}


def _build_language_instruction(lang: str) -> str:
    """Strict language directive injected into prompts that create vault notes."""
    return _LANGUAGE_INSTRUCTIONS.get(lang, _LANGUAGE_INSTRUCTIONS["ru"])


_ONBOARDING_DONE_SENTINEL = "[ONBOARDING_DONE]"
_ONBOARDING_MAX_TURNS = 10  # 2-4 expected, hard cap to prevent runaway


_ONBOARDING_SESSION_ID = "onboarding"

# Loop detector: how similar two assistant questions must be (token-set ratio,
# 0..100) before we conclude the agent is asking the same thing repeatedly.
# 70 catches reorderings/paraphrases like
#   "Где сейчас учишься или работаешь?" vs
#   "Сейчас ты учишься или работаешь — расскажи?"
# without firing on legitimately different follow-ups.
_ONBOARDING_LOOP_SIM_THRESHOLD = 70
# Need at least this many assistant turns before we can detect a loop —
# fewer than 3 doesn't give us a comparable pair.
_ONBOARDING_LOOP_MIN_ASSISTANT_TURNS = 3


def _is_onboarding_looping(saved_messages: list[dict[str, Any]]) -> bool:
    """Detect whether onboarding is stuck asking the same question(s) again.

    Compares the last assistant message to the previous few via rapidfuzz
    token-set ratio (word-order-insensitive). If similarity is high, the agent
    has not progressed since the prior turn — force-ending is preferable to
    looping forever.
    """
    assistant_texts = [
        str(m.get("content") or "").strip()
        for m in saved_messages
        if m.get("role") == "assistant"
    ]
    assistant_texts = [t for t in assistant_texts if t]
    if len(assistant_texts) < _ONBOARDING_LOOP_MIN_ASSISTANT_TURNS:
        return False

    from rapidfuzz import fuzz as _fuzz

    last = assistant_texts[-1]
    for older in assistant_texts[-4:-1]:
        # Combine two metrics: token_set_ratio handles word-reordering (LLM
        # paraphrasing); partial_ratio handles substring overlap (LLM rephrasing
        # but reusing whole clauses). Either crossing the threshold counts.
        sim = max(
            _fuzz.token_set_ratio(last, older),
            _fuzz.partial_ratio(last, older),
        )
        if sim >= _ONBOARDING_LOOP_SIM_THRESHOLD:
            return True
    return False


def _build_onboarding_system_prompt(profile: dict[str, object]) -> str:
    """Render the onboarding system prompt from the *current* profile state.

    Called on every turn — not cached in onboarding state — so a `/lang` switch
    mid-onboarding is honored on the very next user reply. Closes the bug
    where switching ui_language during onboarding had no effect because the
    initial Uzbek/Russian/English prompt was stored verbatim in Redis.
    """
    from src.vault.vault_map import build_vault_map

    bot_name = str(profile.get("bot_name", "Mnemo"))
    personality = str(profile.get("personality", ""))
    owner_name = str(profile.get("owner_name", "Owner"))
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)

    base_system = prompts.render(
        "onboarding",
        lang=ui_lang,
        bot_name=bot_name,
        personality=personality,
        owner_name=owner_name,
        owner_path="_meta/owner.md",
        vault_language=notes_lang,
    )
    vault_map = build_vault_map()
    lang_instruction = _build_language_instruction(notes_lang)
    dialog_directive = _DIALOG_LANG_DIRECTIVES.get(ui_lang, _DIALOG_LANG_DIRECTIVES["ru"])
    return (
        f"{base_system}\n\n---\n\n{dialog_directive}\n\n---\n\n"
        f"{lang_instruction}\n\n---\n\n{vault_map}"
    )


async def _run_onboarding_turn(
    messages: list[dict[str, Any]],
    stream: _StreamUI | None = None,
) -> tuple[str, bool]:
    """Run one turn of the onboarding agent loop.

    Returns (assistant_text, is_done). is_done is True when the agent emitted
    the [ONBOARDING_DONE] sentinel — meaning the initial graph is built.

    Tool dispatch passes session_id="onboarding" so create_note (and other
    tools) can read filled slots from `slot:filled:onboarding` and enforce the
    proper-noun preservation guard (M4).

    When `stream` is provided, the LLM's content tokens are progressively
    edited into the placeholder bubble — same UX as normal chat.
    """
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args, session_id=_ONBOARDING_SESSION_ID)

    on_text_cb = stream.on_text if stream else None
    on_tool_cb = stream.on_tool_start if stream else None

    result = await agent_loop.run_chat(
        messages,
        registry.openai_specs(),
        dispatch,
        max_rounds=30,
        on_text=on_text_cb,
        on_tool_start=on_tool_cb,
        session_id=_ONBOARDING_SESSION_ID,
    )
    is_done = _ONBOARDING_DONE_SENTINEL in result
    text = result.replace(_ONBOARDING_DONE_SENTINEL, "").strip()
    return text, is_done


async def _seal_onboarding_transcript(
    saved_messages: list[dict[str, Any]],
    profile: dict[str, object],
) -> None:
    """Write the onboarding dialog to the transcript layer.

    Onboarding doesn't go through extractor.run_pipeline, so without this hook
    the literal log of the first conversation is lost. The transcript is the
    only place the user's exact words survive long-term.

    Idempotent on session_id="onboarding_{user_id}_{date}" — re-running an
    onboarding overwrites the file (rare, intentional).
    """
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from src.session.manager import SessionMessage, get_notes_language
    from src.vault import transcripts

    user_id = int(str(profile.get("user_id", settings.allowed_user_ids[0])))
    tz = ZoneInfo(settings.tz)
    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    session_id = f"onboarding_{user_id}_{date_str}"
    notes_lang = get_notes_language(profile)

    # Convert dict-format saved_messages to SessionMessage. Timestamps are not
    # tracked per-turn in onboarding state, so we stamp them as `now` — the
    # ORDER is the load-bearing part, not the precise wall-clock time.
    now = datetime.now(UTC)
    msgs: list[SessionMessage] = []
    for m in saved_messages:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        msgs.append(SessionMessage(role=role, content=content, ts=now))  # type: ignore[arg-type]

    if not msgs:
        return

    try:
        await transcripts.seal_session(session_id, msgs, lang=notes_lang)
    except Exception as exc:
        logger.warning("onboarding transcript seal failed", error=str(exc))


async def _finalize_onboarding(user_id: int = 0, ui_lang: str = "ru") -> None:
    """Bootstrap default scheduled tasks + reveal the main reply keyboard.

    `user_id`/`ui_lang` default to safe values so legacy callers still work,
    but new callers should pass them so the user sees `kb.activated` + the
    persistent keyboard at the end of onboarding — that's the natural
    moment to expose the buttons, before they start chatting freely.
    """
    try:
        from src.scheduler.defaults import bootstrap_defaults

        bootstrap_defaults()
        logger.info("default tasks bootstrapped after onboarding")
    except Exception as exc:
        logger.warning("default tasks bootstrap failed", error=str(exc))

    if not user_id:
        return
    try:
        from src.telegram.bot import get_bot
        from src.telegram.keyboards import main_reply_keyboard

        await get_bot().send_message(
            user_id,
            t("kb.activated", ui_lang),
            reply_markup=main_reply_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.warning("reveal main keyboard failed", user_id=user_id, error=str(exc))


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

    # Step 3: build initial agent prompt — extracted into a helper so we can
    # re-render it on every turn (honoring live /lang switches).
    ui_lang = session_mgr.get_ui_language(profile)
    system = _build_onboarding_system_prompt(profile)

    # The first user message is just the raw portrait. The system prompt
    # already explains everything: dialog mode, plain language, when to wrap up.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": portrait},
    ]

    stream = await _make_stream_ui(user_id, ui_lang)
    text, is_done = await _run_onboarding_turn(messages, stream=stream)
    if stream is not None:
        await stream.finalize(text)
    else:
        await reply_fn(text)

    # Track full dialog for later refine (always append assistant turn)
    messages.append({"role": "assistant", "content": text})

    if is_done:
        # Refine owner.md from FULL dialog (not just initial portrait)
        try:
            await _refine_owner_from_dialog(messages, profile)
        except Exception as exc:
            logger.warning("owner refine after onboarding failed", error=str(exc))
        await _seal_onboarding_transcript(messages, profile)
        await _finalize_onboarding(user_id=user_id, ui_lang=ui_lang)
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
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    if step == "step_ui_language":
        # Waiting on inline-keyboard callback. Ignore plain-text input here so a
        # user typing "русский" instead of pressing the button doesn't get stuck.
        await reply_fn(t("start.pick_ui_language", "ru"))
        return True

    if step == "step_bot_name":
        bot_name = content.strip() or "Mnemo"
        await session_mgr.update_profile(redis, user_id, {"bot_name": bot_name})
        await redis.set(key, orjson.dumps({"state": "step_personality"}), ex=86400)
        await reply_fn(t("onboarding.ask_personality", ui_lang, bot_name=bot_name))
        return True

    if step == "step_personality":
        personality = _resolve_personality(content, ui_lang)
        await session_mgr.update_profile(redis, user_id, {"personality": personality})
        await redis.set(key, orjson.dumps({"state": "step_owner_name"}), ex=86400)
        await reply_fn(t("onboarding.ask_owner_name", ui_lang))
        return True

    if step == "step_owner_name":
        owner_name = content.strip() or "Owner"
        await session_mgr.update_profile(
            redis, user_id, {"owner_name": owner_name, "user_id": user_id}
        )
        await redis.set(key, orjson.dumps({"state": "step_notes_language"}), ex=86400)
        # Send the question via direct bot call to attach an inline keyboard
        from src.telegram.bot import get_bot
        from src.telegram.handlers.commands import _ui_lang_keyboard

        bot = get_bot()
        await bot.send_message(
            user_id,
            t("onboarding.ask_notes_language", ui_lang, owner_name=owner_name),
            reply_markup=_ui_lang_keyboard("onboard_notes_lang"),
        )
        return True

    if step == "step_notes_language":
        # Same waiting-on-callback pattern as step_ui_language
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

        # Refresh the system prompt on every turn so /lang switches mid-
        # onboarding take effect, and so any profile-derived state (personality,
        # owner_name) updates flow into the LLM context.
        if saved_messages and saved_messages[0].get("role") == "system":
            saved_messages[0] = {
                "role": "system",
                "content": _build_onboarding_system_prompt(profile),
            }

        # If the agent registered a pending slot before asking, bind the user's
        # literal answer to it. This is what prevents tokens like "БЕК" from
        # being normalized away by the LLM on this turn.
        slot_note = await _maybe_consume_slot(
            user_id, _ONBOARDING_SESSION_ID, content, ui_lang
        )

        saved_messages.append({"role": "user", "content": content})
        if slot_note:
            saved_messages.append({"role": "system", "content": slot_note})

        stream = await _make_stream_ui(user_id, ui_lang)
        text, is_done = await _run_onboarding_turn(saved_messages, stream=stream)
        if stream is not None:
            await stream.finalize(text)
        else:
            await reply_fn(text)

        # Track full dialog for refinement (always append assistant turn)
        saved_messages.append({"role": "assistant", "content": text})

        # Loop detector: if the agent keeps repeating the same question, the
        # user is stuck. Force-end through the normal done-path so all the
        # side effects (owner refine, transcript seal, defaults bootstrap)
        # still happen — just earlier than [ONBOARDING_DONE] would have.
        looping = not is_done and _is_onboarding_looping(saved_messages)
        if looping:
            logger.warning(
                "onboarding loop detected — force-ending",
                user_id=user_id,
                turn_count=turn_count + 1,
            )
            await reply_fn(t("onboarding.loop_force_end", ui_lang))

        if is_done or looping:
            await redis.delete(key)
            try:
                profile = await session_mgr.get_profile(redis, user_id)
                await _refine_owner_from_dialog(saved_messages, profile)
            except Exception as exc:
                logger.warning("owner refine after onboarding failed", error=str(exc))
            await _seal_onboarding_transcript(saved_messages, profile)
            await _finalize_onboarding(user_id=user_id, ui_lang=ui_lang)
            return True

        if turn_count + 1 >= _ONBOARDING_MAX_TURNS:
            # Agent still has questions but we hit the limit — finalize forcibly
            logger.warning("onboarding hit max turns, ending forcibly", user_id=user_id)
            await redis.delete(key)
            await reply_fn(t("onboarding.forced_end", ui_lang))
            try:
                profile = await session_mgr.get_profile(redis, user_id)
                await _refine_owner_from_dialog(saved_messages, profile)
            except Exception as exc:
                logger.warning("owner refine after onboarding failed", error=str(exc))
            await _seal_onboarding_transcript(saved_messages, profile)
            await _finalize_onboarding(user_id=user_id, ui_lang=ui_lang)
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


# ── streaming UI ──────────────────────────────────────────────────────────────


# Cadence of edit_message_text calls during stream. Telegram allows ~1/sec per
# chat reliably; we go a bit slower to leave headroom for rate-limit jitter.
_STREAM_EDIT_MIN_INTERVAL_SEC = 0.9
# Don't edit until the buffer grew by at least this many chars — prevents
# wasting an edit on a single-token delta.
_STREAM_EDIT_MIN_DELTA_CHARS = 25

_TOOL_PROGRESS_LABELS: dict[str, dict[str, str]] = {
    "ru": {
        # Recall-family: bot is "remembering" — it's its OWN memory, not a search engine
        "recall": "💭 проверяю память…",
        "search_existing_entities": "💭 вспоминаю…",
        "search_notes": "💭 вспоминаю…",
        "read_note": "💭 вспоминаю…",
        # Write-family: bot is "memorising", not "filing"
        "create_note": "✍️ запоминаю…",
        "append_to_note": "✍️ дополняю…",
        "update_frontmatter": "✍️ обновляю…",
        # Graph reasoning — "thinking" beats "querying"
        "kg_query": "💭 думаю…",
        "kg_get_entity": "💭 думаю…",
        # External / misc
        "fetch_url": "🌐 читаю ссылку…",
        "set_pending_slot": "👀 уточняю…",
        "get_user_profile": "💭 проверяю профиль…",
        "_default": "✨ думаю…",
    },
    "en": {
        "recall": "💭 checking memory…",
        "search_existing_entities": "💭 recalling…",
        "search_notes": "💭 recalling…",
        "read_note": "💭 recalling…",
        "create_note": "✍️ remembering…",
        "append_to_note": "✍️ adding to memory…",
        "update_frontmatter": "✍️ updating…",
        "kg_query": "💭 thinking…",
        "kg_get_entity": "💭 thinking…",
        "fetch_url": "🌐 reading the link…",
        "set_pending_slot": "👀 clarifying…",
        "get_user_profile": "💭 checking profile…",
        "_default": "✨ thinking…",
    },
    "uz": {
        "recall": "💭 xotirani tekshiryapman…",
        "search_existing_entities": "💭 eslayapman…",
        "search_notes": "💭 eslayapman…",
        "read_note": "💭 eslayapman…",
        "create_note": "✍️ yodlayapman…",
        "append_to_note": "✍️ qo'shyapman…",
        "update_frontmatter": "✍️ yangilayapman…",
        "kg_query": "💭 o'ylayapman…",
        "kg_get_entity": "💭 o'ylayapman…",
        "fetch_url": "🌐 havolani o'qiyapman…",
        "set_pending_slot": "👀 aniqlashtiryapman…",
        "get_user_profile": "💭 profilni tekshiryapman…",
        "_default": "✨ o'ylayapman…",
    },
}

_THINKING_PLACEHOLDER: dict[str, str] = {
    "ru": "…",
    "en": "…",
    "uz": "…",
}


class _StreamUI:
    """Edits a single Telegram message progressively as the LLM streams tokens.

    Lifecycle:
        - `_make_stream_ui` sends the placeholder bubble and returns this object.
        - `on_tool_start(name)` swaps text to a localized "📌 действие…" label.
        - `on_text(full)` debounce-edits with the growing accumulator.
        - `finalize(text)` writes the final reply; returns False if Telegram
          refused (rare — caller falls back to fresh send_message).
    """

    def __init__(self, bot: object, chat_id: int, message_id: int, ui_lang: str) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id
        self._ui_lang = ui_lang
        self._last_edit_at: float = 0.0
        self._last_text: str = ""
        self._labels = _TOOL_PROGRESS_LABELS.get(ui_lang, _TOOL_PROGRESS_LABELS["ru"])

    async def on_text(self, full_text: str) -> None:
        import time

        if not full_text:
            return
        now = time.monotonic()
        grew_by = len(full_text) - len(self._last_text)
        elapsed = now - self._last_edit_at
        if elapsed < _STREAM_EDIT_MIN_INTERVAL_SEC and grew_by < _STREAM_EDIT_MIN_DELTA_CHARS:
            return
        await self._edit(full_text)

    async def on_tool_start(self, tool_name: str) -> None:
        label = self._labels.get(tool_name, self._labels["_default"])
        await self._edit(label, force=True)

    async def finalize(self, final_text: str) -> bool:
        """Land the final reply. Returns True if edit succeeded, False if a
        fresh message is needed (caller's fallback)."""
        if not final_text:
            return True
        ok = await self._edit(final_text, force=True)
        logger.info(
            "stream finalize",
            message_id=self._message_id,
            chars=len(final_text),
            success=ok,
        )
        return ok

    async def _edit(self, text: str, *, force: bool = False) -> bool:
        import time

        from src.telegram.formatting import to_telegram_html

        truncated = text if len(text) < 4000 else text[:3950] + "…"
        if not force and truncated == self._last_text:
            return True
        # Convert LLM-emitted Markdown to Telegram HTML — bot is created with
        # parse_mode=HTML, raw **bold** would render literally.
        rendered = to_telegram_html(truncated)
        try:
            await self._bot.edit_message_text(  # type: ignore[attr-defined]
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=rendered,
            )
            self._last_text = truncated
            self._last_edit_at = time.monotonic()
            return True
        except Exception as exc:
            msg = str(exc)
            # "message is not modified" — content identical to prior edit; benign.
            if "not modified" in msg.lower():
                return True
            # Rate limit / transient — next edit will catch up; not fatal.
            # Promoted from debug → info while we diagnose streaming UX.
            logger.info("stream edit failed", error=msg[:200])
            return False


async def _make_stream_ui(user_id: int, ui_lang: str) -> _StreamUI | None:
    """Send the placeholder bubble and return a streaming UI handle.

    Returns None on failure — caller falls back to non-streaming send path.
    """
    try:
        from src.telegram.bot import get_bot

        bot = get_bot()
        placeholder = _THINKING_PLACEHOLDER.get(ui_lang, _THINKING_PLACEHOLDER["ru"])
        sent = await bot.send_message(user_id, placeholder)
        logger.info(
            "stream ui ready",
            user_id=user_id,
            message_id=sent.message_id,
            ui_lang=ui_lang,
        )
        return _StreamUI(bot, user_id, sent.message_id, ui_lang)
    except Exception as exc:
        logger.warning("stream ui setup failed", user_id=user_id, error=str(exc))
        return None


# ── slot binding helper ───────────────────────────────────────────────────────


async def _maybe_consume_slot(
    user_id: int,
    session_id: str,
    user_message: str,
    ui_lang: str,
) -> str:
    """If a pending slot exists, fill it with the literal user reply.

    Returns a system-message body to inject into the next LLM call (so the
    agent sees "user just answered X verbatim"), or "" if no slot was pending.
    Failures here never break the chat — slot binding is best-effort enrichment.
    """
    try:
        from src.session import slots
        from src.session.manager import get_redis

        redis = await get_redis()
        filled = await slots.consume_pending(redis, user_id, session_id, user_message)
        if filled is None:
            return ""
        return slots.format_filled_for_prompt(filled, lang=ui_lang)
    except Exception as exc:
        logger.warning("slot consume failed", error=str(exc), user_id=user_id)
        return ""


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

    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)

    try:
        shifted, new_topic = await detect(session, history, content, notes_lang=notes_lang)
    except Exception as exc:
        logger.warning("topic shift check failed", error=str(exc))
        return session

    if not shifted:
        return session

    await reply_fn(t("save.shifting", ui_lang))
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

_RECALL_HEADERS = {
    "ru": "Контекст из твоей долгосрочной памяти (релевантные прошлые заметки):",
    "en": "Context from your long-term memory (relevant past notes):",
    "uz": "Uzoq xotirangdan kontekst (tegishli oldingi yozuvlar):",
}
_RECALL_FOOTERS = {
    "ru": (
        "Используй это чтобы не игнорировать прошлый опыт юзера. "
        "Не цитируй дословно — просто помни."
    ),
    "en": (
        "Use this so you don't ignore the user's prior experience. "
        "Don't quote verbatim — just remember."
    ),
    "uz": (
        "Bundan foydalanib foydalanuvchining oldingi tajribasini "
        "e'tiborsiz qoldirma. So'zma-so'z keltirma — shunchaki esda tut."
    ),
}
_TRUNCATE_HINTS = {
    "ru": "[…ответ обрезан, слишком длинный для Telegram]",
    "en": "[…reply truncated, too long for Telegram]",
    "uz": "[…javob qisqartirildi, Telegram uchun juda uzun]",
}


async def _fetch_recall_context(user_message: str) -> str:
    """Retrieve relevant chunks from LightRAG for this message.

    Intentionally unbounded — Mnemo's core promise is that the bot never
    forgets, so we wait however long LightRAG needs to return a complete
    result. Perceived latency is hidden via streaming UI (handler level), NOT
    via timeout-skip of memory operations. See `feedback_memory_over_speed`.
    """
    if len(user_message) < _RECALL_MIN_CHARS:
        return ""
    try:
        from src.lightrag_svc.client import query as kg_query

        ctx = await kg_query(
            user_message[:500],
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
    # Settings input gate — must run BEFORE onboarding so the user can edit
    # their name/personality even when an onboarding-state ghost exists.
    if kind == "text":
        from src.telegram.bot import get_bot
        from src.telegram.handlers.settings import try_consume_text_input

        try:
            if await try_consume_text_input(user_id, content, get_bot()):
                return
        except Exception as exc:
            logger.warning("settings text-input consumer failed", error=str(exc))

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

    # Kick off auto-recall as soon as we have `content` — it's independent of
    # session state and can run concurrently with the Redis-bound topic-shift /
    # push / touch sequence below. We await it just before the LLM call.
    recall_task: asyncio.Task[str] | None = None
    if kind == "text":
        recall_task = asyncio.create_task(_fetch_recall_context(content))

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
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)

    history = [{"role": m.role, "content": m.content} for m in history_msgs[-30:]]

    bot_name = str(profile.get("bot_name", "Mnemo"))
    personality = str(profile.get("personality", ""))
    # Agent SPEAKS in ui_language. Notes are still written in notes_language —
    # the lang_instruction block below pins canonical_name/aliases/body to
    # notes_lang regardless of dialog language.
    system_prompt = prompts.render(
        "system", lang=ui_lang, bot_name=bot_name, personality=personality
    )
    # Dialog language directive: pins the bot's REPLY language even when
    # the conversation history is in a different language (e.g. after /lang
    # switch from uz → ru, the prior msgs are still Uzbek and the LLM
    # otherwise mirrors them). Notes-language directive controls vault content,
    # which is independent.
    dialog_directive = _DIALOG_LANG_DIRECTIVES.get(ui_lang, _DIALOG_LANG_DIRECTIVES["ru"])
    notes_directive = _LANGUAGE_INSTRUCTIONS.get(notes_lang, _LANGUAGE_INSTRUCTIONS["ru"])
    system_prompt = f"{system_prompt}\n\n---\n\n{dialog_directive}\n\n---\n\n{notes_directive}"

    # Auto-recall: await the task we kicked off at function start. By now the
    # LightRAG roundtrip has been running concurrently with all Redis-bound
    # work above, so total latency is max(recall, session_setup) instead of
    # their sum. Bounded by _RECALL_TIMEOUT_SEC inside _fetch_recall_context.
    recall_ctx = await recall_task if recall_task is not None else ""

    # Slot binding: if the agent registered a pending slot before its last
    # message, the literal user reply gets recorded and a SLOT_FILLED note is
    # injected into this turn's context. See src/session/slots.py.
    slot_note = await _maybe_consume_slot(user_id, session.session_id, content, ui_lang)

    openai_msgs = agent_loop.build_messages(system_prompt, profile, history, content)
    if slot_note:
        openai_msgs.insert(1, {"role": "system", "content": slot_note})
    if recall_ctx:
        # Recall header in ui_language (the agent's working language)
        recall_header = _RECALL_HEADERS.get(ui_lang, _RECALL_HEADERS["ru"])
        recall_footer = _RECALL_FOOTERS.get(ui_lang, _RECALL_FOOTERS["ru"])
        openai_msgs.insert(
            1,
            {
                "role": "system",
                "content": f"{recall_header}\n\n{recall_ctx}\n\n{recall_footer}",
            },
        )
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args, session_id=session.session_id)

    # Streaming setup (text-mode only): send a placeholder bubble, edit it as
    # tokens arrive. Hides total latency from the user — they see the bot
    # "thinking" + writing rather than a single long wait. Voice/image keep
    # the simple final-send path.
    stream = await _make_stream_ui(user_id, ui_lang) if kind == "text" else None

    on_text_cb = stream.on_text if stream else None
    on_tool_cb = stream.on_tool_start if stream else None

    reply = await agent_loop.run_chat(
        openai_msgs,
        registry.openai_specs(),
        dispatch,
        on_text=on_text_cb,
        on_tool_start=on_tool_cb,
        session_id=session.session_id,
    )

    # Truncate replies that exceed Telegram's 4096 char limit (BadRequest otherwise)
    if len(reply) > 4000:
        hint = _TRUNCATE_HINTS.get(ui_lang, _TRUNCATE_HINTS["ru"])
        reply = reply[:3950] + f"\n\n{hint}"

    # Reply fingerprint — block accidental duplicate sends within last 5 replies
    import hashlib

    fp = hashlib.sha256(reply.encode("utf-8")).hexdigest()[:16].encode()
    fp_key = f"user:reply_fp:{user_id}"
    recent = await redis.lrange(fp_key, 0, 4)
    is_duplicate = fp in recent
    if is_duplicate:
        logger.warning(
            "duplicate reply suppressed",
            user_id=user_id,
            session_id=session.session_id,
            preview=reply[:100],
        )
    else:
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

    # Deliver the final reply. Streaming path edits the placeholder in place;
    # non-streaming path sends a fresh message via reply_fn.
    if stream is not None:
        delivered = await stream.finalize(reply)
        if not delivered:
            await reply_fn(reply)
    elif not is_duplicate:
        await reply_fn(reply)

    logger.info("replied", user_id=user_id, session_id=session.session_id, kind=kind)


@router.message()
async def handle_text(message: Message) -> None:
    if not message.text or not message.from_user:
        return
    from src.telegram.formatting import to_telegram_html
    from src.telegram.keyboards import match_main_kb_button

    # Reply-keyboard buttons send their localized label as plain text.
    # Route to the matching command handler before the agent loop sees the
    # message — otherwise the LLM would treat "💾 Запомнить" as user content.
    button_cmd = match_main_kb_button(message.text)
    if button_cmd is not None:
        from src.telegram.handlers.commands import cmd_save, cmd_start, cmd_undo
        from src.telegram.handlers.settings import cmd_settings

        dispatch = {
            "save": cmd_save,
            "undo": cmd_undo,
            "settings": cmd_settings,
            "start": cmd_start,
        }
        handler = dispatch.get(button_cmd)
        if handler is not None:
            await handler(message)
            return

    async def reply_fn(text: str) -> None:
        """Wrap every reply through Markdown→HTML so LLM-emitted **bold**
        renders correctly under the bot's HTML parse mode."""
        await message.answer(to_telegram_html(text))

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.from_user.id):
        await _coalesce_text_message(
            message.from_user.id,
            message.text.strip(),
            reply_fn,
        )
