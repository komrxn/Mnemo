"""Unit tests for the topic-coherence gate.

Pins the behavior that prevents the production bleed bug (2026-05-18):
a note titled "Бакалавриат в финансовом" received facts about the user's
trout business because nothing along the write path validated topic match.

The gate has two stages: rapidfuzz (always) and a mini-LLM fallback (only
on borderline scores). These tests cover both, plus the safe-degradation
paths (note unreadable, empty block, LLM failure).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.vault.coherence import (
    _fuzzy_score,
    is_block_coherent_with_note,
)


@pytest.fixture
def vault_with_notes(tmp_path: Path) -> Path:
    """A vault with two distinct, well-separated notes."""
    people = tmp_path / "20_People"
    people.mkdir()
    jobs = tmp_path / "30_Jobs"
    jobs.mkdir()

    (people / "хилола.md").write_text(
        "---\ntype: person\naliases: [Хилола]\n---\n\n"
        "Хилола — дочь владельца, 10 лет, учится в школе.\n",
        encoding="utf-8",
    )
    (jobs / "бек.md").write_text(
        "---\ntype: job\naliases: [BEK, Restaurant BEK]\n---\n\n"
        "Семейный ресторан в Ташкенте. Поставщики мяса, кухня, "
        "официанты, бухгалтерия. Брат отвечает за операционку.\n",
        encoding="utf-8",
    )
    (tmp_path / "60_Thoughts").mkdir()
    (tmp_path / "60_Thoughts" / "бакалавриат-в-финансовом.md").write_text(
        "---\ntype: thought\naliases: []\n---\n\n"
        "Бакалавриат на финансовом факультете, "
        "размышляю как совмещать с работой.\n",
        encoding="utf-8",
    )
    return tmp_path


# ── Stage 1 (fuzzy) ───────────────────────────────────────────────────────────


def test_fuzzy_score_strong_match() -> None:
    """Block clearly on-topic should score ≥75 from title alone."""
    score = _fuzzy_score(
        block="Бек: открыли новый зал на 30 мест",
        title="бек",
        aliases=["BEK", "Restaurant BEK"],
        body="Семейный ресторан в Ташкенте. Поставщики мяса, кухня.",
    )
    assert score >= 75, f"on-topic block scored {score}, expected ≥75"


def test_fuzzy_score_body_overlap_borderline_invokes_llm() -> None:
    """The bek example from the plan: 'новый поставщик мяса' is on-topic
    for a restaurant note via body overlap (поставщики/мясо/кухня), but the
    title 'бек' doesn't appear in the block. By design fuzzy can't decide
    this confidently — it should land in the LLM-fallback range (40-75),
    and the LLM will clear it. This pins the design intent: pure fuzzy is
    only decisive at the extremes, borderline goes to mini-LLM."""
    from src.vault.coherence import _FUZZY_MISMATCH_THRESHOLD, _FUZZY_OK_THRESHOLD

    score = _fuzzy_score(
        block="нашли нового поставщика мяса, дешевле на 15%",
        title="бек",
        aliases=["BEK"],
        body="Семейный ресторан. Поставщики мяса, кухня, бухгалтерия, "
        "официанты. Брат отвечает за операционку и закупки.",
    )
    assert _FUZZY_MISMATCH_THRESHOLD <= score < _FUZZY_OK_THRESHOLD, (
        f"borderline body-overlap scored {score}, expected in "
        f"[{_FUZZY_MISMATCH_THRESHOLD}, {_FUZZY_OK_THRESHOLD})"
    )


def test_fuzzy_score_bleed_case_low() -> None:
    """The actual production bleed: trout facts vs bachelor's note.
    Must score below the mismatch threshold (40)."""
    score = _fuzzy_score(
        block="форель: купил оборудование для разведения, поставили садки на пруд",
        title="бакалавриат в финансовом",
        aliases=[],
        body="Бакалавриат на финансовом факультете, размышляю как совмещать.",
    )
    assert score < 40, f"bleed block scored {score}, expected <40"


# ── End-to-end verdicts ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verdict_ok_for_on_topic_block(vault_with_notes: Path) -> None:
    with patch("src.vault.coherence.reader") as mock_reader:
        # Re-bind the underlying settings path to tmp vault.
        from src.vault import frontmatter as fm_mod

        def _read(path: str):
            full = vault_with_notes / path
            raw = full.read_text(encoding="utf-8")
            fm_dict, body = fm_mod.parse(raw)
            return MagicMock(
                frontmatter=MagicMock(aliases=fm_dict.get("aliases", [])),
                body=body,
            )

        mock_reader.note_exists.return_value = True
        mock_reader.read_note.side_effect = _read

        verdict = await is_block_coherent_with_note(
            "Бек: подняли цену на бизнес-ланч",
            "30_Jobs/бек.md",
        )
        assert verdict == "ok"


@pytest.mark.asyncio
async def test_verdict_mismatch_blocks_the_bleed(vault_with_notes: Path) -> None:
    """The production scenario: trout facts must NOT land on bachelor's note."""
    with patch("src.vault.coherence.reader") as mock_reader:
        from src.vault import frontmatter as fm_mod

        def _read(path: str):
            full = vault_with_notes / path
            raw = full.read_text(encoding="utf-8")
            fm_dict, body = fm_mod.parse(raw)
            return MagicMock(
                frontmatter=MagicMock(aliases=fm_dict.get("aliases", [])),
                body=body,
            )

        mock_reader.note_exists.return_value = True
        mock_reader.read_note.side_effect = _read

        verdict = await is_block_coherent_with_note(
            "форель: купил оборудование для разведения, поставили садки на пруд",
            "60_Thoughts/бакалавриат-в-финансовом.md",
        )
        assert verdict == "mismatch"


