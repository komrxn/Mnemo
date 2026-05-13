"""/settings command + inline-keyboard menu — editable single-message UI.

Architecture
------------
One sent message becomes the persistent settings panel. Every callback edits
it in place (text + keyboard), so the user has a single chat bubble to
navigate through name / personality / languages. Closing deletes the message.

State machine
-------------
The "menu" (callback-driven) is stateless on the server: each callback
re-renders from `user:profile:{user_id}` Redis hash, which is the single
source of truth.

When the user picks "Имя" or "Свой стиль" we need to wait for the next text
message. That's persisted as `user:settings_state:{user_id}` with the menu's
chat_id/message_id so the text-input handler ([handlers/text.py]) can edit
the same menu back when the user is done.

Callback prefixes (all under `settings:` so handle_text's gate can route):
    settings:main                          → render main menu
    settings:close                         → delete menu
    settings:name                          → render "send a new name" view
    settings:personality                   → render personality submenu
    settings:p_set:<preset_key>            → save preset, back to main
    settings:p_custom                      → render "send custom personality" view
    settings:langs                         → render languages submenu
    settings:lang_pick:<target>            → render UI/notes lang picker (ui|notes)
    settings:lang_set:<target>:<lang>      → save lang, back to main

Personality presets are i18n keys under `personality.*` (already used in
onboarding). The custom branch routes through the text-input gate and the
existing `_maybe_retranslate_personality` is NOT triggered — user's input is
already in their chosen language.
"""

from __future__ import annotations

import html
from typing import Any

import orjson
import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.i18n import LANGUAGES, t
from src.session import manager as session_mgr

logger = structlog.get_logger()
router = Router(name="settings")


# ── public constants ──────────────────────────────────────────────────────────

PERSONALITY_PRESETS: tuple[str, ...] = ("friendly", "direct", "sarcastic", "mentor")
NAME_MIN_LEN = 1
NAME_MAX_LEN = 30
PERSONALITY_MIN_LEN = 5
PERSONALITY_MAX_LEN = 300
# A single appended rule is shorter — "no emojis" is fine; "5 paragraph manifesto"
# is not. Cap small so accumulated rules don't bloat the system prompt.
RULE_MIN_LEN = 3
RULE_MAX_LEN = 200
# Total personality (base + appended rules) cap. Once we cross this, we still
# allow it but log a warning — eventually we'll need a "consolidate rules" UI.
PERSONALITY_HARD_CAP = 800


# ── synthesis (gpt-5.4-mini, low temperature, anti-отсебятина) ────────────────


_SYNTH_FULL_PROMPT = {
    "ru": (
        "Ты оформляешь инструкцию характера для AI-бота по имени {bot_name}.\n"
        'Юзер описал как хочет чтоб {bot_name} с ним общался: "{raw}"\n\n'
        "Сформулируй короткую конкретную инструкцию для системного промпта — "
        "2-4 строки. Императив, без воды.\n\n"
        "ЖЁСТКИЕ ПРАВИЛА:\n"
        "- НЕ добавляй ничего чего юзер не сказал.\n"
        "- НЕ выдумывай детали (фразы-маркеры, голос, особенные ритуалы).\n"
        "- Если юзер не упомянул аспект — не пиши про него.\n"
        "- Без шаблонов («адаптивный», «поддерживающий», «эмпатичный»).\n"
        "- Конкретика: тон, длина ответов, эмоции, юмор, форма обращения.\n\n"
        "Верни ТОЛЬКО инструкцию, без кавычек, без объяснений, без префиксов."
    ),
    "en": (
        "You are drafting a character instruction for an AI bot named {bot_name}.\n"
        'User described how they want {bot_name} to talk to them: "{raw}"\n\n'
        "Compose a short concrete instruction for the system prompt — 2-4 lines. "
        "Imperative, no fluff.\n\n"
        "HARD RULES:\n"
        "- Do NOT add anything the user didn't say.\n"
        "- Do NOT invent details (catchphrases, voice, special rituals).\n"
        "- If user didn't mention an aspect — don't mention it.\n"
        "- No template adjectives (\"adaptive\", \"supportive\", \"empathetic\").\n"
        "- Concrete: tone, reply length, emotion, humor, address form.\n\n"
        "Return ONLY the instruction, no quotes, no explanation, no prefixes."
    ),
    "uz": (
        "Sen {bot_name} ismli AI-bot uchun xarakter ko'rsatmasini tuzayapsan.\n"
        'Foydalanuvchi {bot_name} qanday gaplashishini tasvirladi: "{raw}"\n\n'
        "Tizim promtsi uchun qisqa va aniq ko'rsatma yoz — 2-4 qator. "
        "Buyruq shaklida, ortiqcha gapsiz.\n\n"
        "QAT'IY QOIDALAR:\n"
        "- Foydalanuvchi aytmagan narsani QO'SHMA.\n"
        "- Tafsilotlarni o'ylab topMA (so'z-belgilar, ovoz, marosimlar).\n"
        "- Foydalanuvchi tilga olmagan jihatni yozMA.\n"
        "- Shablon sifatlardan voz kech (\"moslashuvchan\", \"qo'llab-quvvatlovchi\").\n"
        "- Aniq narsalar: ohang, javob uzunligi, his-tuyg'u, hazil, murojaat shakli.\n\n"
        "FAQAT ko'rsatmani qaytar, tirnoqlarsiz, izohsiz, prefikssiz."
    ),
}

