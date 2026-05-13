"""Tests for /settings — menu rendering, state machine, validation, escape.

The settings flow has a tricky shape: one editable Telegram message, state
in Redis for text-input branches, three sub-menus, four personality presets.
Tests focus on:
  - Menu rendering for empty / populated / xss-y profile (HTML escape).
  - Keyboard structure (every option leads somewhere; back button present).
  - Text-input consumer: validation, save, no-op when state absent.
  - Settings state Redis key namespace doesn't collide with other features.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from src.session import manager as session_mgr
from src.telegram.handlers import settings as st

# ── tiny FakeRedis (same shape as test_slots / test_bek_scenario) ────────────


class FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, bytes] = {}
        self._lists: dict[str, list[bytes]] = defaultdict(list)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._kv[key] = value

    async def get(self, key: str) -> bytes | None:
        return self._kv.get(key)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
            if k in self._lists:
                del self._lists[k]
                n += 1
        return n


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


def _seed_profile(redis: FakeRedis, user_id: int, **fields: Any) -> None:
    default = {"ui_language": "ru", "notes_language": "ru", "bot_name": "Mnemo"}
    default.update(fields)
    redis._kv[session_mgr.key_profile(user_id)] = orjson.dumps(default)


# ── personality preview ─────────────────────────────────────────────────────


def test_personality_preview_empty_returns_default_marker() -> None:
    out = st._personality_preview("", "ru")
    # "default" marker is HTML italic — must remain unescaped (it's static, not user input)
    assert "<i>" in out


def test_personality_preview_escapes_html_chars() -> None:
    out = st._personality_preview("<script>alert(1)</script>", "ru")
    assert "<script>" not in out  # raw tag would break parse_mode=HTML
    assert "&lt;script&gt;" in out


def test_personality_preview_truncates_long() -> None:
    out = st._personality_preview("a" * 200, "ru", limit=20)
    assert len(out) <= 21  # 19 chars + …
    assert out.endswith("…")


# ── menu payload ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_menu_payload_renders_all_fields(fake_redis: FakeRedis) -> None:
    _seed_profile(
        fake_redis,
        user_id=1,
        bot_name="Max",
        personality="Дружелюбный и тёплый",
        ui_language="ru",
        notes_language="en",
    )
    text, kb, ui_lang = await st._menu_payload(fake_redis, 1)
    assert "Max" in text
    assert "Дружелюбный" in text
    # ru locale uses lowercase "русский" (existing language_label convention).
    assert "русский" in text.lower()
    assert "English" in text  # notes_lang label
    assert ui_lang == "ru"
    # All four buttons present
    callbacks = {row[0].callback_data for row in kb.inline_keyboard}
    assert callbacks == {
        "settings:name",
        "settings:personality",
        "settings:langs",
        "settings:close",
    }


@pytest.mark.asyncio
async def test_menu_payload_html_escapes_bot_name(fake_redis: FakeRedis) -> None:
    """A bot name with `<` must not break Telegram's HTML parsing."""
    _seed_profile(fake_redis, user_id=1, bot_name="Max<script>")
    text, _, _ = await st._menu_payload(fake_redis, 1)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


@pytest.mark.asyncio
async def test_menu_payload_empty_personality_shows_default(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1, bot_name="X", personality="")
    text, _, _ = await st._menu_payload(fake_redis, 1)
    assert "<i>по умолчанию</i>" in text


# ── personality keyboard ────────────────────────────────────────────────────


def test_personality_keyboard_has_all_presets_and_custom_and_back() -> None:
    kb = st._personality_keyboard("ru")
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert "settings:p_set:friendly" in callbacks
    assert "settings:p_set:direct" in callbacks
    assert "settings:p_set:sarcastic" in callbacks
    assert "settings:p_set:mentor" in callbacks
    assert "settings:p_custom" in callbacks
    assert "settings:main" in callbacks  # back


def test_languages_keyboard_has_ui_notes_back() -> None:
    kb = st._languages_keyboard("ru")
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert "settings:lang_pick:ui" in callbacks
    assert "settings:lang_pick:notes" in callbacks
    assert "settings:main" in callbacks


def test_lang_picker_keyboard_has_three_langs_and_back() -> None:
    kb = st._lang_picker_keyboard("ui")
    callbacks = [row[0].callback_data for row in kb.inline_keyboard]
    assert "settings:lang_set:ui:ru" in callbacks
    assert "settings:lang_set:ui:en" in callbacks
    assert "settings:lang_set:ui:uz" in callbacks
    assert "settings:langs" in callbacks  # back to languages submenu


# ── state Redis helpers ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_and_get_awaiting_state_roundtrip(fake_redis: FakeRedis) -> None:
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="name", menu_chat_id=42, menu_message_id=99
    )
    state = await st.get_awaiting_state(fake_redis, 1)
    assert state is not None
    assert state["awaiting"] == "name"
    assert state["menu_chat_id"] == 42
    assert state["menu_message_id"] == 99


@pytest.mark.asyncio
async def test_clear_awaiting_state_removes(fake_redis: FakeRedis) -> None:
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="personality", menu_chat_id=1, menu_message_id=2
    )
    await st.clear_awaiting_state(fake_redis, 1)
    assert await st.get_awaiting_state(fake_redis, 1) is None