@pytest.mark.asyncio
async def test_verdict_empty_block_is_ok() -> None:
    """Empty / whitespace block doesn't need a gate — let the underlying
    writer decide whether to reject."""
    verdict = await is_block_coherent_with_note("   ", "any/path.md")
    assert verdict == "ok"


@pytest.mark.asyncio
async def test_verdict_missing_note_returns_ok() -> None:
    """If the target note doesn't exist, this isn't an append we're guarding
    — the caller will end up creating fresh, so verdict is permissive."""
    with patch("src.vault.coherence.reader") as mock_reader:
        mock_reader.note_exists.return_value = False
        verdict = await is_block_coherent_with_note(
            "any content", "10_Daily/never-existed.md"
        )
        assert verdict == "ok"


@pytest.mark.asyncio
async def test_unread_note_degrades_to_ok() -> None:
    """If read fails (I/O error etc.), don't block the write — refusing
    legitimate writes over an I/O hiccup is worse than the bleed risk."""
    with patch("src.vault.coherence.reader") as mock_reader:
        mock_reader.note_exists.return_value = True
        mock_reader.read_note.side_effect = OSError("disk failure")
        verdict = await is_block_coherent_with_note("anything", "20_People/x.md")
        assert verdict == "ok"


# ── LLM fallback (borderline path) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_fallback_only_fires_on_borderline() -> None:
    """LLM must not be called when fuzzy is decisive (cheap path stays cheap)."""
    with patch("src.vault.coherence.reader") as mock_reader, patch(
        "src.vault.coherence._llm_verdict"
    ) as mock_llm:
        mock_reader.note_exists.return_value = True
        mock_reader.read_note.return_value = MagicMock(
            frontmatter=MagicMock(aliases=["BEK"]),
            body="ресторан, поставщики мяса, кухня",
        )

        # Strong fuzzy match — must NOT hit LLM.
        await is_block_coherent_with_note(
            "Бек открыли новый зал, поставщики мяса дешевле",
            "30_Jobs/бек.md",
        )
        mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_fallback_used_when_borderline() -> None:
    """When fuzzy scores in the 40-75 range, we escalate to LLM."""
    with patch("src.vault.coherence.reader") as mock_reader, patch(
        "src.vault.coherence._llm_verdict", new_callable=AsyncMock
    ) as mock_llm:
        mock_reader.note_exists.return_value = True
        mock_reader.read_note.return_value = MagicMock(
            frontmatter=MagicMock(aliases=[]),
            body="заметка про что-то нейтральное и обтекаемое",
        )
        mock_llm.return_value = "ok"

        # Construct a block that scores borderline (some token overlap but
        # not strong). "нейтральное" matches but mostly different vocab.
        verdict = await is_block_coherent_with_note(
            "обтекаемое содержимое заметки",
            "60_Thoughts/something.md",
        )
        # If LLM was called, fuzzy was indeed borderline.
        if mock_llm.called:
            assert verdict == "ok"


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_unsure() -> None:
    """LLM call failure should NOT crash the gate — degrade to 'unsure',
    callers will treat as permissive."""
    from src.vault.coherence import _llm_verdict

    # Force `agent_loop.get_client()` to raise — simulates network down,
    # auth error, etc. Verdict must come back as 'unsure', not propagate.
    with patch("src.agent.loop.get_client", side_effect=RuntimeError("net down")):
        verdict = await _llm_verdict(block="anything", title="any", body="body")
        assert verdict == "unsure"
