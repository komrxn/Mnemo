"""Regression tests for `_StreamUI` flood-control behavior.

Background: production logs showed `EditMessageText` flood-control violations
(`Retry in 200+ seconds`) because the on_text gate used `and` instead of `or`
for skip, letting edits fire whenever EITHER threshold passed. Under fast
token streaming this hit 2-3 edits/sec, well above Telegram's 1/sec limit.
On rate-limit, retries kept firing because _last_edit_at only updated on
success — amplifying the storm.

These tests pin the corrected invariants:
  1. Both min interval AND min delta required to fire an edit.
  2. On TelegramRetryAfter, further edits are dropped until cooldown expires.
  3. _last_edit_at is updated on every attempt (success or failure).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter

from src.telegram.handlers.text import (
    _STREAM_EDIT_MIN_DELTA_CHARS,
    _STREAM_EDIT_MIN_INTERVAL_SEC,
    _StreamUI,
)


def _make_ui() -> tuple[_StreamUI, AsyncMock]:
    bot = AsyncMock()
    ui = _StreamUI(bot=bot, chat_id=1, message_id=1, ui_lang="ru")
    return ui, bot


@pytest.mark.anyio
async def test_on_text_skips_when_interval_too_short(monkeypatch) -> None:
    """Even with a huge delta, edits must wait for the min interval."""
    ui, bot = _make_ui()
    # Prime: first edit always fires (elapsed is huge from monotonic=0).
    await ui.on_text("a" * 200)
    assert bot.edit_message_text.await_count == 1

    # Freeze time so elapsed < min_interval but delta is large.
    import time as time_mod

    fixed = time_mod.monotonic() + 0.1  # well under 0.9s
    monkeypatch.setattr(time_mod, "monotonic", lambda: fixed)
    await ui.on_text("a" * 1000)  # delta ≫ 25
    assert bot.edit_message_text.await_count == 1  # gated by interval


@pytest.mark.anyio
async def test_on_text_skips_when_delta_too_small(monkeypatch) -> None:
    """Even after the min interval, tiny deltas must not consume an edit."""
    ui, bot = _make_ui()
    await ui.on_text("a" * 200)
    assert bot.edit_message_text.await_count == 1

    import time as time_mod

    fixed = time_mod.monotonic() + _STREAM_EDIT_MIN_INTERVAL_SEC + 5
    monkeypatch.setattr(time_mod, "monotonic", lambda: fixed)
    # Add only 1 char — well under min_delta=25.
    await ui.on_text("a" * 201)
    assert bot.edit_message_text.await_count == 1


@pytest.mark.anyio
async def test_on_text_fires_when_both_thresholds_pass(monkeypatch) -> None:
    """Edit fires only when interval AND delta both clear their floors."""
    ui, bot = _make_ui()
    await ui.on_text("a" * 200)

    import time as time_mod

    fixed = time_mod.monotonic() + _STREAM_EDIT_MIN_INTERVAL_SEC + 0.1
    monkeypatch.setattr(time_mod, "monotonic", lambda: fixed)
    await ui.on_text("a" * (200 + _STREAM_EDIT_MIN_DELTA_CHARS + 5))
    assert bot.edit_message_text.await_count == 2


@pytest.mark.anyio
async def test_rate_limit_parks_subsequent_edits() -> None:
    """When TelegramRetryAfter fires, further edits are dropped silently."""
    ui, bot = _make_ui()
    bot.edit_message_text.side_effect = TelegramRetryAfter(
        method=None, message="flood", retry_after=200
    )
    ok = await ui._edit("hello", force=True)
    assert ok is False
    assert ui._blocked_until > 0.0

    # Second attempt should NOT hit the API — we're parked.
    bot.edit_message_text.reset_mock()
    bot.edit_message_text.side_effect = None
    ok2 = await ui._edit("world", force=True)
    assert ok2 is False
    bot.edit_message_text.assert_not_called()


@pytest.mark.anyio
async def test_last_edit_at_updates_on_failure() -> None:
    """A failed attempt must still consume our 1/sec budget — otherwise the
    next 25-char delta retries immediately and amplifies a real flood."""
    ui, bot = _make_ui()
    bot.edit_message_text.side_effect = RuntimeError("network blip")
    assert ui._last_edit_at == 0.0
    await ui._edit("hello", force=True)
    assert ui._last_edit_at > 0.0


@pytest.mark.anyio
async def test_not_modified_is_benign() -> None:
    """Telegram 'message is not modified' must NOT count as an error."""
    ui, bot = _make_ui()
    bot.edit_message_text.side_effect = RuntimeError(
        "Bad Request: message is not modified"
    )
    ok = await ui._edit("hello", force=True)
    assert ok is True
