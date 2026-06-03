"""Tests for the probe-mode toggle (reply-keyboard + state + handler).

Pins the contract that:
  - New users get probe_mode="on" by default (matches user feedback that
    the bot was too capture-y after the previous rewrite).
  - Both probe label states (probe_on, probe_off) collapse to the synthetic
    command "toggle_probe" — single handler flips state.
  - Tapping the button persists the new state via update_profile AND
    re-sends the keyboard with the flipped label (Telegram reply keyboards
    don't update unless replaced).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.session.manager import get_probe_mode
from src.telegram.keyboards import (
    PROBE_OFF_LABEL_KEY,
    PROBE_ON_LABEL_KEY,
    match_main_kb_button,
)


# ── get_probe_mode default-on contract ────────────────────────────────────────


def test_get_probe_mode_defaults_to_on_for_empty_profile() -> None:
    """Brand-new users (no migration applied yet) get probing ON.
    Matches the documented decision: bot is копательный out of the box."""
    assert get_probe_mode({}) is True


def test_get_probe_mode_off_when_explicitly_off() -> None:
    assert get_probe_mode({"probe_mode": "off"}) is False


def test_get_probe_mode_on_when_explicitly_on() -> None:
    assert get_probe_mode({"probe_mode": "on"}) is True


def test_get_probe_mode_treats_unknown_value_as_on() -> None:
    """Defensive: any garbage in the field defaults to ON. Worst case is
    the bot asks a question — much better than silent capture-only."""
    assert get_probe_mode({"probe_mode": "weird-value"}) is True


# ── label → toggle_probe collapse ─────────────────────────────────────────────


def test_probe_on_label_resolves_to_toggle_probe() -> None:
    """In ru/en/uz, tapping the 'probe ON' label fires 'toggle_probe'."""
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        label = t(PROBE_ON_LABEL_KEY, lang)
        assert match_main_kb_button(label) == "toggle_probe", (
            f"{lang}: {label!r} did not resolve to toggle_probe"
        )


def test_probe_off_label_resolves_to_toggle_probe() -> None:
    from src.i18n import t

    for lang in ("ru", "en", "uz"):
        label = t(PROBE_OFF_LABEL_KEY, lang)
        assert match_main_kb_button(label) == "toggle_probe", (
            f"{lang}: {label!r} did not resolve to toggle_probe"
        )


# ── migration backfills probe_mode for old profiles ──────────────────────────


def test_apply_language_migration_backfills_probe_mode() -> None:
    """Pre-existing profiles (created before this feature) get probe_mode='on'
    on next read. Ensures continuity — old users were effectively in 'on'
    mode (no toggle existed) and behavior should be identical."""
    from src.session.manager import _apply_language_migration

    profile: dict[str, object] = {"ui_language": "ru", "notes_language": "ru"}
    _apply_language_migration(profile)
    assert profile["probe_mode"] == "on"


def test_apply_language_migration_does_not_override_explicit_off() -> None:
    """If user explicitly set probe_mode='off', migration must NOT clobber."""
    from src.session.manager import _apply_language_migration

    profile: dict[str, object] = {"probe_mode": "off"}
    _apply_language_migration(profile)
    assert profile["probe_mode"] == "off"


# ── handler integration: tap → flip state + new keyboard ─────────────────────


@pytest.mark.asyncio
async def test_toggle_handler_flips_state_and_resends_keyboard() -> None:
    """End-to-end: simulate the user tapping the probe-toggle button.
    Handler must (1) call update_profile with the flipped value AND
    (2) call message.answer with a keyboard whose probe label reflects
    the NEW state — otherwise the user sees a stale label."""
    from src.telegram.handlers.text import handle_text

    message = MagicMock()
    message.text = "🧠 Копаем"  # ru, probe currently ON → tap flips to OFF
    message.from_user = MagicMock(id=12345)
    message.answer = AsyncMock()
    message.bot = MagicMock()

    profile_before = {
        "ui_language": "ru",
        "notes_language": "ru",
        "probe_mode": "on",
    }
    redis = AsyncMock()

    with patch(
        "src.session.manager.get_redis", new_callable=AsyncMock, return_value=redis
    ), patch(
        "src.session.manager.get_profile",
        new_callable=AsyncMock,
        return_value=profile_before,
    ), patch(
        "src.session.manager.update_profile", new_callable=AsyncMock
    ) as mock_update:
        await handle_text(message)

    # State persisted with the flipped value.
    mock_update.assert_awaited_once()
    call_args = mock_update.await_args
    assert call_args.args[1] == 12345
    assert call_args.args[2] == {"probe_mode": "off"}

    # Toast + new keyboard sent. The keyboard's probe button label must be
    # the OFF variant (since we just toggled OFF, the next tap should be
    # the 'switch back ON' label).
    message.answer.assert_awaited_once()
    sent_kwargs = message.answer.await_args.kwargs
    kb = sent_kwargs["reply_markup"]
    labels = [btn.text for row in kb.keyboard for btn in row]
    from src.i18n import t

    assert t(PROBE_OFF_LABEL_KEY, "ru") in labels
    assert t(PROBE_ON_LABEL_KEY, "ru") not in labels
