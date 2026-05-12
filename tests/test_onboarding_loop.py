"""Tests for the onboarding loop detector + personality auto-translate.

Both fixes address visible first-impression bugs: an agent that keeps asking
the same clarifying question, and a personality string stuck in the wrong
language after a /lang switch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.telegram.handlers.text import _is_onboarding_looping

# ── _is_onboarding_looping ────────────────────────────────────────────────────


def _msg(role: str, content: str) -> dict[str, Any]:
    return {"role": role, "content": content}


def test_loop_detector_returns_false_with_too_few_turns() -> None:
    """Need at least 3 assistant turns to detect repetition — earlier is fine."""
    msgs = [
        _msg("system", "..."),
        _msg("user", "ML Ingener, 18 yosh"),
        _msg("assistant", "Где учишься?"),
        _msg("user", "IT Park"),
        _msg("assistant", "Какой AI-проект сейчас важен?"),
    ]
    assert _is_onboarding_looping(msgs) is False


def test_loop_detector_catches_exact_repetition() -> None:
    """Bot asked the same question twice — loop confirmed."""
    msgs = [
        _msg("system", "..."),
        _msg("assistant", "Где сейчас учишься или работаешь?"),
        _msg("user", "не уверен"),
        _msg("assistant", "Какие AI/ML темы тебе ближе всего?"),
        _msg("user", "не знаю"),
        _msg("assistant", "Где сейчас учишься или работаешь?"),
    ]
    assert _is_onboarding_looping(msgs) is True


def test_loop_detector_catches_paraphrase() -> None:
    """Different wording, same intent — token_set_ratio catches it."""
    msgs = [
        _msg("system", "..."),
        _msg("assistant", "Расскажи где сейчас учишься или работаешь?"),
        _msg("user", "хм"),
        _msg("assistant", "Какие AI темы тебе интересны больше всего?"),
        _msg("user", "хм"),
        _msg("assistant", "Сейчас ты учишься или работаешь — где именно?"),
    ]
    assert _is_onboarding_looping(msgs) is True


def test_loop_detector_skips_legitimate_progression() -> None:
    """Each turn asks a different thing — no loop."""
    msgs = [
        _msg("system", "..."),
        _msg("assistant", "Где сейчас учишься или работаешь?"),
        _msg("user", "IT Park, ML Engineering"),
        _msg("assistant", "А кто твоя девушка, как её зовут?"),
        _msg("user", "Даша"),
        _msg("assistant", "Какие у тебя AI-проекты сейчас в работе?"),
    ]
    assert _is_onboarding_looping(msgs) is False


def test_loop_detector_handles_empty_assistant_turns() -> None:
    """Empty/whitespace-only assistant entries are skipped, not counted."""
    msgs = [
        _msg("assistant", ""),
        _msg("assistant", "   "),
        _msg("assistant", "Где учишься?"),
    ]
    assert _is_onboarding_looping(msgs) is False  # only 1 real turn after filter


def test_loop_detector_handles_uzbek() -> None:
    """Loop detection is language-agnostic — same logic for ru/en/uz."""
    msgs = [
        _msg("assistant", "Hozir qayerda o'qiysan yoki ishlaysan?"),
        _msg("user", "..."),
        _msg("assistant", "Qaysi AI mavzulari senga yaqin?"),
        _msg("user", "..."),
        _msg("assistant", "Hozir qayerda o'qiysan yoki ishlaysan?"),
    ]
    assert _is_onboarding_looping(msgs) is True


# ── personality auto-translate ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_personality_retranslate_writes_new_value() -> None:
    """When personality is in another language, /lang switch translates it."""
    from src.telegram.handlers.commands import _maybe_retranslate_personality

    profile = {"personality": "Do'stona va iliq — qo'llab-quvvatlovchi"}

    fake_choice = MagicMock()
    fake_choice.message.content = "Дружелюбный и тёплый — поддерживающий"
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch(
            "src.telegram.handlers.commands.session_mgr.update_profile",
            new=AsyncMock(),
        ) as upd,
    ):
        await _maybe_retranslate_personality(
            redis=MagicMock(),
            user_id=42,
            profile=profile,
            new_ui_lang="ru",
        )

    upd.assert_called_once()
    args, kwargs = upd.call_args
    # Helper is called positionally: (redis, user_id, patch_dict)
    patch_arg = args[2] if len(args) >= 3 else kwargs.get("patch", {})
    assert "personality" in patch_arg
    assert "Дружелюбный" in patch_arg["personality"]


@pytest.mark.asyncio
async def test_personality_retranslate_noop_on_empty_profile() -> None:
    """No personality stored → nothing to translate, no Redis write."""
    from src.telegram.handlers.commands import _maybe_retranslate_personality

    with patch(
        "src.telegram.handlers.commands.session_mgr.update_profile", new=AsyncMock()
    ) as upd:
        await _maybe_retranslate_personality(
            redis=MagicMock(), user_id=1, profile={}, new_ui_lang="en"
        )
        await _maybe_retranslate_personality(
            redis=MagicMock(),
            user_id=1,
            profile={"personality": "   "},
            new_ui_lang="en",
        )

    upd.assert_not_called()


@pytest.mark.asyncio
async def test_personality_retranslate_strips_quotes_and_markdown() -> None:
    """Models sometimes wrap output in quotes despite the directive — we clean it."""
    from src.telegram.handlers.commands import _maybe_retranslate_personality

    fake_choice = MagicMock()
    fake_choice.message.content = '"Friendly and warm"'
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch(
            "src.telegram.handlers.commands.session_mgr.update_profile",
            new=AsyncMock(),
        ) as upd,
    ):
        await _maybe_retranslate_personality(
            redis=MagicMock(),
            user_id=1,
            profile={"personality": "Дружелюбный"},
            new_ui_lang="en",
        )

    args, _ = upd.call_args
    saved = args[2]["personality"]
    assert saved == "Friendly and warm"  # quotes stripped


@pytest.mark.asyncio
async def test_personality_retranslate_swallows_openai_error() -> None:
    """OpenAI failure must NOT propagate — old personality stays in Redis."""
    from src.telegram.handlers.commands import _maybe_retranslate_personality

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("src.agent.loop.get_client", return_value=fake_client),
        patch(
            "src.telegram.handlers.commands.session_mgr.update_profile",
            new=AsyncMock(),
        ) as upd,
    ):
        # Should not raise
        await _maybe_retranslate_personality(
            redis=MagicMock(),
            user_id=1,
            profile={"personality": "Дружелюбный"},
            new_ui_lang="en",
        )

    upd.assert_not_called()
