from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from src.agent import prompts
from src.agent.loop import get_client
from src.config import settings
from src.session.manager import ActiveSession, SessionMessage
from src.vault import git_ops, reader, writer
from src.vault.frontmatter import NoteType, make_note_path

logger = structlog.get_logger()


# ── extraction schema ─────────────────────────────────────────────────────────


class EntityInfo(BaseModel):
    type: Literal["person", "project", "task", "job", "theme", "memory", "thought"]
    name: str
    aliases: list[str] = []
    new_facts: list[str] = []
    updates: list[str] = []
    due: str = ""
    status: str = "open"
    # typed_links: dict where each key is a frontmatter field (owner, works_at, ...)
    # and each value is a list of vault-relative target paths (e.g. "30_Jobs/legai.md").
    # Single-value fields (owner/works_at/for_job/for_project/about_person/parent_theme)
    # use a list with exactly one element. List-value fields (themes, related_people)
    # use a list with 0+ elements.
    typed_links: dict[str, list[str]] = {}


class ThoughtEntry(BaseModel):
    title: str
    body: str


class MemoryEntry(BaseModel):
    title: str
    body: str


class SessionExtraction(BaseModel):
    summary: str
    topic: str
    entities: list[EntityInfo] = []
    thoughts: list[ThoughtEntry] = []
    memories: list[MemoryEntry] = []
    links_to_create: list[list[str]] = []
    open_questions: list[str] = []


# ── LLM extraction ────────────────────────────────────────────────────────────


async def extract(msgs: list[SessionMessage]) -> SessionExtraction:
    """Extract structured data from session messages via structured outputs."""
    conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in msgs)
    client = get_client()
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_main,
        messages=[
            {"role": "system", "content": prompts.load("session_extract")},
            {"role": "user", "content": f"Диалог сессии:\n\n{conversation}"},
        ],
        response_format=SessionExtraction,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError("structured output returned None")
    return parsed


# ── vault application ─────────────────────────────────────────────────────────

_ENTITY_TO_NOTE_TYPE: dict[str, NoteType] = {
    "person": "person",
    "project": "project",
    "task": "task",
    "job": "job",
    "theme": "theme",
    "memory": "memory",
    "thought": "thought",
}


# All known typed-link fields. Anything else gets a warning.
_ALL_TYPED_LINKS = frozenset(
    {
        "owner",
        "works_at",
        "for_job",
        "for_project",
        "about_person",
        "parent_theme",
        "themes",
        "related_people",
    }
)
# `owner` is the only forward field with NO inverse (intentional — see Phase C):
# inverting it would flood _meta/owner.md with thousands of backlinks. So owner
# goes straight into frontmatter without going through add_typed_link.
_OWNER_FIELD = "owner"


async def _apply_entity(entity: EntityInfo, session_id: str) -> str:
    note_type: NoteType = _ENTITY_TO_NOTE_TYPE.get(entity.type, "inbox")  # type: ignore[assignment]
    rel_path = make_note_path(note_type, entity.name)

    body_parts: list[str] = []
    if entity.new_facts:
        body_parts.append("## Факты\n\n" + "\n".join(f"- {f}" for f in entity.new_facts))
    if entity.updates:
        body_parts.append("## Обновления\n\n" + "\n".join(f"- {u}" for u in entity.updates))
    body = "\n\n".join(body_parts)

    fm: dict[str, Any] = {"type": note_type, "aliases": entity.aliases}
    if note_type == "task":
        if entity.status:
            fm["status"] = entity.status
        if entity.due:
            fm["due"] = entity.due

    # owner — write straight to frontmatter (no inverse needed)
    owner_targets = entity.typed_links.get(_OWNER_FIELD, [])
    if owner_targets:
        fm[_OWNER_FIELD] = f"[[{owner_targets[0].removesuffix('.md')}]]"

    # Collect all OTHER typed links (these need add_typed_link for inverse generation)
    typed_links_to_add: list[tuple[str, str]] = []
    for field, targets in entity.typed_links.items():
        if field == _OWNER_FIELD:
            continue
        if field not in _ALL_TYPED_LINKS:
            logger.warning(
                "unknown typed_link field skipped", entity=rel_path, field=field
            )
            continue
        for tgt in targets:
            typed_links_to_add.append((field, tgt))

    # Write or update the note (owner already in fm, other links added below)
    if reader.note_exists(rel_path):
        if body:
            await writer.append_to_note(rel_path, body, session_id)
        await writer.update_frontmatter(rel_path, fm, session_id)
    else:
        await writer.write_note(rel_path, body or entity.name, fm, session_id)

    # All non-owner typed links go via add_typed_link
    # (creates inverse on target + single git commit per pair + reindex queue)
    if typed_links_to_add:
        from src.vault.linking import add_typed_link

        for field, target_path in typed_links_to_add:
            try:
                await add_typed_link(rel_path, target_path, field, session_id)
            except Exception as exc:
                logger.warning(
                    "typed_link failed",
                    from_=rel_path,
                    to=target_path,
                    relation=field,
                    error=str(exc),
                )

    return rel_path