def test_settings_state_key_namespace_distinct() -> None:
    """Must not collide with onboarding/slots/sessions namespaces."""
    k = session_mgr.key_settings_state(1)
    assert k.startswith("user:settings_state:")
    assert k != session_mgr.key_onboarding(1)
    assert k != session_mgr.key_profile(1)


# ── try_consume_text_input — the integration with handlers/text.py ──────────


@pytest.mark.asyncio
async def test_try_consume_returns_false_when_no_state(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1)
    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "hello", fake_bot)
    assert result is False
    fake_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_try_consume_name_happy_path(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1, bot_name="Mnemo")
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="name", menu_chat_id=1, menu_message_id=99
    )

    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "  Max  ", fake_bot)

    assert result is True
    # Profile updated
    profile = await session_mgr.get_profile(fake_redis, 1)
    assert profile["bot_name"] == "Max"
    # State cleared
    assert await st.get_awaiting_state(fake_redis, 1) is None
    # Menu edited + confirmation sent
    fake_bot.edit_message_text.assert_called_once()
    fake_bot.send_message.assert_called_once()
    confirmation = fake_bot.send_message.call_args[0][1]
    assert "Max" in confirmation


@pytest.mark.asyncio
async def test_try_consume_name_too_long_keeps_state_alive(fake_redis: FakeRedis) -> None:
    """Validation failure: keep state, send error, do NOT save."""
    _seed_profile(fake_redis, user_id=1, bot_name="Mnemo")
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="name", menu_chat_id=1, menu_message_id=99
    )

    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "x" * 50, fake_bot)

    assert result is True
    profile = await session_mgr.get_profile(fake_redis, 1)
    assert profile["bot_name"] == "Mnemo"  # unchanged
    # State must still be there so user can retry
    assert await st.get_awaiting_state(fake_redis, 1) is not None
    fake_bot.edit_message_text.assert_not_called()
    fake_bot.send_message.assert_called_once()  # error message sent


@pytest.mark.asyncio
async def test_try_consume_name_empty_rejected(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1, bot_name="Mnemo")
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="name", menu_chat_id=1, menu_message_id=99
    )

    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "   ", fake_bot)

    assert result is True
    profile = await session_mgr.get_profile(fake_redis, 1)
    assert profile["bot_name"] == "Mnemo"  # unchanged
    assert await st.get_awaiting_state(fake_redis, 1) is not None


@pytest.mark.asyncio
async def test_try_consume_personality_happy_path(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1, bot_name="Mnemo", personality="default")
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="personality", menu_chat_id=1, menu_message_id=99
    )

    fake_bot = AsyncMock()
    new_style = "Строгий, по делу, без эмодзи."
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, new_style, fake_bot)

    assert result is True
    profile = await session_mgr.get_profile(fake_redis, 1)
    assert profile["personality"] == new_style


@pytest.mark.asyncio
async def test_try_consume_personality_too_short(fake_redis: FakeRedis) -> None:
    _seed_profile(fake_redis, user_id=1, personality="old style")
    await st.set_awaiting_state(
        fake_redis, 1, awaiting="personality", menu_chat_id=1, menu_message_id=99
    )

    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "ok", fake_bot)

    assert result is True
    profile = await session_mgr.get_profile(fake_redis, 1)
    assert profile["personality"] == "old style"
    assert await st.get_awaiting_state(fake_redis, 1) is not None


@pytest.mark.asyncio
async def test_try_consume_unknown_awaiting_clears_state(fake_redis: FakeRedis) -> None:
    """A corrupted/unknown state value is dropped so the user isn't stuck."""
    fake_redis._kv[session_mgr.key_settings_state(1)] = orjson.dumps(
        {"awaiting": "garbage", "menu_chat_id": 1, "menu_message_id": 2}
    )
    fake_bot = AsyncMock()
    with patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)):
        result = await st.try_consume_text_input(1, "anything", fake_bot)
    assert result is False
    assert await st.get_awaiting_state(fake_redis, 1) is None


# ── personality preset persistence ──────────────────────────────────────────


def test_personality_presets_match_locale_keys() -> None:
    """Every preset key must have a matching personality.* localization."""
    from src.i18n import t

    for key in st.PERSONALITY_PRESETS:
        for lang in ("ru", "en", "uz"):
            value = t(f"personality.{key}", lang)
            assert value and value != f"personality.{key}", f"missing: {lang}/{key}"
            # Button label also exists
            label = t(f"settings.btn_personality_{key}", lang)
            assert label and label != f"settings.btn_personality_{key}"


# ── cmd_descriptions sanity ─────────────────────────────────────────────────


def test_settings_command_listed_in_commands_meta() -> None:
    from src.telegram.commands_meta import _COMMANDS

    names = [cmd for cmd, _ in _COMMANDS]
    assert "settings" in names
    assert "lang" not in names  # removed


def test_settings_command_description_present_in_all_locales() -> None:
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        desc = t("cmd_descriptions.settings", lang)
        assert desc and desc != "cmd_descriptions.settings"
