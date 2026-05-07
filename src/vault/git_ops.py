from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog

logger = structlog.get_logger()

_FORBIDDEN: frozenset[str] = frozenset({"--force", "-f", "--no-verify", "--mirror", "--hard"})


def _check(*args: str) -> None:
    for arg in args:
        assert arg not in _FORBIDDEN, f"forbidden git flag: {arg!r}"


def _ssh_env() -> dict[str, str]:
    """Build env with GIT_SSH_COMMAND pointing to our deploy key."""
    import shutil
    from src.config import settings
    env = os.environ.copy()
    key = settings.vault_git_ssh_key
    if key and Path(key).exists():
        # /run/secrets is read-only in Docker — copy to writable /tmp first
        tmp_key = Path("/tmp/vault_ssh_key")
        shutil.copy2(key, tmp_key)
        tmp_key.chmod(0o600)
        env["GIT_SSH_COMMAND"] = f"ssh -i {tmp_key} -o StrictHostKeyChecking=no -o BatchMode=yes"
    return env


async def _run(cwd: Path, *args: str, use_ssh: bool = False) -> str:
    """Run git command in cwd, return stdout. Raises RuntimeError on non-zero exit."""
    _check(*args)
    env = _ssh_env() if use_ssh else None
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip())
    return stdout.decode(errors="replace").strip()


async def init_if_needed(vault_path: Path) -> None:
    from src.config import settings

    if not (vault_path / ".git").exists():
        await _run(vault_path, "init")
        logger.info("vault git init", path=str(vault_path))

    if not settings.vault_git_remote:
        return

    try:
        current = await _run(vault_path, "remote", "get-url", "origin")
        if current != settings.vault_git_remote:
            # Remote exists but points to wrong URL — update it
            await _run(vault_path, "remote", "set-url", "origin", settings.vault_git_remote)
            logger.info("git remote updated", remote=settings.vault_git_remote)
    except RuntimeError:
        await _run(vault_path, "remote", "add", "origin", settings.vault_git_remote)
        logger.info("git remote added", remote=settings.vault_git_remote)


async def configure(vault_path: Path, name: str, email: str) -> None:
    await _run(vault_path, "config", "user.name", name)
    await _run(vault_path, "config", "user.email", email)


async def stage_and_commit(vault_path: Path, rel_paths: list[str], message: str) -> str:
    """Stage specific paths (handles additions, modifications, deletions) and commit."""
    if rel_paths:
        await _run(vault_path, "add", "-A", "--", *rel_paths)

    status = await _run(vault_path, "status", "--porcelain")
    if not status.strip():
        try:
            return await _run(vault_path, "rev-parse", "HEAD")
        except RuntimeError:
            return "no-commits-yet"

    await _run(vault_path, "commit", "-m", message)
    sha = await _run(vault_path, "rev-parse", "HEAD")
    logger.info("committed", sha=sha[:8], msg=message)
    return sha


async def push(vault_path: Path) -> None:
    from src.config import settings
    if not settings.vault_git_remote:
        logger.info("no remote configured, skipping push")
        return
    try:
        await _run(vault_path, "push", use_ssh=True)
    except RuntimeError:
        # Branch has no upstream yet — set it on first push
        branch = await _run(vault_path, "rev-parse", "--abbrev-ref", "HEAD")
        await _run(vault_path, "push", "--set-upstream", "origin", branch, use_ssh=True)
    logger.info("vault pushed")


async def revert_head(vault_path: Path) -> str:
    """Create a revert commit for the last commit. Returns description of what was reverted."""
    try:
        last_msg = await _run(vault_path, "log", "-1", "--pretty=%s")
        await _run(vault_path, "revert", "--no-edit", "HEAD")
        logger.info("reverted HEAD", msg=last_msg)
        return f"отменён коммит: {last_msg}"
    except RuntimeError as exc:
        return f"не смог откатить: {exc}"


async def pull_rebase(vault_path: Path) -> bool:
    """Pull with rebase. Returns True on success, False on conflict."""
    from src.config import settings
    if not settings.vault_git_remote:
        return True
    try:
        await _run(vault_path, "pull", "--rebase", use_ssh=True)
        return True
    except RuntimeError as exc:
        logger.warning("git conflict detected", error=str(exc))
        return False
