from __future__ import annotations

from pathlib import Path

import structlog

from src.config import settings

logger = structlog.get_logger()


async def index_files(paths: list[str]) -> None:
    """Incrementally index specific vault files into LightRAG graph."""
    if not paths:
        return
    from src.lightrag_svc.client import get_rag

    vault = Path(settings.vault_path)
    texts: list[str] = []
    for rel in paths:
        fpath = vault / rel
        if not fpath.exists() or fpath.suffix != ".md":
            continue
        text = fpath.read_text(encoding="utf-8").strip()
        if text:
            texts.append(text)

    if not texts:
        return

    rag = await get_rag()
    try:
        await rag.ainsert(texts)  # type: ignore[attr-defined]
        logger.info("incremental index done", files=len(texts))
    except Exception as exc:
        logger.error("incremental index failed", error=str(exc))


async def full_reindex() -> None:
    """Re-index the entire vault from scratch."""
    vault = Path(settings.vault_path)
    all_md = list(vault.rglob("*.md"))
    logger.info("full reindex start", files=len(all_md))

    texts: list[str] = []
    for fpath in all_md:
        text = fpath.read_text(encoding="utf-8").strip()
        if text:
            texts.append(text)

    if not texts:
        return

    from src.lightrag_svc.client import get_rag
    rag = await get_rag()
    try:
        batch_size = 50
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            await rag.ainsert(batch)  # type: ignore[attr-defined]
            logger.info("reindex batch", done=i + len(batch), total=len(texts))
    except Exception as exc:
        logger.error("full reindex failed", error=str(exc))
        raise
