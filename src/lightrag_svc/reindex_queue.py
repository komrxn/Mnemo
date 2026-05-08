"""Debounced reindex queue.

Multiple callers (smart linker, extractor, manual edits) may enqueue reindex
requests in burst. We coalesce them: collect paths in a set, wait until 2
seconds pass without new enqueues, then call index_files once with the
deduplicated batch.

This eliminates races on graphml when 10 typed-link writes happen in parallel.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger()

_DEBOUNCE_SECONDS = 2.0

_pending: set[str] = set()
_lock = asyncio.Lock()
_flush_task: asyncio.Task[None] | None = None
_last_enqueue: float = 0.0


async def enqueue(paths: list[str]) -> None:
    """Add paths to the debounce queue. A flush task is scheduled if not running."""
    if not paths:
        return
    global _flush_task, _last_enqueue
    loop = asyncio.get_event_loop()
    async with _lock:
        _pending.update(paths)
        _last_enqueue = loop.time()
        if _flush_task is None or _flush_task.done():
            _flush_task = asyncio.create_task(_flush_loop())


async def _flush_loop() -> None:
    """Sleep `_DEBOUNCE_SECONDS`. If no new enqueue happened during sleep, flush.

    If new enqueue arrived (timer was reset via _last_enqueue), keep waiting.
    """
    while True:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
        loop = asyncio.get_event_loop()
        async with _lock:
            quiet_for = loop.time() - _last_enqueue
            if not _pending:
                return
            if quiet_for >= _DEBOUNCE_SECONDS:
                paths = sorted(_pending)
                _pending.clear()
                break
            # Else: timer was reset by recent enqueue, loop again

    # Flush outside the lock — index_files can be slow
    try:
        from src.lightrag_svc.indexer import index_files

        await index_files(paths)
        logger.info("debounced reindex flushed", count=len(paths))
    except Exception as exc:
        logger.error("debounced reindex failed", count=len(paths), error=str(exc))


async def flush_now() -> None:
    """Force-flush pending paths immediately (used in tests, on shutdown)."""
    async with _lock:
        if not _pending:
            return
        paths = sorted(_pending)
        _pending.clear()

    from src.lightrag_svc.indexer import index_files

    try:
        await index_files(paths)
        logger.info("flush_now reindex done", count=len(paths))
    except Exception as exc:
        logger.error("flush_now reindex failed", count=len(paths), error=str(exc))
