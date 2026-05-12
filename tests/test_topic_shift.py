"""Tests for topic-shift suppression during Q&A (M5 of memory-layers plan)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.session.manager import ActiveSession, SessionMessage
from src.session.topic_shift import _last_assistant_asked_question, detect


def _msg(role: str, content: str) -> SessionMessage:
    return SessionMessage(role=role, content=content, ts=datetime.now(UTC))


def _session() -> ActiveSession:
    return ActiveSession(
        session_id="ses_test",
        started_at=datetime.now(UTC),
        last_msg_at=datetime.now(UTC),
    )


# ── _last_assistant_asked_question heuristic ─────────────────────────────────


def test_question_mark_at_end_is_detected() -> None:
    msgs = [_msg("user", "hi"), _msg("assistant", "как тебя зовут?")]
    assert _last_assistant_asked_question(msgs) is True


def test_question_mark_followed_by_emoji_is_detected() -> None:
    """Common pattern: bot asks then adds an emoji — must still count as Q."""
    msgs = [_msg("assistant", "что выбираешь? 🤔")]
    assert _last_assistant_asked_question(msgs) is True


def test_statement_is_not_detected_as_question() -> None:
    msgs = [_msg("assistant", "записал твой ответ.")]
    assert _last_assistant_asked_question(msgs) is False


def test_walks_back_to_last_assistant_message() -> None:
    """If the latest msg is a user reply, we still look at the bot's prior turn."""
    msgs = [
        _msg("assistant", "что важно сейчас?"),
        _msg("user", "ничего"),  # user msg ignored — we want assistant context
    ]
    assert _last_assistant_asked_question(msgs) is True


def test_no_assistant_messages_returns_false() -> None:
    msgs = [_msg("user", "hi"), _msg("user", "again")]
    assert _last_assistant_asked_question(msgs) is False


def test_cjk_question_mark_is_detected() -> None:
    msgs = [_msg("assistant", "что выбираешь？")]
    assert _last_assistant_asked_question(msgs) is True


# ── detect() suppression on Q&A ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_suppresses_when_last_msg_is_question() -> None:
    """The headline guarantee: bot asked → user answered → no shift detected,
    so the answer doesn't get split into a new session."""
    history = [
        _msg("user", "у меня семейный ресторан"),
        _msg("assistant", "понял"),
        _msg("user", "ещё есть крипто-продукт"),
        _msg("assistant", "как называется ресторан?"),
    ]
    new_user_reply = "Ресторан БЕК"  # this is the answer — must not trigger shift

    no_pending = AsyncMock(return_value=False)
    with patch("src.session.topic_shift._slot_pending_for_session", new=no_pending):
        shift, topic = await detect(_session(), history, new_user_reply)

    assert shift is False
    assert topic == ""


@pytest.mark.asyncio
async def test_detect_suppresses_when_slot_pending() -> None:
    """Even without a `?`, an explicit pending slot suppresses shift."""
    history = [
        _msg("user", "msg1"),
        _msg("assistant", "got it"),
        _msg("user", "msg2"),
        _msg("assistant", "noted"),
    ]
    has_pending = AsyncMock(return_value=True)
    with patch("src.session.topic_shift._slot_pending_for_session", new=has_pending):
        shift, topic = await detect(_session(), history, "hi")
    assert shift is False
    assert topic == ""


@pytest.mark.asyncio
async def test_detect_returns_false_when_too_few_msgs() -> None:
    """Existing safeguard kept: less than 4 messages → no shift check."""
    history = [_msg("user", "hi"), _msg("assistant", "yo")]
    shift, _ = await detect(_session(), history, "first real msg")
    assert shift is False
