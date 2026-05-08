from __future__ import annotations

import structlog

from src.lightrag_svc.client import get_rag

logger = structlog.get_logger()


def _entity_name(rel_path: str) -> str:
    """Same convention as converter.py: rel_path without .md."""
    return rel_path.removesuffix(".md")


async def handle_rename(old_rel: str, new_rel: str) -> None:
    """Sync LightRAG graph after a vault note rename: delete old entity, index new."""
    old_name = _entity_name(old_rel)
    new_name = _entity_name(new_rel)
    if old_name == new_name:
        return

    rag = await get_rag()

    try:
        await rag.adelete_by_entity(old_name)  # type: ignore[attr-defined]
        logger.info("graph entity deleted on rename", old=old_name, new=new_name)
    except Exception as exc:
        # Old entity may never have been indexed — log and continue
        logger.warning(
            "graph entity delete failed (likely never indexed)", old=old_name, error=str(exc)
        )

    try:
        from src.lightrag_svc.indexer import index_files

        await index_files([new_rel])
    except Exception as exc:
        logger.error("graph reindex after rename failed", new=new_rel, error=str(exc))


async def handle_delete(rel_path: str) -> None:
    """Sync LightRAG graph after a vault note delete."""
    name = _entity_name(rel_path)
    rag = await get_rag()
    try:
        await rag.adelete_by_entity(name)  # type: ignore[attr-defined]
        logger.info("graph entity deleted on note delete", name=name)
    except Exception as exc:
        logger.warning("graph entity delete failed", name=name, error=str(exc))