_SYNTH_RULE_PROMPT = {
    "ru": (
        "Юзер хочет добавить ОДНО правило к стилю общения бота {bot_name}.\n"
        'Текущий стиль: "{current}"\n'
        'Новое требование юзера: "{raw}"\n\n'
        "Сформулируй ОДНО короткое правило-инструкцию в повелительном "
        "наклонении (1-2 строки).\n\n"
        "ЖЁСТКИЕ ПРАВИЛА:\n"
        "- НЕ повторяй то что уже есть в текущем стиле.\n"
        "- НЕ добавляй детали которых юзер не упомянул.\n"
        "- Если требование противоречит существующему — сформулируй "
        "  как override («теперь без эмодзи»).\n"
        "- Конкретный императив: «не используй X», «обращайся как Y», «избегай Z».\n\n"
        "Верни ТОЛЬКО правило, одной фразой, без кавычек, без объяснений."
    ),
    "en": (
        "User wants to add ONE rule to bot {bot_name}'s communication style.\n"
        'Current style: "{current}"\n'
        'User\'s new request: "{raw}"\n\n'
        "Compose ONE short imperative rule (1-2 lines).\n\n"
        "HARD RULES:\n"
        "- Do NOT repeat what's already in the current style.\n"
        "- Do NOT add details the user didn't mention.\n"
        "- If the request contradicts existing style — phrase as an override "
        "  (\"now without emojis\").\n"
        "- Concrete imperative: \"don't use X\", \"address as Y\", \"avoid Z\".\n\n"
        "Return ONLY the rule, one phrase, no quotes, no explanation."
    ),
    "uz": (
        "Foydalanuvchi {bot_name} bot uslubiga BITTA qoida qo'shmoqchi.\n"
        'Hozirgi uslub: "{current}"\n'
        'Foydalanuvchining yangi talabi: "{raw}"\n\n'
        "BITTA qisqa buyruq qoidasini tuz (1-2 qator).\n\n"
        "QAT'IY QOIDALAR:\n"
        "- Hozirgi uslubda allaqachon borini takrorlamA.\n"
        "- Foydalanuvchi aytmagan tafsilotlarni qo'shMA.\n"
        "- Agar talab mavjud uslubga zid kelsa — bekor qiluvchi qoida "
        "  qilib yoz (\"endi smayliksiz\").\n"
        "- Aniq buyruq: \"X ishlatma\", \"Y deb murojaat qil\", \"Z dan saqlan\".\n\n"
        "FAQAT qoidani qaytar, bitta ibora, tirnoqlarsiz, izohsiz."
    ),
}


async def _call_synth(system_prompt: str, raw: str) -> str:
    """Single mini-LLM call for personality synthesis. Low temperature to
    minimize отсебятина. Raises on failure — caller decides fallback policy.
    """
    from src.agent import loop as agent_loop
    from src.config import settings

    client = agent_loop.get_client()
    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw},
        ],
        temperature=0.3,
        max_completion_tokens=200,
    )
    text = (resp.choices[0].message.content or "").strip()
    # The model sometimes wraps despite the instruction.
    text = text.strip("\"'`").strip()
    return text


