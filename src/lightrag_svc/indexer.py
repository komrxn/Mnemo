from __future__ import annotations

from pathlib import Path

import structlog

from src.config import settings

logger = structlog.get_logger()


async def index_files(paths: list[str]) -> None:
    """Incrementally update the LightRAG graph from vault notes.

    Uses custom KG injection: our typed wikilinks become graph edges directly,
    no LLM extraction. Cost: ~$0.0001 per note (embeddings only).
    """
    if not paths:
        return
    from src.lightrag_svc.client import get_rag
    from src.lightrag_svc.converter import vault_to_custom_kg

    custom_kg = vault_to_custom_kg(paths)
    if not custom_kg["entities"]:
        logger.info("incremental index skipped: no entities")
        return

    rag = await get_rag()
    try:
        await rag.ainsert_custom_kg(custom_kg)  # type: ignore[attr-defined]
        logger.info(
            "incremental index done (custom kg)",
            entities=len(custom_kg["entities"]),
            relations=len(custom_kg["relationships"]),
            chunks=len(custom_kg["chunks"]),
        )
    except Exception as exc:
        logger.error("incremental index failed", error=str(exc))


async def full_reindex() -> None:
    """Re-build the LightRAG graph from scratch from current vault state."""
    vault = Path(settings.vault_path)
    all_md = [
        m
        for m in vault.rglob("*.md")
        if not str(m.relative_to(vault)).startswith(
            ("_meta/MOC_", "_meta/ontology", "_meta/portrait")
        )
    ]
    rel_paths = [str(m.relative_to(vault)) for m in all_md]
    logger.info("full reindex start (custom kg)", files=len(rel_paths))

    from src.lightrag_svc import client as lc
    from src.lightrag_svc.client import get_rag
    from src.lightrag_svc.converter import vault_to_custom_kg

    # Drop singleton so next get_rag() reinitializes storages (idempotent upsert)
    if lc._rag is not None:
        lc._rag = None

    custom_kg = vault_to_custom_kg(rel_paths)
    if not custom_kg["entities"]:
        logger.info("full reindex: no entities")
        return

    rag = await get_rag()
    try:
        BATCH = 100
        ents = custom_kg["entities"]
        rels = custom_kg["relationships"]
        chks = custom_kg["chunks"]
        for i in range(0, len(ents), BATCH):
            batch_ent_names = {e["entity_name"] for e in ents[i : i + BATCH]}
            batch_src_ids = {e["source_id"] for e in ents[i : i + BATCH]}
            sub: dict[str, list] = {
                "entities": ents[i : i + BATCH],
                "relationships": [r for r in rels if r["src_id"] in batch_ent_names],
                "chunks": [c for c in chks if c["source_id"] in batch_src_ids],
            }
            await rag.ainsert_custom_kg(sub)  # type: ignore[attr-defined]
            logger.info("reindex batch done", done=i + len(sub["entities"]), total=len(ents))
    except Exception as exc:
        logger.error("full reindex failed", error=str(exc))
        raise
