"""Regression test for the system-task leak in `list_tasks`.

Production bug 2026-05-22: user asked "что у нас висит?" and the bot
replied with a list that included its own internal cron jobs —
"sync vault", "weekly reflection", "stale-project check", etc. Those
are bootstrapped in `src/scheduler/defaults.py` and have nothing to
do with the user.

`_list_tasks` was iterating `scheduler.get_jobs()`, which returns the
union of user-scheduled tasks and bot-internal cron jobs. Fix: only
read user-scheduled tasks from the Redis `_TASK_META_KEY` hash —
that's the same hash `_schedule_task` writes to, so system jobs
(which never call `_schedule_task`) are absent by construction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest


@pytest.mark.asyncio
async def test_list_tasks_returns_only_user_reminders() -> None:
    """Only entries from `_TASK_META_KEY` are returned. System cron jobs
    from `defaults.py` (which never write to that hash) are absent."""
    from src.tools.scheduler import ListTasksParams, _list_tasks

    redis = AsyncMock()
    # The hash contains ONLY the user-initiated tasks. System jobs like
    # daily_morning_digest never land here — they go straight into the
    # scheduler in bootstrap_defaults() without persisting metadata.
    redis.hgetall.return_value = {
        b"custom_abc123": orjson.dumps(
            {
                "task_id": "custom_abc123",
                "description": "продолжить про echelon",
                "kind": "reminder",
                "when": "2026-05-22T22:00:00+05:00",
                "payload": {"description": "продолжить про echelon"},
            }
        ),
    }

    scheduler = MagicMock()
    # Live next-run lookup — returns a job with a stable next_run_time.
    job = MagicMock()
    job.next_run_time = "2026-05-22 22:00:00+05:00"
    scheduler.get_job.return_value = job

    with patch("src.session.manager.get_redis", return_value=redis), patch(
        "src.scheduler.apsched.get_scheduler", return_value=scheduler
    ):
        result = await _list_tasks(ListTasksParams())

    # User's reminder must be present, by description (not by opaque id).
    assert "продолжить про echelon" in result
    # System cron-job ids must NOT leak — the bug we're pinning down.
    forbidden = [
        "daily_morning_digest",
        "weekly_reflection",
        "vault_pull_sync",
        "lightrag_full_reindex",
        "tasks_due_check",
        "stale_project_check",
    ]
    for bad in forbidden:
        assert bad not in result, f"internal cron {bad!r} leaked into list_tasks"
    # Opaque ids shouldn't surface either — show description, not custom_abc123.
    assert "custom_abc123" not in result


@pytest.mark.asyncio
async def test_list_tasks_empty_when_no_user_reminders() -> None:
    """No user reminders → friendly message, NOT a dump of system jobs."""
    from src.tools.scheduler import ListTasksParams, _list_tasks

    redis = AsyncMock()
    redis.hgetall.return_value = {}

    scheduler = MagicMock()
    # Even if the scheduler has system jobs running, we must report empty.
    scheduler.get_jobs.return_value = [
        MagicMock(id="daily_morning_digest"),
        MagicMock(id="weekly_reflection"),
    ]

    with patch("src.session.manager.get_redis", return_value=redis), patch(
        "src.scheduler.apsched.get_scheduler", return_value=scheduler
    ):
        result = await _list_tasks(ListTasksParams())

    assert "нет запланированных напоминаний" in result.lower()
    # Most important: the system jobs the scheduler has must NOT appear.
    assert "daily_morning_digest" not in result
    assert "weekly_reflection" not in result


@pytest.mark.asyncio
async def test_list_tasks_falls_back_to_stored_when_no_live_job() -> None:
    """If scheduler.get_job() returns None (e.g. one-shot already fired
    but meta not yet cleaned up), we use the stored `when` field rather
    than crashing or showing a misleading blank."""
    from src.tools.scheduler import ListTasksParams, _list_tasks

    redis = AsyncMock()
    redis.hgetall.return_value = {
        b"custom_xyz": orjson.dumps(
            {
                "task_id": "custom_xyz",
                "description": "позвонить маме",
                "kind": "reminder",
                "when": "2026-05-23T09:00:00+05:00",
                "payload": {},
            }
        ),
    }

    scheduler = MagicMock()
    scheduler.get_job.return_value = None

    with patch("src.session.manager.get_redis", return_value=redis), patch(
        "src.scheduler.apsched.get_scheduler", return_value=scheduler
    ):
        result = await _list_tasks(ListTasksParams())

    assert "позвонить маме" in result
    assert "2026-05-23T09:00:00" in result