async def _synthesize_full_personality(
    raw: str, bot_name: str, ui_lang: str
) -> str | None:
    """Take a free-form user description and return a clean personality
    instruction. Returns None on any failure — caller falls back to `raw`.
    """
    try:
        system = _SYNTH_FULL_PROMPT.get(ui_lang, _SYNTH_FULL_PROMPT["ru"]).format(
            bot_name=bot_name, raw=raw
        )
        synth = await _call_synth(system, raw)
        if not synth or len(synth) < 10:
            logger.warning(
                "personality synth produced too-short output",
                ui_lang=ui_lang,
                len=len(synth),
            )
            return None
        return synth
    except Exception as exc:
        logger.warning("personality synth failed", error=str(exc))
        return None


async def _synthesize_rule(
    raw: str, current_personality: str, bot_name: str, ui_lang: str
) -> str | None:
    """Synthesize a single appended rule, aware of what's already in the
    current style so it doesn't duplicate. Returns None on failure."""
    try:
        system = _SYNTH_RULE_PROMPT.get(ui_lang, _SYNTH_RULE_PROMPT["ru"]).format(
            bot_name=bot_name,
            current=current_personality or "(пусто)",
            raw=raw,
        )
        synth = await _call_synth(system, raw)
        if not synth or len(synth) < 3:
            logger.warning("rule synth produced empty output", ui_lang=ui_lang)
            return None
        return synth
    except Exception as exc:
        logger.warning("rule synth failed", error=str(exc))
        return None


def _append_rule(current: str, rule: str) -> str:
    """Append `rule` to `current` personality, normalizing spacing/punctuation.

    Rules are joined by ". " for readability. If `current` already ends with
    a sentence terminator, we don't double up.
    """
    cur = (current or "").strip().rstrip(".!?")
    r = rule.strip().rstrip(".!?")
    if not cur:
        return r + "."
    return f"{cur}. {r}."


# ── view-state helpers ────────────────────────────────────────────────────────


def _personality_preview(personality: str, ui_lang: str, *, limit: int = 60) -> str:
    """Render a current personality value compactly for the menu header.

    Empty → italic "default" marker. Long values are truncated with an ellipsis
    so the menu fits on one screen. User-supplied content is HTML-escaped —
    the bot sends with parse_mode=HTML, and a stray `<` would break parsing.
    """
    text = (personality or "").strip()
    if not text:
        return t("settings.personality_value_empty", ui_lang)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return html.escape(text, quote=False)


async def _menu_payload(
    redis: object, user_id: int
) -> tuple[str, InlineKeyboardMarkup, str]:
    """Build the main-menu text + keyboard from the *current* profile.

    Returns (rendered_html, keyboard, ui_lang). Called by every callback that
    lands on the main menu (open / back / save).
    """
    profile = await session_mgr.get_profile(redis, user_id)  # type: ignore[arg-type]
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)
    bot_name = str(profile.get("bot_name") or "Mnemo")
    personality = str(profile.get("personality") or "")

    text = t(
        "settings.menu",
        ui_lang,
        name=html.escape(bot_name, quote=False),
        personality=_personality_preview(personality, ui_lang),
        ui_lang=t(f"language_label.{ui_lang}", ui_lang),
        notes_lang=t(f"language_label.{notes_lang}", ui_lang),
    )
    return text, _main_keyboard(ui_lang), ui_lang


def _main_keyboard(ui_lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings.btn_name", ui_lang), callback_data="settings:name"
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_personality", ui_lang),
                    callback_data="settings:personality",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_languages", ui_lang),
                    callback_data="settings:langs",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_close", ui_lang),
                    callback_data="settings:close",
                )
            ],
        ]
    )


