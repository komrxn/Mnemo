from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def confirm_keyboard(correlation_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Подтвердить",
            callback_data=f"confirm:{correlation_id}:yes",
        ),
        InlineKeyboardButton(
            text="Отмена",
            callback_data=f"confirm:{correlation_id}:no",
        ),
    )
    return builder.as_markup()
