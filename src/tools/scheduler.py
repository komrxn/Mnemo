from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import structlog
from pydantic import BaseModel

from src.config import settings
from src.tools.registry import ToolDef, get_registry

logger = structlog.get_logger()

_TASK_META_KEY = "scheduler:task_meta"


# ── param models ──────────────────────────────────────────────────────────────


class ScheduleTaskParams(BaseModel):
    when: str
    kind: str
    payload: dict[str, Any] = {}
    description: str


class ListTasksParams(BaseModel):
    pass


class UpdateTaskParams(BaseModel):
    task_id: str
    when: str = ""
    payload: dict[str, Any] = {}
    description: str = ""


class CancelTaskParams(BaseModel):
    task_id: str


# ── trigger builder ───────────────────────────────────────────────────────────


def _make_trigger(when: str) -> Any:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    # ISO datetime: contains "T" or at least two "-" with digits
    if "T" in when or (when.count("-") >= 2 and not when.startswith("-")):
        tz = ZoneInfo(settings.tz)
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return DateTrigger(run_date=dt)

    parts = when.split()
    if len(parts) != 5:
        raise ValueError(f"ожидается cron (5 полей) или ISO datetime, получено: {when!r}")
    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )


# ── redis helpers ─────────────────────────────────────────────────────────────


async def _save_meta(task_id: str, meta: dict[str, Any]) -> None:
    from src.session.manager import get_redis

    redis = await get_redis()
    await redis.hset(_TASK_META_KEY, task_id, orjson.dumps(meta))


async def _load_meta(task_id: str) -> dict[str, Any] | None:
    from src.session.manager import get_redis

    redis = await get_redis()
    raw = await redis.hget(_TASK_META_KEY, task_id)
    return orjson.loads(raw) if raw else None


async def _remove_meta(task_id: str) -> None:
    from src.session.manager import get_redis

    redis = await get_redis()
    await redis.hdel(_TASK_META_KEY, task_id)


async def _sync_vault() -> None:
    """Mirror all task metadata to _meta/scheduled_tasks.md."""
    from src.scheduler.apsched import get_scheduler
    from src.session.manager import get_redis
    from src.vault import writer

    redis = await get_redis()
    all_meta_raw = await redis.hgetall(_TASK_META_KEY)
    scheduler = get_scheduler()

    lines = [
        "# Запланированные задачи\n",
        "| ID | Описание | Расписание | Тип | Следующий запуск |",
        "|---|---|---|---|---|",
    ]

    for raw_key, raw_val in all_meta_raw.items():
        tid = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        meta: dict[str, Any] = orjson.loads(raw_val)
        job = scheduler.get_job(tid)
        next_run = str(job.next_run_time) if job and job.next_run_time else "-"
        lines.append(
            f"| {tid} | {meta.get('description', '-')} | {meta.get('when', '-')} "
            f"| {meta.get('kind', '-')} | {next_run} |"
        )

    try:
        await writer.write_note("_meta/scheduled_tasks.md", "\n".join(lines), {"type": "inbox"})
    except Exception as exc:
        logger.warning("scheduled_tasks.md sync failed", error=str(exc))


# ── handlers ──────────────────────────────────────────────────────────────────


async def _schedule_task(p: ScheduleTaskParams, session_id: str = "") -> str:
    from src.scheduler.apsched import get_scheduler
    from src.scheduler.triggers import proactive_trigger

    task_id = f"custom_{uuid.uuid4().hex[:8]}"
    try:
        trigger = _make_trigger(p.when)
    except ValueError as exc:
        return str(exc)

    scheduler = get_scheduler()
    # user_initiated=True: this task came from the agent calling schedule_task
    # in response to the user's explicit request ("напомни через 30 минут...").
    # proactive_trigger uses this flag to refuse SKIP and guarantee delivery.
    scheduler.add_job(
        proactive_trigger,
        trigger=trigger,
        id=task_id,
        kwargs={
            "task_id": task_id,
            "kind": p.kind,
            "payload": p.payload,
            "user_initiated": True,
        },
        replace_existing=True,
    )
    await _save_meta(
        task_id,
        {
            "task_id": task_id,
            "description": p.description,
            "kind": p.kind,
            "when": p.when,
            "payload": p.payload,
        },
    )
    await _sync_vault()
    logger.info("task scheduled", task_id=task_id, when=p.when)
    return f"задача создана: {task_id} ({p.description})"


async def _list_tasks(p: ListTasksParams, session_id: str = "") -> str:
    from src.scheduler.apsched import get_scheduler

    jobs = get_scheduler().get_jobs()
    if not jobs:
        return "нет запланированных задач"
    lines = []
    for job in jobs:
        next_run = str(job.next_run_time) if job.next_run_time else "нет"
        lines.append(f"- {job.id}: следующий запуск {next_run}")
    return "\n".join(lines)


async def _update_task(p: UpdateTaskParams, session_id: str = "") -> str:
    from src.scheduler.apsched import get_scheduler

    scheduler = get_scheduler()
    job = scheduler.get_job(p.task_id)
    if job is None:
        return f"задача {p.task_id!r} не найдена"

    if p.when:
        try:
            trigger = _make_trigger(p.when)
            job.reschedule(trigger=trigger)
        except ValueError as exc:
            return str(exc)

    meta = await _load_meta(p.task_id) or {}
    if p.description:
        meta["description"] = p.description
    if p.when:
        meta["when"] = p.when
    if p.payload:
        meta["payload"] = p.payload
    await _save_meta(p.task_id, meta)
    await _sync_vault()
    return f"задача {p.task_id!r} обновлена"


async def _cancel_task(p: CancelTaskParams, session_id: str = "") -> str:
    from src.scheduler.apsched import get_scheduler

    scheduler = get_scheduler()
    job = scheduler.get_job(p.task_id)
    if job is None:
        return f"задача {p.task_id!r} не найдена"
    job.remove()
    await _remove_meta(p.task_id)
    await _sync_vault()
    return f"задача {p.task_id!r} отменена"


# ── registration ──────────────────────────────────────────────────────────────


def _register() -> None:
    reg = get_registry()
    specs = [
        (
            "schedule_task",
            "Запланировать задачу: when = cron (5 полей) или ISO datetime",
            ScheduleTaskParams,
            _schedule_task,
        ),
        (
            "list_tasks",
            "Список всех запланированных задач с временем следующего запуска",
            ListTasksParams,
            _list_tasks,
        ),
        (
            "update_task",
            "Обновить расписание, описание или payload задачи",
            UpdateTaskParams,
            _update_task,
        ),
        ("cancel_task", "Отменить запланированную задачу", CancelTaskParams, _cancel_task),
    ]
    for name, desc, cls, handler in specs:
        reg.register(ToolDef(name=name, description=desc, params_cls=cls, handler=handler))


_register()