def _back_only_keyboard(ui_lang: str) -> InlineKeyboardMarkup:
    """Single 'Back to main settings' button — used on text-input views."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings.btn_back", ui_lang), callback_data="settings:main"
                )
            ]
        ]
    )


def _personality_keyboard(ui_lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(f"settings.btn_personality_{key}", ui_lang),
                    callback_data=f"settings:p_set:{key}",
                )
            ]
            for key in PERSONALITY_PRESETS
        ]
        + [
            [
                InlineKeyboardButton(
                    text=t("settings.btn_personality_custom", ui_lang),
                    callback_data="settings:p_custom",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_personality_add_rule", ui_lang),
                    callback_data="settings:p_add_rule",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_back", ui_lang),
                    callback_data="settings:main",
                )
            ],
        ]
    )


def _synth_preview_keyboard(ui_lang: str, action: str) -> InlineKeyboardMarkup:
    """Save / Rewrite / Back buttons under a synth preview.

    `action` is "synth" (full personality replace) or "rule" (append). The
    callback dispatcher reads action from callback_data and applies the right
    persistence path. Back returns to the personality submenu.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings.btn_synth_save", ui_lang),
                    callback_data=f"settings:p_save_{action}",
                ),
                InlineKeyboardButton(
                    text=t("settings.btn_synth_rewrite", ui_lang),
                    callback_data=f"settings:p_rewrite_{action}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_back", ui_lang),
                    callback_data="settings:personality",
                )
            ],
        ]
    )


def _languages_keyboard(ui_lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("settings.btn_lang_ui", ui_lang),
                    callback_data="settings:lang_pick:ui",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_lang_notes", ui_lang),
                    callback_data="settings:lang_pick:notes",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("settings.btn_back", ui_lang),
                    callback_data="settings:main",
                )
            ],
        ]
    )


def _lang_picker_keyboard(target: str) -> InlineKeyboardMarkup:
    """3-flag picker — saves directly to profile on click."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"settings:lang_set:{target}:{lang}")]
            for lang, label in (
                ("ru", "🇷🇺 Русский"),
                ("en", "🇬🇧 English"),
                ("uz", "🇺🇿 O'zbekcha"),
            )
        ]
        + [
            [
                InlineKeyboardButton(
                    text="↩️",
                    callback_data="settings:langs",
                )
            ]
        ]
    )


# ── settings-state Redis helpers ──────────────────────────────────────────────


async def set_awaiting_state(
    redis: object,
    user_id: int,
    *,
    awaiting: str,
    menu_chat_id: int,
    menu_message_id: int,
) -> None:
    """Mark that we expect the user's next text message to be a settings input."""
    payload = {
        "awaiting": awaiting,
        "menu_chat_id": menu_chat_id,
        "menu_message_id": menu_message_id,
    }
    await redis.set(  # type: ignore[attr-defined]
        session_mgr.key_settings_state(user_id),
        orjson.dumps(payload),
        ex=session_mgr._SETTINGS_STATE_TTL,
    )


async def get_awaiting_state(redis: object, user_id: int) -> dict[str, Any] | None:
    raw = await redis.get(session_mgr.key_settings_state(user_id))  # type: ignore[attr-defined]
    if raw is None:
        return None
    try:
        return orjson.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None


async def clear_awaiting_state(redis: object, user_id: int) -> None:
    await redis.delete(session_mgr.key_settings_state(user_id))  # type: ignore[attr-defined]


# ── /settings command ─────────────────────────────────────────────────────────


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    redis = await session_mgr.get_redis()
    # If a prior settings-input was pending, clear it — the user explicitly
    # restarted the flow, that stale state would shadow real messages later.
    await clear_awaiting_state(redis, user_id)

    text, kb, _ = await _menu_payload(redis, user_id)
    await message.answer(text, reply_markup=kb)


# ── navigation: main / close / back ───────────────────────────────────────────


