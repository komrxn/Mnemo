from __future__ import annotations

import asyncio
import subprocess

import structlog

from src.config import settings

logger = structlog.get_logger()


def _ripgrep_sync(query: str, vault_path: str, top_k: int) -> list[dict[str, str]]:
    """Run ripgrep synchronously (called via asyncio.to_thread)."""
    result = subprocess.run(
        [
            "rg",
            "--ignore-case",
            "--with-filename",
            "--line-number",
            "--max-count",
            "3",
            "--glob",
            "*.md",
            query,
            vault_path,
        ],
        capture_output=True,
        text=True,
    )
    hits: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        # format: path:line_num:context
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path = parts[0]
        context = parts[2].strip()
        hits.setdefault(path, []).append(context)

    results = []
    for abs_path, contexts in list(hits.items())[:top_k]:
        rel = abs_path.replace(vault_path.rstrip("/") + "/", "")
        results.append({"path": rel, "context": " … ".join(contexts[:2])})
    return results


async def search(query: str, top_k: int = 10) -> list[dict[str, str]]:
    """Full-text search via ripgrep. Returns list of {path, context}."""
    return await asyncio.to_thread(_ripgrep_sync, query, settings.vault_path, top_k)
