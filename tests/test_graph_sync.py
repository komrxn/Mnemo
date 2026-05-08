from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handle_rename_deletes_old_and_indexes_new() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock(return_value=None)

    with (
        patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)),
        patch("src.lightrag_svc.indexer.index_files", new=AsyncMock(return_value=None)) as mock_idx,
    ):
        from src.lightrag_svc.graph_sync import handle_rename

        await handle_rename("20_People/anna.md", "20_People/anna-petrova.md")

    mock_rag.adelete_by_entity.assert_called_once_with("20_People/anna")
    mock_idx.assert_called_once_with(["20_People/anna-petrova.md"])


@pytest.mark.asyncio
async def test_handle_rename_noop_when_paths_equal() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock()

    with patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)):
        from src.lightrag_svc.graph_sync import handle_rename

        await handle_rename("20_People/anna.md", "20_People/anna.md")

    mock_rag.adelete_by_entity.assert_not_called()


@pytest.mark.asyncio
async def test_handle_delete_calls_adelete_by_entity() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock(return_value=None)

    with patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)):
        from src.lightrag_svc.graph_sync import handle_delete

        await handle_delete("40_Projects/legai.md")

    mock_rag.adelete_by_entity.assert_called_once_with("40_Projects/legai")