@router.callback_query(F.data == "settings:main")
async def handle_settings_main(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    redis = await session_mgr.get_redis()
    await clear_awaiting_state(redis, callback.from_user.id)

    text, kb, _ = await _menu_payload(redis, callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:
        logger.debug("settings main edit failed", error=str(exc))
    await callback.answer()


@router.callback_query(F.data == "settings:close")
async def handle_settings_close(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    redis = await session_mgr.get_redis()
    await clear_awaiting_state(redis, callback.from_user.id)

    profile = await session_mgr.get_profile(redis, callback.from_user.id)
    ui_lang = session_mgr.get_ui_language(profile)
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer(t("settings.closed", ui_lang))


# ── name flow ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "settings:name")
async def handle_settings_name(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    bot_name = str(profile.get("bot_name") or "Mnemo")

    try:
        await callback.message.edit_text(
            t("settings.name_view", ui_lang, name=html.escape(bot_name, quote=False)),
            reply_markup=_back_only_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.debug("settings name view edit failed", error=str(exc))

    await set_awaiting_state(
        redis,
        user_id,
        awaiting="name",
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )
    await callback.answer()


# ── personality flow ──────────────────────────────────────────────────────────


@router.callback_query(F.data == "settings:personality")
async def handle_settings_personality(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    # Personality menu is purely navigational; if a prior name-input was
    # awaiting, abandon it.
    await clear_awaiting_state(redis, user_id)
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    personality = str(profile.get("personality") or "")

    try:
        await callback.message.edit_text(
            t(
                "settings.personality_view",
                ui_lang,
                current=_personality_preview(personality, ui_lang, limit=120),
            ),
            reply_markup=_personality_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.debug("settings personality view edit failed", error=str(exc))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:p_set:"))
async def handle_settings_p_set(callback: CallbackQuery) -> None:
    """Save one of the preset personalities."""
    if not callback.from_user or not callback.data or not isinstance(callback.message, Message):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    preset_key = parts[2]
    if preset_key not in PERSONALITY_PRESETS:
        return

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    # Preset value is stored in the user's UI language — that's the language
    # they're navigating settings in, and matches what they see on the button.
    new_personality = t(f"personality.{preset_key}", ui_lang)
    await session_mgr.update_profile(redis, user_id, {"personality": new_personality})

    text, kb, _ = await _menu_payload(redis, user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:
        logger.debug("settings p_set edit failed", error=str(exc))
    await callback.answer(t("settings.personality_saved", ui_lang))
    logger.info("personality set (preset)", user_id=user_id, preset=preset_key)


@router.callback_query(F.data == "settings:p_custom")
async def handle_settings_p_custom(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    personality = str(profile.get("personality") or "")

    try:
        await callback.message.edit_text(
            t(
                "settings.personality_custom_view",
                ui_lang,
                current=_personality_preview(personality, ui_lang, limit=160),
            ),
            reply_markup=_back_only_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.debug("settings p_custom view edit failed", error=str(exc))

    await set_awaiting_state(
        redis,
        user_id,
        awaiting="personality",
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data == "settings:p_add_rule")
async def handle_settings_p_add_rule(callback: CallbackQuery) -> None:
    """Enter the 'append a rule' text-input flow."""
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    personality = str(profile.get("personality") or "")

    try:
        await callback.message.edit_text(
            t(
                "settings.add_rule_view",
                ui_lang,
                current=_personality_preview(personality, ui_lang, limit=200),
            ),
            reply_markup=_back_only_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.debug("settings p_add_rule view edit failed", error=str(exc))

    await set_awaiting_state(
        redis,
        user_id,
        awaiting="add_rule",
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )
    await callback.answer()


# ── synth preview confirm/rewrite callbacks ──────────────────────────────────


@router.callback_query(F.data == "settings:p_save_synth")
async def handle_settings_p_save_synth(callback: CallbackQuery) -> None:
    """Save the synthesized full personality (replaces existing)."""
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    state = await get_awaiting_state(redis, user_id)
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    if state is None or state.get("awaiting") != "preview_synth":
        # State expired or stale — bail to main menu.
        text, kb, _ = await _menu_payload(redis, user_id)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    draft = str(state.get("draft") or "").strip()
    if not draft:
        await callback.answer()
        return

    await session_mgr.update_profile(redis, user_id, {"personality": draft})
    await clear_awaiting_state(redis, user_id)

    text, kb, _ = await _menu_payload(redis, user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:
        logger.debug("settings p_save_synth edit failed", error=str(exc))
    await callback.answer(t("settings.personality_saved", ui_lang))
    logger.info("personality saved (synth)", user_id=user_id)


@router.callback_query(F.data == "settings:p_rewrite_synth")
async def handle_settings_p_rewrite_synth(callback: CallbackQuery) -> None:
    """User rejected the synth preview — let them write again."""
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    personality = str(profile.get("personality") or "")

    try:
        await callback.message.edit_text(
            t(
                "settings.personality_custom_view",
                ui_lang,
                current=_personality_preview(personality, ui_lang, limit=160),
            ),
            reply_markup=_back_only_keyboard(ui_lang),
        )
    except Exception:
        pass

    # Re-enter awaiting-personality state, dropping any draft.
    await set_awaiting_state(
        redis,
        user_id,
        awaiting="personality",
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data == "settings:p_save_rule")
async def handle_settings_p_save_rule(callback: CallbackQuery) -> None:
    """Append the synthesized rule to existing personality."""
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    state = await get_awaiting_state(redis, user_id)
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    if state is None or state.get("awaiting") != "preview_rule":
        text, kb, _ = await _menu_payload(redis, user_id)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    draft_rule = str(state.get("draft") or "").strip()
    if not draft_rule:
        await callback.answer()
        return

    current = str(profile.get("personality") or "")
    new_personality = _append_rule(current, draft_rule)
    if len(new_personality) > PERSONALITY_HARD_CAP:
        logger.warning(
            "personality length over soft cap",
            user_id=user_id,
            length=len(new_personality),
        )
    await session_mgr.update_profile(redis, user_id, {"personality": new_personality})
    await clear_awaiting_state(redis, user_id)

    text, kb, _ = await _menu_payload(redis, user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:
        logger.debug("settings p_save_rule edit failed", error=str(exc))
    await callback.answer(t("settings.rule_added", ui_lang))
    logger.info("personality rule appended", user_id=user_id)


@router.callback_query(F.data == "settings:p_rewrite_rule")
async def handle_settings_p_rewrite_rule(callback: CallbackQuery) -> None:
    """User wants to re-write the rule — back to add_rule_view."""
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    personality = str(profile.get("personality") or "")

    try:
        await callback.message.edit_text(
            t(
                "settings.add_rule_view",
                ui_lang,
                current=_personality_preview(personality, ui_lang, limit=200),
            ),
            reply_markup=_back_only_keyboard(ui_lang),
        )
    except Exception:
        pass

    await set_awaiting_state(
        redis,
        user_id,
        awaiting="add_rule",
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )
    await callback.answer()


# ── languages flow ────────────────────────────────────────────────────────────


@router.callback_query(F.data == "settings:langs")
async def handle_settings_langs(callback: CallbackQuery) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        return
    redis = await session_mgr.get_redis()
    await clear_awaiting_state(redis, callback.from_user.id)
    profile = await session_mgr.get_profile(redis, callback.from_user.id)
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)

    try:
        await callback.message.edit_text(
            t(
                "settings.languages_view",
                ui_lang,
                ui_lang=t(f"language_label.{ui_lang}", ui_lang),
                notes_lang=t(f"language_label.{notes_lang}", ui_lang),
            ),
            reply_markup=_languages_keyboard(ui_lang),
        )
    except Exception as exc:
        logger.debug("settings langs view edit failed", error=str(exc))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:lang_pick:"))
async def handle_settings_lang_pick(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not isinstance(callback.message, Message):
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    target = parts[2]
    if target not in ("ui", "notes"):
        return

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    prompt_key = "settings.pick_ui_lang" if target == "ui" else "settings.pick_notes_lang"
    try:
        await callback.message.edit_text(
            t(prompt_key, ui_lang),
            reply_markup=_lang_picker_keyboard(target),
        )
    except Exception as exc:
        logger.debug("settings lang_pick edit failed", error=str(exc))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:lang_set:"))
async def handle_settings_lang_set(callback: CallbackQuery) -> None:
    """Persist the chosen language, then re-render the main menu.

    For UI-language changes we also re-translate the stored `personality`
    (cf. `_maybe_retranslate_personality` in onboarding flow): the personality
    string is injected into every system prompt; if its language doesn't
    match the dialog, the LLM mirrors the wrong language.
    """
    if not callback.from_user or not callback.data or not isinstance(callback.message, Message):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    _, _, target, new_lang = parts
    if target not in ("ui", "notes") or new_lang not in LANGUAGES:
        return

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()

    field = "ui_language" if target == "ui" else "notes_language"
    await session_mgr.update_profile(redis, user_id, {field: new_lang})

    if target == "ui":
        # Reuse the helper from commands.py (avoid duplicating the prompt
        # logic). Imported lazily to dodge a circular import.
        from src.telegram.handlers.commands import _maybe_retranslate_personality

        profile = await session_mgr.get_profile(redis, user_id)
        await _maybe_retranslate_personality(redis, user_id, profile, new_lang)

    text, kb, ui_lang = await _menu_payload(redis, user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as exc:
        logger.debug("settings lang_set edit failed", error=str(exc))
    key = "lang.ui_updated" if target == "ui" else "lang.notes_updated"
    label = t(f"language_label.{new_lang}", ui_lang)
    await callback.answer(t(key, ui_lang, name=label))
    logger.info("language updated via settings", user_id=user_id, target=target, lang=new_lang)


# ── text-input consumer (called from handlers/text.py before onboarding) ──────


async def try_consume_text_input(
    user_id: int,
    content: str,
    bot: object,
) -> bool:
    """If a settings-input is awaited, route the message through the right
    flow and update the menu accordingly. Returns True iff consumed.

    Branches:
        awaiting=name         → validate length → save → menu
        awaiting=personality  → validate → synthesize → preview menu (no save yet)
        awaiting=add_rule     → validate → synthesize rule → preview menu (no save yet)

    The synth/preview branches are the "anti-отсебятина" UX: the LLM-cleaned
    draft is shown to the user; saving happens only on Yes confirm (separate
    callback). On synth failure we fall back to user's raw input with a
    warning preview.
    """
    redis = await session_mgr.get_redis()
    state = await get_awaiting_state(redis, user_id)
    if state is None:
        return False

    awaiting = str(state.get("awaiting") or "")
    menu_chat_id = state.get("menu_chat_id")
    menu_message_id = state.get("menu_message_id")

    # `preview_*` states are button-only — if the user types while in them,
    # let the text flow naturally to normal chat, keep the preview alive.
    if awaiting in {"preview_synth", "preview_rule"}:
        return False

    if (
        awaiting not in ("name", "personality", "add_rule")
        or not menu_chat_id
        or not menu_message_id
    ):
        # Unknown / corrupt — clean up so a future legit message isn't shadowed.
        await clear_awaiting_state(redis, user_id)
        return False

    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    bot_name = str(profile.get("bot_name") or "Mnemo")
    cleaned = content.strip()

    if awaiting == "name":
        return await _handle_name_input(
            bot, redis, user_id, ui_lang, cleaned, menu_chat_id, menu_message_id
        )

    if awaiting == "personality":
        return await _handle_personality_input(
            bot, redis, user_id, ui_lang, bot_name, cleaned, menu_chat_id, menu_message_id
        )

    # awaiting == "add_rule"
    current_personality = str(profile.get("personality") or "")
    return await _handle_add_rule_input(
        bot,
        redis,
        user_id,
        ui_lang,
        bot_name,
        current_personality,
        cleaned,
        menu_chat_id,
        menu_message_id,
    )


async def _handle_name_input(
    bot: object,
    redis: object,
    user_id: int,
    ui_lang: str,
    cleaned: str,
    menu_chat_id: int,
    menu_message_id: int,
) -> bool:
    """Simple direct flow — name has no synthesis step."""
    if len(cleaned) < NAME_MIN_LEN:
        await bot.send_message(user_id, t("settings.name_too_short", ui_lang))  # type: ignore[attr-defined]
        return True
    if len(cleaned) > NAME_MAX_LEN:
        await bot.send_message(user_id, t("settings.name_too_long", ui_lang))  # type: ignore[attr-defined]
        return True
    await session_mgr.update_profile(redis, user_id, {"bot_name": cleaned})  # type: ignore[arg-type]
    await clear_awaiting_state(redis, user_id)
    text, kb, _ = await _menu_payload(redis, user_id)
    try:
        await bot.edit_message_text(  # type: ignore[attr-defined]
            chat_id=menu_chat_id,
            message_id=menu_message_id,
            text=text,
            reply_markup=kb,
        )
    except Exception as exc:
        logger.debug("settings name save edit failed", error=str(exc))
    await bot.send_message(  # type: ignore[attr-defined]
        user_id, t("settings.name_saved", ui_lang, name=html.escape(cleaned, quote=False))
    )
    logger.info("bot_name set via settings", user_id=user_id, name=cleaned)
    return True


async def _handle_personality_input(
    bot: object,
    redis: object,
    user_id: int,
    ui_lang: str,
    bot_name: str,
    cleaned: str,
    menu_chat_id: int,
    menu_message_id: int,
) -> bool:
    """Synthesize a clean personality from raw input, show preview, await confirm."""
    if len(cleaned) < PERSONALITY_MIN_LEN:
        await bot.send_message(user_id, t("settings.personality_too_short", ui_lang))  # type: ignore[attr-defined]
        return True
    if len(cleaned) > PERSONALITY_MAX_LEN:
        await bot.send_message(user_id, t("settings.personality_too_long", ui_lang))  # type: ignore[attr-defined]
        return True

    synth = await _synthesize_full_personality(cleaned, bot_name, ui_lang)
    if synth is None:
        draft = cleaned
        preview_text = t(
            "settings.personality_synth_failed",
            ui_lang,
            raw=html.escape(cleaned, quote=False),
        )
    else:
        draft = synth
        preview_text = t(
            "settings.personality_synth_preview",
            ui_lang,
            synthesized=html.escape(synth, quote=False),
        )

    # Persist draft in state; the save-confirm callback reads it.
    payload = {
        "awaiting": "preview_synth",
        "menu_chat_id": menu_chat_id,
        "menu_message_id": menu_message_id,
        "draft": draft,
    }
    await redis.set(  # type: ignore[attr-defined]
        session_mgr.key_settings_state(user_id),
        orjson.dumps(payload),
        ex=session_mgr._SETTINGS_STATE_TTL,
    )

    try:
        await bot.edit_message_text(  # type: ignore[attr-defined]
            chat_id=menu_chat_id,
            message_id=menu_message_id,
            text=preview_text,
            reply_markup=_synth_preview_keyboard(ui_lang, "synth"),
        )
    except Exception as exc:
        logger.debug("settings personality preview edit failed", error=str(exc))
    return True


async def _handle_add_rule_input(
    bot: object,
    redis: object,
    user_id: int,
    ui_lang: str,
    bot_name: str,
    current_personality: str,
    cleaned: str,
    menu_chat_id: int,
    menu_message_id: int,
) -> bool:
    """Synthesize a single appended rule, show preview, await confirm."""
    if len(cleaned) < RULE_MIN_LEN:
        await bot.send_message(user_id, t("settings.personality_too_short", ui_lang))  # type: ignore[attr-defined]
        return True
    if len(cleaned) > RULE_MAX_LEN:
        await bot.send_message(user_id, t("settings.personality_too_long", ui_lang))  # type: ignore[attr-defined]
        return True

    synth = await _synthesize_rule(cleaned, current_personality, bot_name, ui_lang)
    if synth is None:
        draft_rule = cleaned
        updated = _append_rule(current_personality, draft_rule)
        preview_text = (
            t("settings.add_rule_failed", ui_lang)
            + "\n\n"
            + t(
                "settings.add_rule_preview",
                ui_lang,
                rule=html.escape(draft_rule, quote=False),
                updated=html.escape(updated, quote=False),
            )
        )
    else:
        draft_rule = synth
        updated = _append_rule(current_personality, draft_rule)
        preview_text = t(
            "settings.add_rule_preview",
            ui_lang,
            rule=html.escape(draft_rule, quote=False),
            updated=html.escape(updated, quote=False),
        )

    payload = {
        "awaiting": "preview_rule",
        "menu_chat_id": menu_chat_id,
        "menu_message_id": menu_message_id,
        "draft": draft_rule,
    }
    await redis.set(  # type: ignore[attr-defined]
        session_mgr.key_settings_state(user_id),
        orjson.dumps(payload),
        ex=session_mgr._SETTINGS_STATE_TTL,
    )

    try:
        await bot.edit_message_text(  # type: ignore[attr-defined]
            chat_id=menu_chat_id,
            message_id=menu_message_id,
            text=preview_text,
            reply_markup=_synth_preview_keyboard(ui_lang, "rule"),
        )
    except Exception as exc:
        logger.debug("settings add_rule preview edit failed", error=str(exc))
    return True
