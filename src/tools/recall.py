"""Agent-facing tool: `recall` — multi-source memory lookup.

Before the bot says "I don't remember" or asks a clarifying question for
something the user *might* have said earlier, it must call `recall(query)`.
The tool surfaces every place the literal could exist:

1. Current session's raw messages (Redis `session:msgs:{session_id}`).
2. Transcript layer — literal substring across recent sealed sessions
   (`vault/transcripts.search_literal`).
3. Vault — ripgrep across entity notes (`vault/search.search`).
4. LightRAG — semantic query (`lightrag_svc.client.query`).

The combined output lets the agent answer with a literal quote when it exists
instead of falsely claiming ignorance. See `docs/adr/0001-memory-layers.md`
for the read-path ordering: slot → transcript → graph.

Side effect: marks `session:recall_done:{session_id}` in Redis for 120s. Other
tools (e.g. `set_pending_slot`) may consult this marker to enforce a soft
"recall before ask" rule.
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel, Field

from src.tools.registry import ToolDef, get_registry

logger = structlog.get_logger()

_RECALL_MARKER_TTL_SEC = 120  # window in which a recall counts as "recently done"


def key_recall_done(session_id: str) -> str:
    return f"session:recall_done:{session_id}"


class RecallParams(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "What you're trying to remember. Use the exact term you'd quote if "
            "the user had said it (e.g. 'ресторан', 'BEK', 'LegAI'). Keep it "
            "short — this is a literal/semantic search query, not a sentence."
        ),
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum results per source (literal transcripts, vault).",
    )


async def _recall(p: RecallParams, session_id: str = "") -> str:
    """Aggregate memory lookup across all layers. Returns formatted citations.

    All four sources are queried in parallel — semantic is the slow path so
    serialising would dominate total latency. Each source is independent and
    has its own try/except, so a slow/failed source doesn't block the others.
    Empty result is reported explicitly ("nothing found") so the agent knows
    it actually searched.
    """
    msgs_task = _recall_session_msgs(session_id, p.query)
    vault_task = _recall_vault(p.query, p.top_k)
    semantic_task = _recall_semantic(p.query)
    transcripts_result = _recall_transcripts(p.query, p.top_k)

    msgs_section, vault_section, semantic_section = await asyncio.gather(
        msgs_task, vault_task, semantic_task
    )

    await _mark_recall_done(session_id)

    sections = [msgs_section, transcripts_result, vault_section, semantic_section]
    body = "\n\n".join(s for s in sections if s)
    if not body:
        return f"recall('{p.query}'): nothing found in any layer."
    return f"recall('{p.query}'):\n\n{body}"


# ── per-source helpers ───────────────────────────────────────────────────────


async def _recall_session_msgs(session_id: str, query: str) -> str:
    """Substring scan over the active session's messages still in Redis."""
    if not session_id:
        return ""
    try:
        from src.session.manager import get_msgs, get_redis

        redis = await get_redis()
        msgs = await get_msgs(redis, session_id)
        needle = query.lower()
        hits: list[str] = []
        for m in msgs:
            if needle in m.content.lower():
                snippet = m.content.strip().replace("\n", " ")
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                hits.append(f"  - [{m.role}] {snippet}")
                if len(hits) >= 5:
                    break
        if not hits:
            return ""
        return "## current session (literal)\n" + "\n".join(hits)
    except Exception as exc:
        logger.warning("recall session msgs failed", error=str(exc))
        return ""


def _recall_transcripts(query: str, top_k: int) -> str:
    """Literal substring across sealed transcripts (M1 layer)."""
    try:
        from src.vault import transcripts

        hits = transcripts.search_literal(query, k=top_k)
        if not hits:
            return ""
        lines = ["## transcripts (literal)"]
        for h in hits:
            lines.append(f"  - {h.path}:{h.line_no} — {h.snippet[:200]}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("recall transcripts failed", error=str(exc))
        return ""


async def _recall_vault(query: str, top_k: int) -> str:
    """Ripgrep over entity notes in vault."""
    try:
        from src.vault.search import search as vault_search

        results = await vault_search(query, top_k=top_k)
        # The vault search includes transcripts too — exclude them here to avoid
        # double-listing (transcripts have their own section above).
        results = [r for r in results if not r["path"].startswith("90_Transcripts/")]
        if not results:
            return ""
        lines = ["## vault (entity notes)"]
        for r in results:
            lines.append(f"  - {r['path']} — {r['context'][:200]}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("recall vault failed", error=str(exc))
        return ""


async def _recall_semantic(query: str) -> str:
    """LightRAG semantic search — catches paraphrases the literal grep misses."""
    try:
        from src.lightrag_svc.client import query as kg_query

        ctx = await kg_query(query, mode="mix", only_need_context=True, top_k=3)
        if not ctx or not ctx.strip():
            return ""
        snippet = ctx.strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + "\n[…truncated]"
        return "## graph (semantic)\n" + snippet
    except Exception as exc:
        logger.warning("recall semantic failed", error=str(exc))
        return ""


async def _mark_recall_done(session_id: str) -> None:
    """Record that recall ran recently for this session (soft gate for ask-tools)."""
    if not session_id:
        return
    try:
        from src.session.manager import get_redis

        redis = await get_redis()
        await redis.set(key_recall_done(session_id), b"1", ex=_RECALL_MARKER_TTL_SEC)
    except Exception as exc:
        logger.warning("recall marker write failed", error=str(exc))


async def was_recall_done(session_id: str) -> bool:
    """True if `recall` was called within the last `_RECALL_MARKER_TTL_SEC`s."""
    if not session_id:
        return False
    try:
        from src.session.manager import get_redis

        redis = await get_redis()
        return bool(await redis.get(key_recall_done(session_id)))
    except Exception:
        return False


# ── registration ──────────────────────────────────────────────────────────────


def _register() -> None:
    reg = get_registry()
    reg.register(
        ToolDef(
            name="recall",
            description=(
                "Поиск в долгой памяти по ВСЕМ слоям: текущая сессия, "
                "транскрипты прошлых сессий (литерал), заметки vault, "
                "семантический граф. ОБЯЗАТЕЛЬНО вызывай ПЕРЕД тем как "
                "сказать «не помню» / «не вижу» или задать уточняющий "
                "вопрос о том что юзер МОГ упоминать раньше. "
                "Если найден литерал — используй его, не переспрашивай."
            ),
            params_cls=RecallParams,
            handler=_recall,
        )
    )


_register()
