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
from src.multimodal.vision import describe
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


async def _process_photo(message: Message, user_id: int) -> None:
    if not message.bot or not message.photo:
        return
    await message.answer("анализирую изображение...")

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

        content = f"[изображение: {rel_path}]\n\n{description}"
        if message.caption:
            content = f"[изображение: {rel_path}] {message.caption}\n\n{description}"

        logger.info("image described", user_id=user_id, path=rel_path)
    except Exception as exc:
        logger.error("photo processing failed", error=str(exc))
        await message.answer(f"не смог обработать изображение: {exc}")
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    await process_input(
        user_id,
        content,
        message.answer,
        kind="image",
        meta=meta,
    )
