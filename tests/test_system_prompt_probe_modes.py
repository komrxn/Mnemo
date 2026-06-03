"""Tests that `prompts/{ru,en}/system.md` renders distinct content for
`probe_on=True` vs `probe_on=False`.

The system prompt is the lever that drives bot behavior; if the new
`probe_on` Jinja variable doesn't actually toggle visible content, the
keyboard button does nothing visible to the LLM.
"""

from __future__ import annotations

import pytest

from src.agent import prompts


# ── Russian ───────────────────────────────────────────────────────────────────


def test_ru_system_prompt_probe_on_shows_probe_mode_block() -> None:
    out = prompts.render("system", lang="ru", bot_name="Mnemo", personality="", probe_on=True)
    assert "КОПАТЬ" in out
    assert "explore-mode" in out.lower() or "explore" in out.lower()
    # The capture-mode-default block must NOT be the visible one.
    assert "Режим: ЗАПИСЫВАТЬ" not in out


def test_ru_system_prompt_probe_off_shows_capture_block() -> None:
    out = prompts.render(
        "system", lang="ru", bot_name="Mnemo", personality="", probe_on=False
    )
    assert "ЗАПИСЫВАТЬ" in out
    # Soft-off carve-out must be present so the bot can still engage on
    # explicit invites.
    assert "soft off" in out.lower() or "Исключение" in out
    assert "Режим: КОПАТЬ" not in out


def test_ru_system_prompt_always_contains_question_quality_rules() -> None:
    """The 'наводящие vs прикапывание' rules apply in BOTH modes — whenever
    the bot does decide to ask a question, it must be open-ended."""
    for probe_on in (True, False):
        out = prompts.render(
            "system",
            lang="ru",
            bot_name="Mnemo",
            personality="",
            probe_on=probe_on,
        )
        assert "наводящие" in out.lower() or "раскрывающие" in out.lower(), (
            f"probe_on={probe_on}: quality rules missing"
        )
        assert "прикапыван" in out.lower(), (
            f"probe_on={probe_on}: anti-nitpicking rule missing"
        )


# ── English ───────────────────────────────────────────────────────────────────


def test_en_system_prompt_probe_on_shows_probe_mode_block() -> None:
    out = prompts.render(
        "system", lang="en", bot_name="Mnemo", personality="", probe_on=True
    )
    assert "PROBE" in out
    assert "Mode: CAPTURE" not in out


def test_en_system_prompt_probe_off_shows_capture_block() -> None:
    out = prompts.render(
        "system", lang="en", bot_name="Mnemo", personality="", probe_on=False
    )
    assert "CAPTURE" in out
    assert "soft off" in out.lower()
    assert "Mode: PROBE" not in out


# ── personality scope unchanged ──────────────────────────────────────────────


@pytest.mark.parametrize("probe_on", [True, False])
def test_personality_still_renders_in_both_modes(probe_on: bool) -> None:
    """The probe-mode block change must not have broken personality injection."""
    out = prompts.render(
        "system",
        lang="ru",
        bot_name="Mnemo",
        personality="дружелюбный и тёплый",
        probe_on=probe_on,
    )
    assert "дружелюбный и тёплый" in out
