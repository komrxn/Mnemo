from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from src.config import settings
from src.logging_setup import setup_logging
from src.telegram.bot import create_bot_and_dispatcher
from src.vault import git_ops

# Import tools modules so their _register() calls populate the registry
import src.tools.obsidian   # noqa: F401
import src.tools.misc       # noqa: F401
import src.tools.scheduler  # noqa: F401
import src.tools.lightrag   # noqa: F401

logger = structlog.get_logger()

_VAULT_FOLDERS = [
    "00_Inbox", "10_Daily", "20_People", "30_Jobs",
    "40_Projects", "50_Tasks", "60_Thoughts", "70_Memories",
    "80_Themes", "90_Attachments/images", "90_Attachments/audio", "_meta",
]


def _init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    import sentry_sdk
    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    logger.info("sentry enabled")


def _bootstrap_vault(vault: Path) -> None:
    for folder in _VAULT_FOLDERS:
        (vault / folder).mkdir(parents=True, exist_ok=True)
        gk = vault / folder / ".gitkeep"
        if not gk.exists():
            gk.touch()


async def init_vault() -> None:
    vault = Path(settings.vault_path)
    vault.mkdir(parents=True, exist_ok=True)
    await git_ops.init_if_needed(vault)
    await git_ops.configure(vault, settings.vault_git_user_name, settings.vault_git_user_email)
    _bootstrap_vault(vault)
    logger.info("vault ready", path=settings.vault_path)


async def main() -> None:
    setup_logging(settings.log_level)
    _init_sentry()
    logger.info("starting", model=settings.openai_model_main, tz=settings.tz)

    await init_vault()

    # Scheduler
    from src.scheduler.apsched import create_scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("scheduler started")

    bot, dp = create_bot_and_dispatcher()

    from src.session.lifecycle import run_forever
    lifecycle_task = asyncio.create_task(run_forever())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        lifecycle_task.cancel()
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("stopped")


if __name__ == "__main__":
    asyncio.run(main())
