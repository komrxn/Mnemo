from __future__ import annotations

import orjson
import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.i18n import LANGUAGES, t
from src.session import manager as session_mgr
from src.vault import reader

logger = structlog.get_logger()
router = Router(name="commands")


def _ui_lang_keyboard(callback_prefix: str) -> InlineKeyboardMarkup:
    """Inline-keyboard with three flag-emoji buttons for language pick."""
    buttons = [
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{callback_prefix}:ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data=f"{callback_prefix}:en"),
        InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data=f"{callback_prefix}:uz"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[b] for b in buttons])


async def _start_onboarding(user_id: int, redis, message) -> None:  # type: ignore[no-untyped-def]
    """Initiate the onboarding flow.

    The very first step now is picking the UI language — without it we can't
    even ask the next question in the right language. After ui_language is
    chosen via inline keyboard, the regular onboarding (bot name, style,
    owner name, notes language, portrait) starts.
    """
    await redis.set(
        session_mgr.key_onboarding(user_id),
        orjson.dumps({"state": "step_ui_language"}),
        ex=86400,
    )
    # No locale-aware text yet — show the universal greeting (RU/EN/UZ inline)
    await message.answer(
        t("start.pick_ui_language", "ru"),
        reply_markup=_ui_lang_keyboard("onboard_ui_lang"),
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    logger.info("start", user_id=user_id)

    redis = await session_mgr.get_redis()

    if reader.note_exists("_meta/portrait.md") and reader.note_exists("_meta/owner.md"):
        profile = await session_mgr.get_profile(redis, user_id)
        lang = session_mgr.get_ui_language(profile)
        # Re-attach the persistent reply keyboard — `/start` is the user's
        # natural recovery path if they ever lost it.
        from src.telegram.keyboards import main_reply_keyboard

        await message.answer(
            t("start.already_onboarded", lang),
            reply_markup=main_reply_keyboard(lang),
        )
        return

    await redis.delete(session_mgr.key_onboarding(user_id))
    await _start_onboarding(user_id, redis, message)


@router.callback_query(F.data.startswith("onboard_ui_lang:"))
async def handle_onboard_ui_lang(callback: CallbackQuery) -> None:
    """Phase 1 of onboarding: user picked UI language via inline keyboard."""
    if not callback.from_user or not callback.data:
        return
    _, lang = callback.data.split(":", 1)
    if lang not in LANGUAGES:
        return

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()

    await session_mgr.update_profile(redis, user_id, {"ui_language": lang})
    await redis.set(
        session_mgr.key_onboarding(user_id),
        orjson.dumps({"state": "step_bot_name"}),
        ex=86400,
    )

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(t("start.ui_language_set", lang))
    await callback.answer()


@router.callback_query(F.data.startswith("onboard_notes_lang:"))
async def handle_onboard_notes_lang(callback: CallbackQuery) -> None:
    """Onboarding step: user picked notes language via inline keyboard."""
    if not callback.from_user or not callback.data:
        return
    _, lang = callback.data.split(":", 1)
    if lang not in LANGUAGES:
        return

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()

    await session_mgr.update_profile(redis, user_id, {"notes_language": lang})
    await redis.set(
        session_mgr.key_onboarding(user_id),
        orjson.dumps({"state": "awaiting_portrait"}),
        ex=86400,
    )

    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    label = t(f"language_label.{lang}", ui_lang)

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(t("onboarding.ask_portrait", ui_lang, lang_label=label))
    await callback.answer()


async def _do_save(user_id: int, send_message: Message) -> None:
    """Core /save logic. Takes a Message to reply against (so both the slash-
    command path and the kb-confirm callback path can share this body).

    `send_message.answer` posts in the chat — `from_user` is *not* read here,
    which is what lets us call this from a callback context where the
    underlying message belongs to the bot, not the user.
    """
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    session = await session_mgr.close_session(redis, user_id)

    if session is None:
        await send_message.answer(t("save.no_active_session", ui_lang))
        return

    msgs = await session_mgr.get_msgs(redis, session.session_id)

    user_msgs = [m for m in msgs if m.role == "user"]
    if len(user_msgs) < 1:
        await send_message.answer(t("save.empty_session", ui_lang))
        return

    await send_message.answer(t("save.processing", ui_lang))

    from src.agent.extractor import run_pipeline

    async def notify(text: str) -> None:
        await send_message.answer(text)

    try:
        summary = await run_pipeline(session, msgs, notify)
        await send_message.answer(summary)
        logger.info("save complete", session_id=session.session_id, user_id=user_id)
    except Exception as exc:
        logger.error("save failed", session_id=session.session_id, error=str(exc))
        await send_message.answer(t("save.failed", ui_lang, error=str(exc)))


@router.message(Command("save"))
async def cmd_save(message: Message) -> None:
    if not message.from_user:
        return
    await _do_save(message.from_user.id, message)


async def _do_undo(user_id: int, send_message: Message) -> None:
    """Core /undo logic — same split as `_do_save` so the kb-confirm
    callback can reuse it."""
    from pathlib import Path

    from src.config import settings
    from src.vault import git_ops

    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    vault = Path(settings.vault_path)
    try:
        last_files = await git_ops._run(vault, "diff", "--name-only", "HEAD~1", "HEAD")
    except Exception:
        last_files = ""
    onboarding_markers = ("_meta/owner.md", "_meta/portrait.md")
    if any(marker in last_files for marker in onboarding_markers):
        await send_message.answer(t("undo.refuse_onboarding", ui_lang))
        return

    result = await git_ops.revert_head(vault)
    await send_message.answer(result)
    logger.info("undo", result=result, user_id=user_id)


@router.message(Command("undo"))
async def cmd_undo(message: Message) -> None:
    """Revert last vault commit — but refuse if it touched onboarding files."""
    if not message.from_user:
        return
    await _do_undo(message.from_user.id, message)


# ── kb-confirm callback (triggered from warning messages on save/undo taps) ──


@router.callback_query(F.data.startswith("kb_confirm:"))
async def handle_kb_confirm(callback: CallbackQuery) -> None:
    """Two-step confirmation for destructive reply-keyboard buttons.

    Flow: user taps "💾 Запомнить" or "⚠️ Отменить" → `handle_text` sends a
    warning message with this keyboard → user picks Yes/No → we either run
    the action (and delete the warning) or just delete + toast "cancelled".

    Callback data: `kb_confirm:<action>:<yes|no>`. Action ∈ {save, undo}.
    Any unknown action is silently ignored so a stale button from an old
    deploy can't crash us.
    """
    if not callback.from_user or not callback.data:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, action, answer = parts

    user_id = callback.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    # Drop the warning bubble immediately — either we run the action and its
    # own messages take over, or the user explicitly cancelled.
    if isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except Exception:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    if answer == "no":
        await callback.answer(t("kb.confirm_cancelled", ui_lang))
        return

    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    await callback.answer()
    if action == "save":
        await _do_save(user_id, callback.message)
    elif action == "undo":
        await _do_undo(user_id, callback.message)
    # else: unknown action, silently ignore (we already deleted the bubble)


# /lang command was removed in favor of /settings → Languages submenu.
# The personality re-translate helper below remains — it's still called by
# the settings flow on UI-language change (see handlers/settings.py).


_PERSONALITY_TRANSLATE_PROMPT: dict[str, str] = {
    "ru": (
        "Переведи короткое описание стиля общения бота на русский. "
        "Сохрани смысл и тон, оставь компактным (1-2 строки). "
        "Верни ТОЛЬКО переведённый текст — без кавычек, без объяснений, "
        "без markdown, без префиксов вроде 'Перевод:'."
    ),
    "en": (
        "Translate this short bot communication-style description to English. "
        "Keep meaning and tone, keep it compact (1-2 lines). "
        "Return ONLY the translated text — no quotes, no explanation, no "
        "markdown, no prefixes like 'Translation:'."
    ),
    "uz": (
        "Botning muloqot uslubining qisqa tavsifini o'zbek tiliga (lotin yozuvi) "
        "tarjima qil. Ma'no va ohangni saqla, qisqa qoldir (1-2 qator). "
        "FAQAT tarjima qilingan matnni qaytar — qo'shtirnoqlarsiz, "
        "tushuntirishsiz, markdownsiz, 'Tarjima:' kabi prefikslarsiz."
    ),
}


async def _maybe_retranslate_personality(
    redis: object,
    user_id: int,
    profile: dict[str, object],
    new_ui_lang: str,
) -> None:
    """Translate the stored `personality` description into `new_ui_lang`.

    Best-effort: on any failure (OpenAI down, empty profile, malformed
    response) we leave the prior personality untouched — the dialog directive
    in system prompts already mitigates language mismatch as a soft fallback.

    Called from /lang flow only; cheap (one gpt-5.4-mini call) and runs at
    most when the user explicitly switches language.
    """
    personality = profile.get("personality")
    if not isinstance(personality, str) or not personality.strip():
        return

    from src.agent import loop as agent_loop
    from src.config import settings

    try:
        client = agent_loop.get_client()
        sys_prompt = _PERSONALITY_TRANSLATE_PROMPT.get(
            new_ui_lang, _PERSONALITY_TRANSLATE_PROMPT["ru"]
        )
        resp = await client.chat.completions.create(
            model=settings.openai_model_fast,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": personality.strip()},
            ],
        )
        new_text = (resp.choices[0].message.content or "").strip()
        # Strip stray wrapping quotes/markdown the model sometimes adds despite
        # the instruction.
        new_text = new_text.strip("\"'`").strip()
        if not new_text or new_text == personality.strip():
            return
        await session_mgr.update_profile(redis, user_id, {"personality": new_text})  # type: ignore[arg-type]
        logger.info(
            "personality retranslated",
            user_id=user_id,
            new_lang=new_ui_lang,
            preview=new_text[:60],
        )
    except Exception as exc:
        logger.warning(
            "personality retranslate failed (keeping old)",
            user_id=user_id,
            error=str(exc),
        )