async def _update_daily(
    extraction: SessionExtraction,
    created_paths: list[str],
    session_id: str,
) -> str:
    tz = ZoneInfo(settings.tz)
    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    rel_path = f"10_Daily/{date_str}.md"

    # Link only "heroes" — top 3 entities by new facts count — to avoid daily becoming graph hub
    heroes = sorted(
        [e for e in extraction.entities if e.new_facts],
        key=lambda e: -len(e.new_facts),
    )[:3]
    hero_links: list[str] = []
    for hero in heroes:
        note_type: NoteType = _ENTITY_TO_NOTE_TYPE.get(hero.type, "inbox")  # type: ignore[assignment]
        hero_path = make_note_path(note_type, hero.name)
        hero_links.append(f"- [[{hero_path.removesuffix('.md')}|{hero.name}]]")

    open_q = (
        "\n\n## Открытые вопросы\n\n" + "\n".join(f"- {q}" for q in extraction.open_questions)
        if extraction.open_questions
        else ""
    )
    block = f"### Сессия {session_id}\n\n{extraction.summary}{open_q}"
    if hero_links:
        block += "\n\n#### Главное за сессию\n\n" + "\n".join(hero_links)

    if reader.note_exists(rel_path):
        await writer.append_to_note(rel_path, block, session_id)
    else:
        fm: dict[str, Any] = {
            "type": "daily",
            "notes_touched": [f"[[{p.removesuffix('.md')}]]" for p in created_paths],
        }
        await writer.write_note(rel_path, block, fm, session_id)
    return rel_path


