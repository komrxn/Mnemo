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
                    text=t("settings.btn_back", ui_lang),
                    callback_data="settings:main",
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
                current=_personality_preview(personality, ui_lang, limit=120),
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
    """If a settings-input is awaited, validate & save & re-render the menu.

    Returns True iff the message was consumed by settings (caller skips normal
    text flow). On validation failure, sends a short ephemeral error and keeps
    the awaiting state alive so the user can retry without re-navigating.
    """
    redis = await session_mgr.get_redis()
    state = await get_awaiting_state(redis, user_id)
    if state is None:
        return False

    awaiting = str(state.get("awaiting") or "")
    menu_chat_id = state.get("menu_chat_id")
    menu_message_id = state.get("menu_message_id")
    if awaiting not in ("name", "personality") or not menu_chat_id or not menu_message_id:
        await clear_awaiting_state(redis, user_id)
        return False

    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    cleaned = content.strip()

    if awaiting == "name":
        if len(cleaned) < NAME_MIN_LEN:
            await bot.send_message(user_id, t("settings.name_too_short", ui_lang))  # type: ignore[attr-defined]
            return True
        if len(cleaned) > NAME_MAX_LEN:
            await bot.send_message(user_id, t("settings.name_too_long", ui_lang))  # type: ignore[attr-defined]
            return True
        await session_mgr.update_profile(redis, user_id, {"bot_name": cleaned})
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

    # awaiting == "personality"
    if len(cleaned) < PERSONALITY_MIN_LEN:
        await bot.send_message(user_id, t("settings.personality_too_short", ui_lang))  # type: ignore[attr-defined]
        return True
    if len(cleaned) > PERSONALITY_MAX_LEN:
        await bot.send_message(user_id, t("settings.personality_too_long", ui_lang))  # type: ignore[attr-defined]
        return True
    await session_mgr.update_profile(redis, user_id, {"personality": cleaned})
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
        logger.debug("settings personality save edit failed", error=str(exc))
    await bot.send_message(user_id, t("settings.personality_saved", ui_lang))  # type: ignore[attr-defined]
    logger.info("personality set (custom) via settings", user_id=user_id)
    return True
