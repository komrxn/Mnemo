"""Regression test pinning the extractor-side coherence gate.

Production scenario (2026-05-18): extractor at session-end produced a
`thought` entity titled "Бакалавриат" with body about trout business.
`make_note_path` resolved to an existing thought note, and the extractor
silently appended → bleed.

With the gate wired into `_apply_entity` / standalone thoughts/memories,
the extractor must instead create a sibling note when the body doesn't
fit the dedup-matched existing note.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_thought_with_offtopic_body_creates_sibling(tmp_path: Path) -> None:
    """Reproduce the bleed: extractor proposes a `thought` whose body is
    about a totally different subject. Gate flags mismatch → sibling path
    is used → existing note is not poisoned."""
    from src.agent.extractor import apply_to_vault

    # Build a minimal extraction object: no entities, one standalone thought.
    thought = MagicMock()
    thought.title = "Бакалавриат"
    thought.body = "форель: купил оборудование для разведения"

    extraction = MagicMock()
    extraction.entities = []
    extraction.thoughts = [thought]
    extraction.memories = []
    extraction.links_to_create = []
    # apply_to_vault touches _update_daily which needs these fields.
    extraction.summary = "ses"
    extraction.topic = "topic"
    extraction.open_questions = []

    with patch("src.agent.extractor.reader") as mock_reader, patch(
        "src.agent.extractor.writer"
    ) as mock_writer, patch(
        "src.vault.coherence.is_block_coherent_with_note",
        new_callable=AsyncMock,
    ) as mock_gate, patch(
        "src.vault.frontmatter.make_unique_note_path"
    ) as mock_unique, patch(
        "src.agent.linker.propose_links", new_callable=AsyncMock, return_value=[]
    ), patch(
        "src.agent.linker.apply_links", new_callable=AsyncMock, return_value=0
    ), patch(
        "src.vault.moc.regenerate_moc", new_callable=AsyncMock
    ), patch(
        "src.session.manager.get_redis", new_callable=AsyncMock
    ) as mock_redis_fn, patch(
        "src.session.manager.get_profile", new_callable=AsyncMock
    ) as mock_profile, patch(
        "src.agent.extractor._update_daily",
        new_callable=AsyncMock,
        return_value="10_Daily/2026-05-18.md",
    ):
        mock_reader.note_exists.return_value = True  # existing бакалавриат.md
        mock_gate.return_value = "mismatch"
        mock_unique.return_value = "60_Thoughts/бакалавриат-2.md"
        mock_writer.write_note = AsyncMock(return_value="abc12345")
        mock_writer.append_to_note = AsyncMock(return_value="def67890")
        mock_redis_fn.return_value = MagicMock()
        mock_profile.return_value = {"owner_path": "_meta/owner.md"}

        created = await apply_to_vault(extraction, session_id="ses_test")

        # Must NOT have appended to the existing бакалавриат.md.
        mock_writer.append_to_note.assert_not_called()
        # Must have written a sibling note instead.
        mock_writer.write_note.assert_awaited()
        # The sibling path appears in the created list.
        assert any("бакалавриат-2" in p for p in created)


@pytest.mark.asyncio
async def test_thought_with_ontopic_body_appends_normally(tmp_path: Path) -> None:
    """Negative control: when body fits the existing note, the extractor
    still appends — we didn't accidentally block all appends."""
    from src.agent.extractor import apply_to_vault

    thought = MagicMock()
    thought.title = "размышления о работе"
    thought.body = "продолжаю думать, нужно ли менять работу"

    extraction = MagicMock()
    extraction.entities = []
    extraction.thoughts = [thought]
    extraction.memories = []
    extraction.links_to_create = []
    extraction.summary = "ses"
    extraction.topic = "topic"
    extraction.open_questions = []

    with patch("src.agent.extractor.reader") as mock_reader, patch(
        "src.agent.extractor.writer"
    ) as mock_writer, patch(
        "src.vault.coherence.is_block_coherent_with_note",
        new_callable=AsyncMock,
    ) as mock_gate, patch(
        "src.agent.linker.propose_links", new_callable=AsyncMock, return_value=[]
    ), patch(
        "src.agent.linker.apply_links", new_callable=AsyncMock, return_value=0
    ), patch(
        "src.vault.moc.regenerate_moc", new_callable=AsyncMock
    ), patch(
        "src.session.manager.get_redis", new_callable=AsyncMock
    ) as mock_redis_fn, patch(
        "src.session.manager.get_profile", new_callable=AsyncMock
    ) as mock_profile, patch(
        "src.agent.extractor._update_daily",
        new_callable=AsyncMock,
        return_value="10_Daily/2026-05-18.md",
    ):
        mock_reader.note_exists.return_value = True
        mock_gate.return_value = "ok"
        mock_writer.append_to_note = AsyncMock(return_value="abc12345")
        mock_writer.write_note = AsyncMock(return_value="def67890")
        mock_redis_fn.return_value = MagicMock()
        mock_profile.return_value = {"owner_path": "_meta/owner.md"}

        await apply_to_vault(extraction, session_id="ses_test")

        # Append path taken — not the create-sibling fallback.
        mock_writer.append_to_note.assert_awaited()