async def apply_to_vault(extraction: SessionExtraction, session_id: str) -> list[str]:
    """Write extraction results to vault. Returns list of affected note paths."""
    from pathlib import Path

    created: list[str] = []

    # Entities (people, projects, tasks, jobs, themes, memories, thoughts)
    for entity in extraction.entities:
        path = await _apply_entity(entity, session_id)
        created.append(path)

    # Standalone thoughts
    for thought in extraction.thoughts:
        rel_path = make_note_path("thought", thought.title)
        if reader.note_exists(rel_path):
            await writer.append_to_note(rel_path, thought.body, session_id)
        else:
            await writer.write_note(rel_path, thought.body, {"type": "thought"}, session_id)
        created.append(rel_path)

    # Standalone memories
    for memory in extraction.memories:
        rel_path = make_note_path("memory", memory.title)
        if reader.note_exists(rel_path):
            await writer.append_to_note(rel_path, memory.body, session_id)
        else:
            await writer.write_note(rel_path, memory.body, {"type": "memory"}, session_id)
        created.append(rel_path)

    # Daily note
    daily = await _update_daily(extraction, created, session_id)
    created.append(daily)

    # Links from extraction
    for pair in extraction.links_to_create:
        if len(pair) == 2 and reader.note_exists(pair[0]) and reader.note_exists(pair[1]):
            from src.tools.obsidian import AddLinkParams, _add_link

            await _add_link(AddLinkParams(from_path=pair[0], to_path=pair[1]), session_id)

    # Smart linker post-pass
    try:
        from src.agent.linker import apply_links, propose_links
        from src.session import manager as session_mgr

        redis = await session_mgr.get_redis()
        user_id = settings.allowed_user_ids[0]
        profile = await session_mgr.get_profile(redis, user_id)
        owner_path = str(profile.get("owner_path", "_meta/owner.md"))
        proposals = await propose_links(created, owner_path)
        applied = await apply_links(proposals, session_id)
        logger.info("smart linker done", proposed=len(proposals), applied=applied)
    except Exception as exc:
        logger.warning("smart linker skipped", error=str(exc))

    # MOC regeneration for touched types
    try:
        from src.vault.moc import regenerate_moc

        _FOLDER_TYPE = {
            "20_People": "person",
            "30_Jobs": "job",
            "40_Projects": "project",
            "80_Themes": "theme",
        }
        touched_types: set[str] = set()
        for path in created:
            folder = path.split("/", 1)[0] if "/" in path else ""
            if folder in _FOLDER_TYPE:
                touched_types.add(_FOLDER_TYPE[folder])
        for t in touched_types:
            await regenerate_moc(t, session_id)
        if touched_types:
            logger.info("moc regenerated", types=list(touched_types))
    except Exception as exc:
        logger.warning("moc regen skipped", error=str(exc))

    # Push to remote
    await git_ops.push(Path(settings.vault_path))

    # Incremental LightRAG index (background, non-blocking)
    _task = asyncio.create_task(_index_created(created))
    _ = _task  # kept to satisfy RUF006; task runs to completion on its own

    return created


async def _index_created(paths: list[str]) -> None:
    try:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(paths)
    except Exception as exc:
        logger.warning("lightrag index skipped", error=str(exc))


# ── full pipeline ─────────────────────────────────────────────────────────────


async def run_pipeline(
    session: ActiveSession,
    msgs: list[SessionMessage],
    notify: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Full session-end pipeline: extract → apply → push. Returns summary for user."""
    if not msgs:
        return "пустая сессия, ничего не записал"

    logger.info("pipeline start", session_id=session.session_id, msgs=len(msgs))

    # Compact if very long (> 50 msgs — summarise older ones)
    if len(msgs) > 50:
        if notify:
            await notify("сессия длинная, сжимаю контекст...")
        msgs = await _compact_msgs(msgs)

    try:
        extraction = await extract(msgs)
    except Exception as exc:
        logger.error("extraction failed", error=str(exc))
        return f"не смог разобрать сессию: {exc}"

    try:
        created = await apply_to_vault(extraction, session.session_id)
    except Exception as exc:
        logger.error("vault apply failed", error=str(exc))
        return f"не смог записать в vault: {exc}"

    open_q = (
        "\n\nОткрытые вопросы:\n" + "\n".join(f"• {q}" for q in extraction.open_questions)
        if extraction.open_questions
        else ""
    )
    summary = (
        f"Записал: {len(created)} заметок\n"
        f"Тема: {extraction.topic}\n"
        f"Итог: {extraction.summary}{open_q}"
    )
    logger.info("pipeline done", session_id=session.session_id, notes=len(created))
    return summary


async def _compact_msgs(msgs: list[SessionMessage]) -> list[SessionMessage]:
    """Summarise the first 40 messages into one, keep the last 10 verbatim."""
    client = get_client()
    head = msgs[:-10]
    tail = msgs[-10:]
    conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in head)
    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {
                "role": "system",
                "content": "Сожми этот диалог в плотный нарратив, сохрани все факты.",
            },
            {"role": "user", "content": conversation},
        ],
    )
    summary_text = resp.choices[0].message.content or ""
    from datetime import datetime

    compact = SessionMessage(
        role="user",
        content=f"[compacted history]: {summary_text}",
        ts=datetime.now(UTC),
    )
    return [compact, *tail]
