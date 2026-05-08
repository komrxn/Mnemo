from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_queue_state() -> None:
    """Clear module-level state between tests."""
    from src.lightrag_svc import reindex_queue as rq

    rq._pending.clear()
    rq._flush_task = None
    rq._last_enqueue = 0.0


@pytest.mark.asyncio
async def test_debounce_coalesces_burst_into_single_call() -> None:
    """5 quick enqueues with overlap → exactly 1 index_files call after debounce."""
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(["a.md"])
        await enqueue(["b.md"])
        await enqueue(["a.md"])  # dedup
        await enqueue(["c.md"])

        # Wait past debounce window (2s + safety)
        await asyncio.sleep(2.6)

    assert mock_idx.call_count == 1
    called_paths = sorted(mock_idx.call_args[0][0])
    assert called_paths == ["a.md", "b.md", "c.md"]


@pytest.mark.asyncio
async def test_flush_now_forces_immediate_dispatch() -> None:
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue, flush_now

        await enqueue(["x.md", "y.md"])
        await flush_now()

    mock_idx.assert_called_once()
    called_paths = sorted(mock_idx.call_args[0][0])
    assert called_paths == ["x.md", "y.md"]


@pytest.mark.asyncio
async def test_empty_enqueue_is_noop() -> None:
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue, flush_now

        await enqueue([])
        await flush_now()

    mock_idx.assert_not_called()


@pytest.mark.asyncio
async def test_late_enqueue_resets_timer() -> None:
    """Enqueue, sleep < debounce, enqueue again, then wait full debounce — single flush."""
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(["first.md"])
        await asyncio.sleep(1.0)  # less than 2s debounce
        await enqueue(["second.md"])
        # First wake (~t=2): quiet_for=1, loops again, sleeps 2s more.
        # Second wake (~t=4): quiet_for=3, flushes. Wait long enough to cover that.
        await asyncio.sleep(3.5)

    assert mock_idx.call_count == 1
    assert sorted(mock_idx.call_args[0][0]) == ["first.md", "second.md"]
