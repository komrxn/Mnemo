from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from aiogram import F, Router
from aiogram.types import Audio, Message, Voice
from aiogram.utils.chat_action import ChatActionSender

from src.config import settings
from src.i18n import t
from src.multimodal.whisper import transcribe
from src.session import manager as session_mgr
from src.telegram.handlers.text import process_input
from src.vault import writer as vault_writer

logger = structlog.get_logger()
router = Router(name="voice")


@router.message(F.voice | F.audio)
async def handle_voice(message: Message) -> None:
    if not message.from_user or not message.bot:
        return

    voice: Voice | Audio | None = message.voice or message.audio
    if voice is None:
        return

    user_id = message.from_user.id
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)

    async with ChatActionSender.typing(bot=message.bot, chat_id=user_id):
        await message.answer(t("multimodal.transcribing", ui_lang))

        file_info = await message.bot.get_file(voice.file_id)
        suffix = ".ogg" if message.voice else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        await message.bot.download_file(file_info.file_path, destination=str(tmp_path))

        try:
            transcript = await transcribe(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not transcript.strip():
            await message.answer(t("multimodal.no_speech", ui_lang))
            return

        try:
            tz = ZoneInfo(settings.tz)
            session_date = datetime.now(tz).strftime("%Y-%m-%d")
            rel_path = f"90_Attachments/audio/{session_date}/{voice.file_id}{suffix}"
            audio_data = tmp_path.read_bytes() if tmp_path.exists() else b""
            if audio_data:
                await vault_writer.write_attachment(rel_path, audio_data)
            meta: dict[str, str] = {"attachment": rel_path}
        except Exception as exc:
            logger.warning("attachment save failed", error=str(exc))
            meta = {}

        logger.info("transcribed", user_id=user_id, chars=len(transcript))

        notes_lang = session_mgr.get_notes_language(profile)
        tag = {"ru": "[голосовое]", "en": "[voice]", "uz": "[ovozli]"}.get(notes_lang, "[voice]")

        from src.telegram.formatting import to_telegram_html

        async def reply_fn(text: str) -> None:
            await message.answer(to_telegram_html(text))

        await process_input(
            user_id,
            f"{tag} {transcript}",
            reply_fn,
            kind="voice",
            meta=meta,
        )
