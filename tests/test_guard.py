"""Tests for the read-before-ask guard.

The guard is the structural fix for the BEK-class bug: it makes "claim of
ignorance without prior recall" impossible by forcing the LLM into one more
round with explicit instruction to call recall.

We test the patterns directly (cheap, deterministic) and the run_chat
integration via a fake OpenAI client.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.guard import GUARD_RETRY_SYSTEM_MESSAGE, is_ignorance_claim

# ── pattern matcher ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # Russian
        "Я не помню чтобы ты это говорил",
        "не знаю такого",
        "не вижу в памяти такого",
        "у меня нет такой информации",
        "у меня только 'ресторан (семейный)'",  # the literal BEK bug shape
        "в памяти нет упоминаний",
        "ты не упоминал об этом раньше",
        "впервые слышу",
        # English
        "I don't remember that",
        "I don't see it in memory",
        "I cannot recall",
        "no record of that conversation",
        "you didn't mention this",
        "you haven't told me",
        # Uzbek
        "eslolmayman",
        "bilmayman buni",
        "ko'rmayapman",
        "menda yo'q bu haqida",
        "sen aytmagansan",
    ],
)
def test_is_ignorance_claim_catches_known_phrases(text: str) -> None:
    assert is_ignorance_claim(text), f"should flag: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "понял, записал",
        "хорошо, давай разберём",
        "Restaurant БЕК — записал.",  # bot uses the literal — no ignorance
        "got it, noting that down",
        "tushundim, yozib oldim",
        "",
    ],
)
def test_is_ignorance_claim_skips_normal_replies(text: str) -> None:
    assert not is_ignorance_claim(text), f"false positive: {text!r}"


def test_guard_retry_system_message_is_actionable() -> None:
    """The retry message must mention `recall` explicitly so the LLM knows
    what to do — and must be unambiguous about the order of operations."""
    msg = GUARD_RETRY_SYSTEM_MESSAGE
    assert "recall" in msg
    assert "GUARD" in msg or "guard" in msg
    # It must instruct: 1) call recall, 2) examine output, 3) reply honestly
    lowered = msg.lower()
    assert "call" in lowered
    assert "if recall" in lowered or "examine" in lowered


# ── run_chat integration ─────────────────────────────────────────────────────


def _make_fake_message(content: str | None, tool_calls: list[Any] | None = None) -> Any:
    """Build a mock object that quacks like an OpenAI ChatCompletion message."""

    class _M:
        pass

    m = _M()
    m.content = content
    m.tool_calls = tool_calls
    return m


def _make_fake_response(message: Any, usage_dict: dict[str, int] | None = None) -> Any:
    class _Choice:
        pass

    class _Response:
        pass

    choice = _Choice()
    choice.message = message
    resp = _Response()
    resp.choices = [choice]
    if usage_dict:

        class _Usage:
            pass

        u = _Usage()
        u.prompt_tokens = usage_dict["prompt_tokens"]
        u.completion_tokens = usage_dict["completion_tokens"]
        resp.usage = u
    else:
        resp.usage = None
    return resp


@pytest.mark.asyncio
async def test_run_chat_retries_on_ignorance_without_recall() -> None:
    """Guard fires: ignorance claim + recall_done=False → loop retries with
    guard system msg, second LLM call returns proper answer."""
    from src.agent import loop as agent_loop

    # First call: returns ignorance text. Second call: returns clean text.
    responses = [
        _make_fake_response(_make_fake_message("не помню такого имени")),
        _make_fake_response(_make_fake_message("Помню! Это Ресторан БЕК.")),
    ]

    async def fake_create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    fake_client = type("C", (), {})()
    fake_client.chat = type("CC", (), {})()
    fake_client.chat.completions = type("CCC", (), {})()
    fake_client.chat.completions.create = fake_create

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        return ""

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=False)),
    ):
        out = await agent_loop.run_chat(
            messages=[{"role": "user", "content": "напомни про ресторан"}],
            tools=[],
            dispatch=dispatch,
            session_id="ses_test",
        )

    assert out == "Помню! Это Ресторан БЕК."
    assert responses == []  # both responses consumed


@pytest.mark.asyncio
async def test_run_chat_no_retry_when_recall_already_done() -> None:
    """If `recall` was called this round, even an ignorance-shaped reply is
    accepted — the bot legitimately checked and found nothing."""
    from src.agent import loop as agent_loop

    responses = [
        _make_fake_response(_make_fake_message("не нашёл, ничего об этом нет")),
    ]

    async def fake_create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    fake_client = type("C", (), {})()
    fake_client.chat = type("CC", (), {})()
    fake_client.chat.completions = type("CCC", (), {})()
    fake_client.chat.completions.create = fake_create

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        return ""

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=True)),
    ):
        out = await agent_loop.run_chat(
            messages=[{"role": "user", "content": "что насчёт ресторана?"}],
            tools=[],
            dispatch=dispatch,
            session_id="ses_test",
        )

    assert "не нашёл" in out
    assert responses == []


@pytest.mark.asyncio
async def test_run_chat_no_retry_for_normal_reply() -> None:
    """Non-ignorance replies pass through immediately, no second LLM call."""
    from src.agent import loop as agent_loop

    responses = [
        _make_fake_response(_make_fake_message("Понял, записал.")),
    ]

    async def fake_create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    fake_client = type("C", (), {})()
    fake_client.chat = type("CC", (), {})()
    fake_client.chat.completions = type("CCC", (), {})()
    fake_client.chat.completions.create = fake_create

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        return ""

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=False)),
    ):
        out = await agent_loop.run_chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            dispatch=dispatch,
            session_id="ses_test",
        )

    assert out == "Понял, записал."
    assert responses == []


@pytest.mark.asyncio
async def test_run_chat_guard_retry_capped_at_one() -> None:
    """If LLM is stubborn and produces ignorance twice in a row, the second
    one is accepted (no infinite retry loop)."""
    from src.agent import loop as agent_loop

    responses = [
        _make_fake_response(_make_fake_message("не помню")),
        _make_fake_response(_make_fake_message("всё ещё не помню")),
    ]

    async def fake_create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    fake_client = type("C", (), {})()
    fake_client.chat = type("CC", (), {})()
    fake_client.chat.completions = type("CCC", (), {})()
    fake_client.chat.completions.create = fake_create

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        return ""

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=False)),
    ):
        out = await agent_loop.run_chat(
            messages=[{"role": "user", "content": "напомни"}],
            tools=[],
            dispatch=dispatch,
            session_id="ses_test",
        )

    # Both responses consumed (no infinite loop), second one accepted
    assert out == "всё ещё не помню"
    assert responses == []


@pytest.mark.asyncio
async def test_run_chat_no_guard_when_session_id_empty() -> None:
    """If caller doesn't pass session_id, the guard is disabled — legacy
    behavior preserved for non-session contexts."""
    from src.agent import loop as agent_loop

    responses = [
        _make_fake_response(_make_fake_message("не помню")),
    ]

    async def fake_create(**_kwargs: Any) -> Any:
        return responses.pop(0)

    fake_client = type("C", (), {})()
    fake_client.chat = type("CC", (), {})()
    fake_client.chat.completions = type("CCC", (), {})()
    fake_client.chat.completions.create = fake_create

    async def dispatch(name: str, args: dict[str, Any]) -> str:
        return ""

    with patch("src.agent.loop.get_client", return_value=fake_client):
        out = await agent_loop.run_chat(
            messages=[{"role": "user", "content": "anything"}],
            tools=[],
            dispatch=dispatch,
            # no session_id → guard off
        )

    assert out == "не помню"
    assert responses == []


# ── set_pending_slot gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_pending_slot_refuses_without_recall() -> None:
    """The slot tool must refuse to register a clarifying question if the
    agent didn't consult memory first."""
    from src.tools.slots import SetPendingSlotParams, _set_pending_slot

    with patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=False)):
        result = await _set_pending_slot(
            SetPendingSlotParams(
                field="canonical_name",
                question="как называется ресторан?",
                entity_hint="family restaurant",
            ),
            session_id="ses_test",
        )

    assert "⛔" in result
    assert "recall" in result.lower()


@pytest.mark.asyncio
async def test_set_pending_slot_accepts_when_recall_done() -> None:
    """With recall_done set, the slot registers normally."""
    from src.tools.slots import SetPendingSlotParams, _set_pending_slot

    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)

    with (
        patch("src.tools.recall.was_recall_done", new=AsyncMock(return_value=True)),
        patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake_redis)),
    ):
        result = await _set_pending_slot(
            SetPendingSlotParams(
                field="canonical_name",
                question="как называется?",
                entity_hint="restaurant",
            ),
            session_id="ses_test",
        )

    assert "pending slot registered" in result
    assert "⛔" not in result
