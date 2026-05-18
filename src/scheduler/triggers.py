from __future__ import annotations

import orjson
import structlog

from src.agent import prompts
from src.config import settings

logger = structlog.get_logger()

_LAST_RUN_TTL = 30  # idempotency window in seconds


_USER_REMINDER_FALLBACK: dict[str, str] = {
    "ru": "⏰ Напоминание: {description}",
    "en": "⏰ Reminder: {description}",
    "uz": "⏰ Eslatma: {description}",
}


def _fallback_user_reminder(payload: dict, ui_lang: str) -> str:  # type: ignore[type-arg]
    """Last-resort reminder text when the LLM tried to SKIP a user-initiated task.

    The user explicitly scheduled this — we must not silently drop it. Build a
    deterministic short message from `payload.description` (which the agent
    set when calling schedule_task with the user's own words).
    """
    template = _USER_REMINDER_FALLBACK.get(ui_lang, _USER_REMINDER_FALLBACK["ru"])
    description = str(payload.get("description") or "").strip()
    if not description:
        # No description means the agent scheduled a malformed task — still
        # better to nudge the user than swallow it.
        description = {
            "ru": "запланированная задача",
            "en": "scheduled task",
            "uz": "rejalashtirilgan vazifa",
        }.get(ui_lang, "scheduled task")
    return template.format(description=description)


async def proactive_trigger(  # type: ignore[type-arg]
    task_id: str,
    kind: str,
    payload: dict,
    user_initiated: bool = False,
) -> None:
    """Single entrypoint for all scheduled jobs.

    Two distinct paths:
      - `user_initiated=False` (default): bot-side periodic jobs (morning
        digest, stale-project check, etc.). LLM is allowed to return SKIP
        when there's nothing useful to say.
      - `user_initiated=True`: a one-shot reminder the user explicitly asked
        for via the `schedule_task` tool ("напомни через 30 минут..."). The
        user already decided this is worth sending — SKIP is forbidden, and
        if the LLM tries it anyway we fall back to a deterministic localized
        reminder built from the task description.
    """
    from src.agent.loop import run_chat
    from src.session.manager import get_profile, get_redis
    from src.tools.registry import get_registry

    redis = await get_redis()

    # Idempotency: skip if ran in the last 30 seconds (handles restart double-fire)
    last_run_key = f"job:last_run:{task_id}"
    if await redis.get(last_run_key):
        logger.info("job skipped (recent run)", task_id=task_id)
        return
    await redis.set(last_run_key, b"1", ex=_LAST_RUN_TTL)

    user_id = settings.allowed_user_ids[0]
    profile = await get_profile(redis, user_id)
    recent_sessions = _load_recent_sessions()
    task_context = (
        f"kind={kind}\n"
        f"user_initiated={user_initiated}\n"
        f"{orjson.dumps(payload).decode()}"
    )

    from src.session.manager import get_ui_language

    ui_lang = get_ui_language(profile)

    system = prompts.render(
        "proactive",
        lang=ui_lang,
        task_context=task_context,
        user_profile=orjson.dumps(profile).decode() if profile else "{}",
        recent_sessions=recent_sessions,
        user_initiated=user_initiated,
    )

    # Special case: system tasks like full reindex don't need LLM
    if kind == "system":
        await _handle_system_task(payload)
        return

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"[scheduled:{kind}]"},
    ]
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:  # type: ignore[type-arg]
        return await registry.call(name, args)

    try:
        reply = await run_chat(messages, registry.openai_specs(), dispatch, max_rounds=8)
    except Exception as exc:
        logger.error("proactive trigger failed", task_id=task_id, error=str(exc))
        if user_initiated:
            # Even on LLM failure, the user's explicit reminder must land.
            reply = _fallback_user_reminder(payload, ui_lang)
        else:
            return

    stripped = reply.strip()
    if not stripped or stripped.upper() == "SKIP":
        if user_initiated:
            # LLM tried to swallow a user-explicit task — refuse and fall
            # back to a deterministic reminder. The prompt forbids SKIP in
            # this case (belt), this code path is the suspenders.
            logger.warning(
                "proactive: LLM tried to skip a user-initiated task — forcing fallback",
                task_id=task_id,
                kind=kind,
            )
            reply = _fallback_user_reminder(payload, ui_lang)
        else:
            logger.info("proactive decided to skip", task_id=task_id)
            return

    from src.telegram.bot import get_bot

    bot = get_bot()
    await bot.send_message(user_id, reply)
    logger.info(
        "proactive sent",
        task_id=task_id,
        kind=kind,
        user_initiated=user_initiated,
    )


async def _handle_system_task(payload: dict) -> None:  # type: ignore[type-arg]
    action = payload.get("action")
    if action in {"full_reindex", "regenerate_ontology_then_reindex"}:
        try:
            from src.lightrag_svc.indexer import full_reindex

            await full_reindex()
            logger.info("full reindex completed")
        except Exception as exc:
            logger.error("reindex failed", error=str(exc))
        return

    if action == "vault_pull_sync":
        await _do_vault_pull_sync()
        return


async def _do_vault_pull_sync() -> None:
    """Pull vault from remote and propagate diff to LightRAG graph."""
    from pathlib import Path

    from src.vault import git_ops

    vault = Path(settings.vault_path)
    try:
        diff = await git_ops.pull_with_diff(vault)
    except Exception as exc:
        logger.error("vault pull failed", error=str(exc))
        return

    if diff is None:
        # Conflict — alert user, NO auto-resolve
        from src.i18n import t
        from src.telegram.bot import get_bot

        user_id = settings.allowed_user_ids[0]
        ui_lang = "ru"
        try:
            from src.session.manager import get_profile, get_redis, get_ui_language

            redis = await get_redis()
            profile = await get_profile(redis, user_id)
            ui_lang = get_ui_language(profile)
        except Exception as exc_lang:
            logger.warning("conflict alert lang lookup failed", error=str(exc_lang))

        try:
            bot = get_bot()
            await bot.send_message(user_id, t("scheduler.conflict_alert", ui_lang))
        except Exception as exc2:
            logger.warning("conflict alert send failed", error=str(exc2))
        return

    total = len(diff["added"]) + len(diff["modified"]) + len(diff["deleted"]) + len(diff["renamed"])
    if total == 0:
        return

    logger.info(
        "vault diff detected",
        added=len(diff["added"]),
        modified=len(diff["modified"]),
        deleted=len(diff["deleted"]),
        renamed=len(diff["renamed"]),
    )

    to_index = diff["added"] + diff["modified"]
    if to_index:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(to_index)

    if diff["renamed"] or diff["deleted"]:
        from src.lightrag_svc.graph_sync import handle_delete, handle_rename

        for old, new in diff["renamed"]:
            try:
                await handle_rename(old, new)
            except Exception as exc:
                logger.warning("rename sync failed", old=old, new=new, error=str(exc))
        for path in diff["deleted"]:
            try:
                await handle_delete(path)
            except Exception as exc:
                logger.warning("delete sync failed", path=path, error=str(exc))


def _load_recent_sessions() -> str:
    from pathlib import Path

    path = Path(settings.vault_path) / "_meta" / "session_log.md"
    if not path.exists():
        return "нет записей о прошлых сессиях"
    content = path.read_text(encoding="utf-8")
    return content[-2000:] if len(content) > 2000 else content
