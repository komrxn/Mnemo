from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_vault_pull_sync_dispatches_diff_to_correct_handlers() -> None:
    from src.vault.git_ops import VaultDiff

    fake_diff: VaultDiff = {
        "added": ["20_People/анна.md"],
        "modified": ["40_Projects/mnemo.md"],
        "deleted": ["50_Tasks/old.md"],
        "renamed": [("30_Jobs/legai.md", "30_Jobs/legai-corp.md")],
    }

    with (
        patch(
            "src.vault.git_ops.pull_with_diff",
            new=AsyncMock(return_value=fake_diff),
        ),
        patch("src.lightrag_svc.reindex_queue.enqueue", new_callable=AsyncMock) as mock_enq,
        patch("src.lightrag_svc.graph_sync.handle_rename", new_callable=AsyncMock) as mock_ren,
        patch("src.lightrag_svc.graph_sync.handle_delete", new_callable=AsyncMock) as mock_del,
    ):
        from src.scheduler.triggers import _do_vault_pull_sync

        await _do_vault_pull_sync()

    mock_enq.assert_called_once()
    enq_paths = sorted(mock_enq.call_args[0][0])
    assert enq_paths == ["20_People/анна.md", "40_Projects/mnemo.md"]
    mock_ren.assert_called_once_with("30_Jobs/legai.md", "30_Jobs/legai-corp.md")
    mock_del.assert_called_once_with("50_Tasks/old.md")


@pytest.mark.asyncio
async def test_vault_pull_sync_alerts_user_on_conflict() -> None:
    fake_bot = AsyncMock()
    fake_bot.send_message = AsyncMock()

    with (
        patch("src.vault.git_ops.pull_with_diff", new=AsyncMock(return_value=None)),
        patch("src.telegram.bot.get_bot", return_value=fake_bot),
    ):
        from src.scheduler.triggers import _do_vault_pull_sync

        await _do_vault_pull_sync()

    fake_bot.send_message.assert_called_once()
    args = fake_bot.send_message.call_args.args
    assert "Конфликт" in args[1]


@pytest.mark.asyncio
async def test_vault_pull_sync_noop_when_no_changes() -> None:
    from src.vault.git_ops import VaultDiff

    empty_diff: VaultDiff = {
        "added": [],
        "modified": [],
        "deleted": [],
        "renamed": [],
    }

    with (
        patch(
            "src.vault.git_ops.pull_with_diff",
            new=AsyncMock(return_value=empty_diff),
        ),
        patch("src.lightrag_svc.reindex_queue.enqueue", new_callable=AsyncMock) as mock_enq,
        patch("src.lightrag_svc.graph_sync.handle_rename", new_callable=AsyncMock) as mock_ren,
        patch("src.lightrag_svc.graph_sync.handle_delete", new_callable=AsyncMock) as mock_del,
    ):
        from src.scheduler.triggers import _do_vault_pull_sync

        await _do_vault_pull_sync()

    mock_enq.assert_not_called()
    mock_ren.assert_not_called()
    mock_del.assert_not_called()
