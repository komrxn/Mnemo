from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from src.config import settings

logger = structlog.get_logger()

_rag: Any | None = None

_DEFAULT_ENTITY_TYPES = [
    "Person", "Organization", "Project", "Task",
    "Concept", "Skill", "Tool", "Goal", "Theme", "Event",
]


def _llm_func_factory() -> Any:
    """Return async LLM callable for LightRAG."""
    async def llm_func(
        prompt: str,
        system_prompt: str = "",
        history_messages: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> str:
        from src.agent.loop import get_client
        client = get_client()
        msgs: list[dict[str, str]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        if history_messages:
            msgs.extend(history_messages)
        msgs.append({"role": "user", "content": prompt})
        resp = await client.chat.completions.create(
            model=settings.openai_model_main,
            messages=msgs,
        )
        return resp.choices[0].message.content or ""

    return llm_func


def _embedding_func_factory() -> Any:
    """Return EmbeddingFunc for LightRAG (text-embedding-3-large, dim=3072)."""
    from lightrag.utils import EmbeddingFunc  # type: ignore[import]

    async def embed(texts: list[str]) -> np.ndarray:
        from src.agent.loop import get_client
        client = get_client()
        resp = await client.embeddings.create(
            model=settings.openai_embed_model,
            input=texts,
        )
        return np.array([e.embedding for e in resp.data])

    return EmbeddingFunc(embedding_dim=3072, max_token_size=8191, func=embed)


async def _load_entity_types() -> list[str]:
    """Read entity_types from _meta/ontology.md if present, otherwise use defaults."""
    from pathlib import Path
    ontology = Path(settings.vault_path) / "_meta" / "ontology.md"
    if not ontology.exists():
        return list(_DEFAULT_ENTITY_TYPES)
    raw = ontology.read_text(encoding="utf-8")
    if "## Types" not in raw:
        return list(_DEFAULT_ENTITY_TYPES)
    types_block = raw.split("## Types", 1)[1].strip()
    types = [t.strip() for t in types_block.split(",") if t.strip()]
    return types or list(_DEFAULT_ENTITY_TYPES)


async def get_rag() -> Any:
    """Return initialized LightRAG singleton (lazy init)."""
    global _rag
    if _rag is not None:
        return _rag

    from lightrag import LightRAG  # type: ignore[import]
    import os
    os.makedirs(settings.lightrag_storage, exist_ok=True)

    entity_types = await _load_entity_types()
    rag = LightRAG(
        working_dir=settings.lightrag_storage,
        llm_model_func=_llm_func_factory(),
        embedding_func=_embedding_func_factory(),
        chunk_token_size=1200,
        chunk_overlap_token_size=100,
        addon_params={"entity_types": entity_types},
    )
    _rag = rag
    logger.info("lightrag initialized", storage=settings.lightrag_storage, entity_types=entity_types)
    return _rag


async def insert(text: str) -> None:
    rag = await get_rag()
    await rag.ainsert(text)  # type: ignore[attr-defined]


async def query(question: str, mode: str = "mix") -> str:
    from lightrag import QueryParam  # type: ignore[import]
    rag = await get_rag()
    result = await rag.aquery(question, param=QueryParam(mode=mode))  # type: ignore[attr-defined]
    return result or "нет результатов"
