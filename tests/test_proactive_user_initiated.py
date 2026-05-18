"""Regression tests for `proactive_trigger` user-initiated path.

Background (2026-05-18): user asked the bot "напомни через 30 минут продолжить
echelon". Agent called schedule_task; scheduler fired on time. But proactive_
trigger asked the LLM whether to send — and the LLM returned SKIP. The reminder
was silently dropped twice in a row.

Root cause: the LLM-judges-relevance step is appropriate for bot-initiated
periodic cron jobs (digest, stale-project check) where there may legitimately
be nothing to say. It is NOT appropriate for one-shot reminders the user
explicitly asked for. The user already decided — SKIP is a betrayal.

Fix surface:
  - `proactive_trigger` accepts `user_initiated: bool`.
  - `_schedule_task` (tool) sets it to True.
  - When True + LLM returns SKIP / empty / fails → fall back to deterministic
    localized reminder built from payload.description.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler.triggers import _fallback_user_reminder, proactive_trigger


@pytest.fixture(autouse=True)
def _patch_redis() -> None:
    """Stub Redis: pretend the idempotency key isn't set, allow set()."""
    redis = AsyncMock()
    redis.get.return_value = None  # no recent run
    redis.set.return_value = None
    with patch("src.scheduler.triggers.settings") as mock_settings, patch(
        "src.session.manager.get_redis", return_value=redis
    ):
        mock_settings.allowed_user_ids = [12345]
        mock_settings.vault_path = "/tmp/vault"
        yield


# ── _fallback_user_reminder ───────────────────────────────────────────────────


def test_fallback_uses_description_in_ru() -> None:
    out = _fallback_user_reminder({"description": "продолжить про echelon"}, "ru")
    assert "продолжить про echelon" in out
    assert "⏰" in out


def test_fallback_uses_description_in_en() -> None:
    out = _fallback_user_reminder({"description": "continue echelon discussion"}, "en")
    assert "continue echelon discussion" in out
    assert "⏰" in out


def test_fallback_handles_empty_description() -> None:
    """A malformed task with no description must still produce a non-empty
    message — silently swallowing a user-explicit reminder is the bug we're
    fixing, even if the agent forgot to set description."""
    out = _fallback_user_reminder({}, "ru")
    assert out.strip()
    assert "⏰" in out


# ── proactive_trigger end-to-end with user_initiated=True ─────────────────────


@pytest.mark.asyncio
async def test_user_initiated_skip_falls_back_to_reminder() -> None:
    """If the LLM tries to SKIP a user-initiated task, the trigger must
    refuse and send the deterministic fallback instead of silently dropping.

    This is the exact production case: agent scheduled "напомни через 30 минут",
    LLM returned SKIP, user got nothing.
    """
    bot = AsyncMock()
    bot.send_message = AsyncMock()

    profile_stub = {"ui_language": "ru"}

    with patch("src.session.manager.get_profile", return_value=profile_stub), patch(
        "src.agent.loop.run_chat", return_value="SKIP"
    ) as mock_run_chat, patch(
        "src.scheduler.triggers._load_recent_sessions", return_value=""
    ), patch(
        "src.scheduler.triggers.prompts.render", return_value="dummy system"
    ), patch(
        "src.telegram.bot.get_bot", return_value=bot
    ):
        await proactive_trigger(
            task_id="custom_test_user_skip",
            kind="reminder",
            payload={"description": "продолжить про echelon"},
            user_initiated=True,
        )

    # LLM ran but returned SKIP — we MUST still deliver to the user.
    mock_run_chat.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    sent_text = bot.send_message.await_args.args[1]
    assert "продолжить про echelon" in sent_text
    assert sent_text.strip().upper() != "SKIP"


@pytest.mark.asyncio
async def test_bot_initiated_skip_is_respected() -> None:
    """For bot-side periodic jobs (digest etc.), SKIP must still drop the
    message — there's genuinely nothing to say sometimes, and spamming the
    user is worse than silence."""
    bot = AsyncMock()
    profile_stub = {"ui_language": "ru"}

    with patch("src.session.manager.get_profile", return_value=profile_stub), patch(
        "src.agent.loop.run_chat", return_value="SKIP"
    ), patch(
        "src.scheduler.triggers._load_recent_sessions", return_value=""
    ), patch(
        "src.scheduler.triggers.prompts.render", return_value="dummy system"
    ), patch(
        "src.telegram.bot.get_bot", return_value=bot
    ):
        await proactive_trigger(
            task_id="daily_morning_digest",
            kind="digest",
            payload={"period": "yesterday"},
            user_initiated=False,
        )

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_initiated_normal_reply_passes_through() -> None:
    """When the LLM actually writes a reminder text (happy path), it must be
    delivered verbatim — fallback only kicks in on SKIP/empty/error."""
    bot = AsyncMock()
    profile_stub = {"ui_language": "ru"}
    reminder = "Ты хотел продолжить обсуждение echelon — на чём остановились?"

    with patch("src.session.manager.get_profile", return_value=profile_stub), patch(
        "src.agent.loop.run_chat", return_value=reminder
    ), patch(
        "src.scheduler.triggers._load_recent_sessions", return_value=""
    ), patch(
        "src.scheduler.triggers.prompts.render", return_value="dummy system"
    ), patch(
        "src.telegram.bot.get_bot", return_value=bot
    ):
        await proactive_trigger(
            task_id="custom_test_happy",
            kind="reminder",
            payload={"description": "echelon followup"},
            user_initiated=True,
        )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[1] == reminder


@pytest.mark.asyncio
async def test_user_initiated_llm_failure_still_delivers() -> None:
    """If the LLM call itself raises, a user-initiated reminder must still
    land via the deterministic fallback — explicit user requests are never
    silently swallowed."""
    bot = AsyncMock()
    profile_stub = {"ui_language": "ru"}

    with patch("src.session.manager.get_profile", return_value=profile_stub), patch(
        "src.agent.loop.run_chat", side_effect=RuntimeError("openai timeout")
    ), patch(
        "src.scheduler.triggers._load_recent_sessions", return_value=""
    ), patch(
        "src.scheduler.triggers.prompts.render", return_value="dummy system"
    ), patch(
        "src.telegram.bot.get_bot", return_value=bot
    ):
        await proactive_trigger(
            task_id="custom_test_llm_fail",
            kind="reminder",
            payload={"description": "позвонить маме"},
            user_initiated=True,
        )

    bot.send_message.assert_awaited_once()
    sent_text = bot.send_message.await_args.args[1]
    assert "позвонить маме" in sent_text


@pytest.mark.asyncio
async def test_bot_initiated_llm_failure_drops_silently() -> None:
    """For bot-side periodic jobs, an LLM failure should NOT pester the user
    with a fallback — we don't want every flaky API call to spam the chat."""
    bot = AsyncMock()
    profile_stub = {"ui_language": "ru"}

    with patch("src.session.manager.get_profile", return_value=profile_stub), patch(
        "src.agent.loop.run_chat", side_effect=RuntimeError("openai timeout")
    ), patch(
        "src.scheduler.triggers._load_recent_sessions", return_value=""
    ), patch(
        "src.scheduler.triggers.prompts.render", return_value="dummy system"
    ), patch(
        "src.telegram.bot.get_bot", return_value=bot
    ):
        await proactive_trigger(
            task_id="weekly_reflection",
            kind="digest",
            payload={"period": "week"},
            user_initiated=False,
        )

    bot.send_message.assert_not_awaited()
