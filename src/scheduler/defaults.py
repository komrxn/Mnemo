from __future__ import annotations

import structlog
from apscheduler.triggers.cron import CronTrigger

from src.scheduler.apsched import get_scheduler
from src.scheduler.triggers import proactive_trigger

logger = structlog.get_logger()

# (task_id, cron, kind, payload)
_DEFAULTS: list[tuple[str, str, str, dict]] = [  # type: ignore[type-arg]
    (
        "daily_morning_digest",
        "0 9 * * *",
        "digest",
        {"period": "yesterday", "description": "Утренний дайджест"},
    ),
    (
        "weekly_reflection",
        "0 21 * * 6",
        "digest",
        {"period": "week", "description": "Еженедельная рефлексия"},
    ),
    (
        "tasks_due_check",
        "0 10 * * *",
        "reminder",
        {"check": "due_today", "description": "Дедлайны сегодня"},
    ),
    (
        "stale_project_check",
        "0 18 * * 0",
        "check_in",
        {"check": "no_mentions_7d", "description": "Проекты без упоминаний 7 дней"},
    ),
    (
        "lightrag_ontology_and_reindex",
        "0 3 * * 6",
        "system",
        {"action": "regenerate_ontology_then_reindex", "description": "LightRAG онтология + полный реиндекс"},
    ),
]


def bootstrap_defaults() -> None:
    """Create default scheduled tasks if they don't already exist. Idempotent."""
    scheduler = get_scheduler()
    for task_id, cron, kind, payload in _DEFAULTS:
        if scheduler.get_job(task_id) is not None:
            continue
        parts = cron.split()
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
        scheduler.add_job(
            proactive_trigger,
            trigger=trigger,
            id=task_id,
            kwargs={"task_id": task_id, "kind": kind, "payload": payload},
            replace_existing=False,
        )
        logger.info("default task bootstrapped", task_id=task_id, cron=cron)
