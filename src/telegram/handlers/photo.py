from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from src.config import settings
from src.i18n import t
from src.multimodal.vision import describe
from src.session import manager as session_mgr
from src.telegram.handlers.text import process_input
from src.vault import writer as vault_writer

logger = structlog.get_logger()
router = Router(name="photo")


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    if not message.from_user or not message.bot or not message.photo:
        return

    user_id = message.from_user.id

    async with ChatActionSender.typing(bot=message.bot, chat_id=user_id):
        await _process_photo(message, user_id)


_IMAGE_TAG = {"ru": "[изображение", "en": "[image", "uz": "[tasvir"}


async def _process_photo(message: Message, user_id: int) -> None:
    if not message.bot or not message.photo:
        return
    redis = await session_mgr.get_redis()
    profile = await session_mgr.get_profile(redis, user_id)
    ui_lang = session_mgr.get_ui_language(profile)
    notes_lang = session_mgr.get_notes_language(profile)
    await message.answer(t("multimodal.analyzing_image", ui_lang))

    # Highest resolution = last element
    photo = message.photo[-1]
    file_info = await message.bot.get_file(photo.file_id)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    await message.bot.download_file(file_info.file_path, destination=str(tmp_path))

    try:
        description = await describe(tmp_path)

        # Save to vault attachments atomically
        tz = ZoneInfo(settings.tz)
        month = datetime.now(tz).strftime("%Y-%m")
        rel_path = f"90_Attachments/images/{month}/{photo.file_id}.jpg"
        img_data = tmp_path.read_bytes()
        await vault_writer.write_attachment(rel_path, img_data)
        meta = {"attachment": rel_path}

        tag = _IMAGE_TAG.get(notes_lang, _IMAGE_TAG["en"])
        content = f"{tag}: {rel_path}]\n\n{description}"
        if message.caption:
            content = f"{tag}: {rel_path}] {message.caption}\n\n{description}"

        logger.info("image described", user_id=user_id, path=rel_path)
    except Exception as exc:
        logger.error("photo processing failed", error=str(exc))
        await message.answer(t("multimodal.image_failed", ui_lang, error=str(exc)))
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    from src.telegram.formatting import to_telegram_html

    async def reply_fn(text: str) -> None:
        await message.answer(to_telegram_html(text))

    await process_input(
        user_id,
        content,
        reply_fn,
        kind="image",
        meta=meta,
    )
