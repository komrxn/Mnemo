from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from src.config import settings
from src.vault import git_ops
from src.vault.frontmatter import parse, serialize

logger = structlog.get_logger()


class SafetyError(Exception):
    """Raised when a destructive vault operation is attempted without confirmation."""


def _vault() -> Path:
    return Path(settings.vault_path)


def _resolve(rel_path: str) -> Path:
    """Normalize path and verify it stays inside vault root (no path traversal)."""
    vault = _vault().resolve()
    resolved = (vault / rel_path).resolve()
    if not str(resolved).startswith(str(vault)):
        raise ValueError(f"path traversal rejected: {rel_path!r}")
    return resolved


def _now_iso() -> str:
    tz = ZoneInfo(settings.tz)
    return datetime.now(tz).isoformat()


async def write_note(
    rel_path: str,
    body: str,
    frontmatter: dict[str, Any],
    session_id: str = "",
) -> str:
    """Create or overwrite a note. Always produces one git commit."""
    path = _resolve(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    now = _now_iso()
    fm = dict(frontmatter)
    if "created" not in fm:
        fm["created"] = now
    fm["updated"] = now
    if session_id:
        fm["session_id"] = session_id

    path.write_text(serialize(fm, body), encoding="utf-8")
    return await git_ops.stage_and_commit(
        _vault(), [rel_path], f"write {rel_path} [session={session_id}]"
    )


async def append_to_note(rel_path: str, block: str, session_id: str = "") -> str:
    """Append a dated block to an existing note."""
    path = _resolve(rel_path)
    if not path.exists():
        raise FileNotFoundError(f"{rel_path} not found")

    raw = path.read_text(encoding="utf-8")
    fm, body = parse(raw)

    tz = ZoneInfo(settings.tz)
    date_header = datetime.now(tz).strftime("## %Y-%m-%d %H:%M")
    body = body.rstrip("\n") + f"\n\n{date_header}\n\n{block}\n"
    fm["updated"] = _now_iso()

    path.write_text(serialize(fm, body), encoding="utf-8")
    return await git_ops.stage_and_commit(
        _vault(), [rel_path], f"append {rel_path} [session={session_id}]"
    )


async def update_frontmatter(rel_path: str, patch: dict[str, Any], session_id: str = "") -> str:
    path = _resolve(rel_path)
    raw = path.read_text(encoding="utf-8")
    fm, body = parse(raw)
    fm.update(patch)
    fm["updated"] = _now_iso()
    path.write_text(serialize(fm, body), encoding="utf-8")
    return await git_ops.stage_and_commit(
        _vault(), [rel_path], f"update-fm {rel_path} [session={session_id}]"
    )


async def move_note(old_rel: str, new_rel: str, session_id: str = "") -> str:
    old = _resolve(old_rel)
    new = _resolve(new_rel)
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    sha = await git_ops.stage_and_commit(
        _vault(), [old_rel, new_rel], f"move {old_rel} -> {new_rel} [session={session_id}]"
    )
    try:
        import asyncio

        from src.lightrag_svc.graph_sync import handle_rename

        _t = asyncio.create_task(handle_rename(old_rel, new_rel))
        _ = _t
    except Exception as exc:
        logger.warning("graph rename hook skipped", error=str(exc))
    return sha


async def write_attachment(rel_path: str, data: bytes, session_id: str = "") -> str:
    """Save a binary attachment (image, audio). Atomic write + git commit."""
    path = _resolve(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    import os

    os.replace(tmp, path)  # atomic
    return await git_ops.stage_and_commit(
        _vault(), [rel_path], f"attachment {rel_path} [session={session_id}]"
    )


async def delete_note(rel_path: str, *, confirmed: bool, session_id: str = "") -> str:
    if not confirmed:
        raise SafetyError(f"delete_note({rel_path!r}) requires confirmed=True")
    path = _resolve(rel_path)
    path.unlink()
    sha = await git_ops.stage_and_commit(
        _vault(), [rel_path], f"delete {rel_path} [session={session_id}]"
    )
    try:
        import asyncio

        from src.lightrag_svc.graph_sync import handle_delete

        _t = asyncio.create_task(handle_delete(rel_path))
        _ = _t
    except Exception as exc:
        logger.warning("graph delete hook skipped", error=str(exc))
    return sha
