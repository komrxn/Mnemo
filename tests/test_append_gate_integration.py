"""Integration tests for the coherence gate at append-write sites.

Wires the gate into:
  - `src/tools/obsidian.py` `_append_to_note` (agent-facing tool)
  - `src/agent/extractor.py` standalone thoughts/memories
  - `src/vault/write_pipeline.py` dedup-routed appends

Each test patches the coherence verdict and checks the right downstream
behavior (refuse, create-sibling, proceed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_append_to_note_refuses_on_mismatch() -> None:
    """Agent-facing tool returns an error message instead of writing when
    the coherence gate says mismatch. The error string must mention that
    a new note should be created — that's the signal the agent acts on."""
    from src.tools.obsidian import AppendToNoteParams, _append_to_note

    with patch("src.tools.obsidian.reader") as mock_reader, patch(
        "src.vault.coherence.is_block_coherent_with_note",
        new_callable=AsyncMock,
    ) as mock_gate, patch(
        "src.tools.obsidian.writer.append_to_note", new_callable=AsyncMock
    ) as mock_write:
        mock_reader.note_exists.return_value = True
        mock_gate.return_value = "mismatch"

        result = await _append_to_note(
            AppendToNoteParams(
                path="60_Thoughts/бакалавриат-в-финансовом.md",
                block="форель: купил оборудование",
            )
        )

        # Writer must not have been called.
        mock_write.assert_not_called()
        # Error message must steer the agent toward create_note.
        assert "⛔" in result
        assert "create_note" in result.lower() or "create_note" in result


@pytest.mark.asyncio
async def test_append_to_note_proceeds_on_ok() -> None:
    """Happy path: coherence ok → underlying writer is called."""
    from src.tools.obsidian import AppendToNoteParams, _append_to_note

    with patch("src.tools.obsidian.reader") as mock_reader, patch(
        "src.vault.coherence.is_block_coherent_with_note",
        new_callable=AsyncMock,
    ) as mock_gate, patch(
        "src.tools.obsidian.writer.append_to_note", new_callable=AsyncMock
    ) as mock_write, patch(
        "src.tools.obsidian._enqueue_index", new_callable=AsyncMock
    ):
        mock_reader.note_exists.return_value = True
        mock_gate.return_value = "ok"
        mock_write.return_value = "abcdef1234"

        result = await _append_to_note(
            AppendToNoteParams(
                path="30_Jobs/бек.md",
                block="новый поставщик мяса",
            )
        )

        mock_write.assert_awaited_once()
        assert "дописано" in result


@pytest.mark.asyncio
async def test_append_to_note_proceeds_on_unsure() -> None:
    """`unsure` from the gate is permissive at the tool layer — the agent
    might be working on something legitimate the LLM couldn't classify.
    Better to allow than block a real write."""
    from src.tools.obsidian import AppendToNoteParams, _append_to_note

    with patch("src.tools.obsidian.reader") as mock_reader, patch(
        "src.vault.coherence.is_block_coherent_with_note",
        new_callable=AsyncMock,
    ) as mock_gate, patch(
        "src.tools.obsidian.writer.append_to_note", new_callable=AsyncMock
    ) as mock_write, patch(
        "src.tools.obsidian._enqueue_index", new_callable=AsyncMock
    ):
        mock_reader.note_exists.return_value = True
        mock_gate.return_value = "unsure"
        mock_write.return_value = "1234abcd"

        result = await _append_to_note(
            AppendToNoteParams(path="60_Thoughts/x.md", block="borderline content")
        )

        mock_write.assert_awaited_once()
        assert "дописано" in result
