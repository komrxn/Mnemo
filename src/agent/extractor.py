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


class TypedLink(BaseModel):
    """One typed relation from this entity to a target note.

    `field` — name of the frontmatter relation (owner, works_at, for_job, for_project,
    themes, about_person, related_people, parent_theme).
    `target` — vault-relative path of the target note (e.g. "30_Jobs/legai.md").
    """

    field: Literal[
        "owner",
        "works_at",
        "for_job",
        "for_project",
        "themes",
        "about_person",
        "related_people",
        "parent_theme",
    ]
    target: str


class EntityInfo(BaseModel):
    type: Literal["person", "project", "task", "job", "theme", "memory", "thought"]
    name: str
    aliases: list[str] = []
    new_facts: list[str] = []
    updates: list[str] = []
    due: str = ""
    status: str = "open"
    # List of typed relations from this entity. Compatible with OpenAI strict mode
    # (no dict with arbitrary keys). Multiple links to the same field type are allowed
    # (e.g. two `themes` entries → two list items).
    typed_links: list[TypedLink] = []


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


async def extract(msgs: list[SessionMessage], session_id: str = "") -> SessionExtraction:
    """Extract structured data from session messages via structured outputs.

    Injects a vault map + a language directive into the system prompt — LLM
    sees existing entities (no parallels) and uses one language consistently.
    If `session_id` is provided, also injects any filled slots so the LLM
    honors literal user answers when constructing canonical_name/aliases.
    """
    from src.session import slots
    from src.session.manager import get_notes_language, get_profile, get_redis
    from src.telegram.handlers.text import _build_language_instruction
    from src.vault.vault_map import build_vault_map

    conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in msgs)

    try:
        redis = await get_redis()
        user_id = settings.allowed_user_ids[0]
        profile = await get_profile(redis, user_id)
        notes_lang = get_notes_language(profile)
    except Exception:
        notes_lang = "ru"
        redis = None

    base_system = prompts.load("session_extract", lang=notes_lang)
    vault_map = build_vault_map()
    lang_instruction = _build_language_instruction(notes_lang)

    # Pull filled slots so literals like "Ресторан БЕК" can't be paraphrased away
    # at extraction time (M2 of memory-layers plan).
    filled_block = ""
    if session_id and redis is not None:
        try:
            filled = await slots.list_filled(redis, session_id)
            filled_block = slots.format_filled_list_for_extraction(filled, lang=notes_lang)
        except Exception as exc:
            logger.warning("filled slots fetch failed", session_id=session_id, error=str(exc))

    parts = [base_system, lang_instruction, vault_map]
    if filled_block:
        parts.append(filled_block)
    system_with_map = "\n\n---\n\n".join(parts)

    dialog_label = {
        "ru": "Диалог сессии",
        "en": "Session dialog",
        "uz": "Sessiya muloqoti",
    }.get(notes_lang, "Session dialog")

    client = get_client()
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_main,
        messages=[
            {"role": "system", "content": system_with_map},
            {"role": "user", "content": f"{dialog_label}:\n\n{conversation}"},
        ],
        response_format=SessionExtraction,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError("structured output returned None")

    # Post-extraction audit: warn if any slot literal was dropped from all
    # extracted entities/thoughts/memories. Soft for now (just logs) — the
    # Entity validator (M4) is the hard structural gate at write time.
    if session_id and redis is not None:
        try:
            filled = await slots.list_filled(redis, session_id)
            _warn_missing_slot_literals(parsed, filled)
        except Exception as exc:
            logger.warning("slot-literal audit failed", error=str(exc))

    return parsed


def _warn_missing_slot_literals(
    extraction: SessionExtraction,
    filled: list,  # list[FilledSlot] — avoid circular type import
) -> None:
    """Log a warning for each slot whose literal_value is absent from extraction.

    These are cases where the agent's LLM had the literal in its prompt (via
    `format_filled_list_for_extraction`) but produced no entity carrying it.
    The literal is still in transcripts (M1), so it's not lost forever — but
    the graph projection is incomplete and the user can be told.
    """
    if not filled:
        return
    haystack_parts: list[str] = []
    for e in extraction.entities:
        haystack_parts.extend([e.name, *e.aliases, *e.new_facts, *e.updates])
    for t in extraction.thoughts:
        haystack_parts.extend([t.title, t.body])
    for m in extraction.memories:
        haystack_parts.extend([m.title, m.body])
    haystack = " ".join(haystack_parts).lower()

    for f in filled:
        if f.literal_value.lower() not in haystack:
            logger.warning(
                "slot literal not preserved in extraction",
                slot_id=f.slot_id,
                field=f.field,
                entity_hint=f.entity_hint,
                literal=f.literal_value[:80],
            )


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


async def _apply_entity(entity: EntityInfo, session_id: str) -> str:
    """Adapter: convert legacy EntityInfo (extractor LLM output) to Entity contract,
    then route through the unified write_pipeline.
    """
    from src.vault.entity import Entity, Relation, extract_proper_noun_candidates
    from src.vault.write_pipeline import write_entity

    note_type: NoteType = _ENTITY_TO_NOTE_TYPE.get(entity.type, "inbox")  # type: ignore[assignment]

    # Build Entity contract from EntityInfo.
    # one_liner = first new_fact OR fallback to name
    one_liner = entity.new_facts[0] if entity.new_facts else entity.name
    one_liner = one_liner.strip().split("\n")[0][:200]
    if len(one_liner) < 10:
        one_liner = f"{entity.name} ({note_type})"

    relations: list[Relation] = []
    for link in entity.typed_links:
        relations.append(
            Relation(field=link.field, target_path=link.target)  # type: ignore[arg-type]
        )

    # Source tokens for proper-noun preservation (M4). Pull from EntityInfo's
    # own fields — anything the LLM kept here must survive into Entity. This
    # catches partial drops like name="ресторан" + facts=["работает БЕК"]
    # → "БЕК" must end up in canonical_name/aliases/facts/one_liner.
    source_text = " ".join(
        [entity.name, *entity.aliases, *entity.new_facts, *entity.updates]
    )
    source_tokens = extract_proper_noun_candidates(source_text)

    try:
        e = Entity.model_validate(
            {
                "type": note_type,
                "canonical_name": entity.name,
                "aliases": entity.aliases,
                "one_liner": one_liner,
                "facts": entity.new_facts[1:] if len(entity.new_facts) > 1 else [],
                "relations": [r.model_dump() for r in relations],
            },
            context={"source_tokens": source_tokens},
        )
    except Exception as exc:
        logger.warning(
            "extractor entity validation failed",
            name=entity.name,
            type=note_type,
            error=str(exc),
        )
        return ""

    result = await write_entity(e, session_id)

    # write_entity may reject the entity (e.g. name contains owner_name).
    # Empty path → propagate skip to caller.
    if not result.path:
        return ""

    # Task-specific frontmatter (status/due) — set after write_entity since
    # Entity contract doesn't carry those. They live on the task note only.
    if note_type == "task" and (entity.status or entity.due):
        task_patch: dict[str, Any] = {}
        if entity.status:
            task_patch["status"] = entity.status
        if entity.due:
            task_patch["due"] = entity.due
        await writer.update_frontmatter(result.path, task_patch, session_id)

    return result.path


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
        if path:  # _apply_entity returns "" on validation failure — skip
            created.append(path)

    # Standalone thoughts. On dedup-routed append we run the topic-coherence
    # gate — if the LLM extractor proposed a path that doesn't fit the body,
    # we create a sibling note instead of bleeding facts into the wrong one.
    # The extractor has no agent retry loop, so on mismatch we make the
    # deterministic safe choice (create fresh) rather than refusing entirely.
    from src.vault.coherence import is_block_coherent_with_note
    from src.vault.frontmatter import make_unique_note_path

    for thought in extraction.thoughts:
        rel_path = make_note_path("thought", thought.title)
        if reader.note_exists(rel_path):
            verdict = await is_block_coherent_with_note(thought.body, rel_path)
            if verdict == "mismatch":
                logger.warning(
                    "extractor: thought body off-topic for existing note, creating sibling",
                    wanted=rel_path,
                    title=thought.title,
                )
                rel_path = make_unique_note_path("thought", thought.title)
                await writer.write_note(
                    rel_path, thought.body, {"type": "thought"}, session_id
                )
            else:
                await writer.append_to_note(rel_path, thought.body, session_id)
        else:
            await writer.write_note(rel_path, thought.body, {"type": "thought"}, session_id)
        created.append(rel_path)

    # Standalone memories — same coherence gate as thoughts.
    for memory in extraction.memories:
        rel_path = make_note_path("memory", memory.title)
        if reader.note_exists(rel_path):
            verdict = await is_block_coherent_with_note(memory.body, rel_path)
            if verdict == "mismatch":
                logger.warning(
                    "extractor: memory body off-topic for existing note, creating sibling",
                    wanted=rel_path,
                    title=memory.title,
                )
                rel_path = make_unique_note_path("memory", memory.title)
                await writer.write_note(
                    rel_path, memory.body, {"type": "memory"}, session_id
                )
            else:
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

    # Owner.md auto-refresh — only if a fundamental fact about the owner was
    # touched (new job / life-event memory / top-level theme). Skips refresh
    # for sessions that only added details to existing sub-graphs.
    try:
        from src.agent.owner_refresh import refresh_owner_from_vault, should_refresh_after
        from src.session import manager as session_mgr

        if should_refresh_after(created):
            redis = await session_mgr.get_redis()
            user_id = settings.allowed_user_ids[0]
            profile = await session_mgr.get_profile(redis, user_id)
            owner_name = str(profile.get("owner_name", "Владелец"))
            refreshed = await refresh_owner_from_vault(owner_name)
            if refreshed:
                logger.info("owner.md auto-refreshed after session", session_id=session_id)
    except Exception as exc:
        logger.warning("owner refresh skipped", error=str(exc))

    # Push to remote — graceful: if SSH/network broken, vault still has commits
    try:
        await git_ops.push(Path(settings.vault_path))
    except Exception as exc:
        logger.warning(
            "vault push failed (commits saved locally)",
            session_id=session_id,
            error=str(exc),
        )

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
    from src.i18n import t
    from src.session.manager import (
        get_profile as _get_profile,
    )
    from src.session.manager import (
        get_redis as _get_redis,
    )
    from src.session.manager import (
        get_ui_language as _get_ui_lang,
    )

    try:
        _redis = await _get_redis()
        _profile = await _get_profile(_redis, settings.allowed_user_ids[0])
        ui_lang = _get_ui_lang(_profile)
    except Exception:
        ui_lang = "ru"

    if not msgs:
        return t("save.empty_result", ui_lang)

    logger.info("pipeline start", session_id=session.session_id, msgs=len(msgs))

    # Seal the literal transcript BEFORE any compaction or extraction. Transcript
    # is the immutable source of truth for "what was said" — it must survive even
    # if extract() or apply_to_vault() raise. See docs/adr/0001-memory-layers.md.
    try:
        from src.session.manager import (
            get_notes_language as _get_notes_lang,
        )
        from src.vault import transcripts

        _notes_lang = _get_notes_lang(_profile)
        await transcripts.seal_session(
            session.session_id,
            msgs,
            lang=_notes_lang,
            started_at=session.started_at,
            ended_at=session.last_msg_at,
        )
    except Exception as exc:
        logger.warning(
            "transcript seal failed (continuing pipeline)",
            session_id=session.session_id,
            error=str(exc),
        )

    # Compact if very long (> 50 msgs — summarise older ones)
    if len(msgs) > 50:
        if notify:
            await notify(t("save.compressing", ui_lang))
        msgs = await _compact_msgs(msgs)

    try:
        extraction = await extract(msgs, session_id=session.session_id)
    except Exception as exc:
        logger.error("extraction failed", error=str(exc))
        return t("pipeline.extract_failed", ui_lang, error=str(exc))

    try:
        created = await apply_to_vault(extraction, session.session_id)
    except Exception as exc:
        logger.error("vault apply failed", error=str(exc))
        return t("pipeline.apply_failed", ui_lang, error=str(exc))

    # Clear filled slots now that they've been consumed by extraction. Pending
    # slots are user-scoped (not session-scoped) and survive across sessions
    # intentionally, so a 24h-pending slot answered after a session boundary
    # still binds.
    try:
        from src.session import slots as _slots
        from src.session.manager import get_redis as _redis_for_cleanup

        _r = await _redis_for_cleanup()
        await _slots.clear_filled(_r, session.session_id)
    except Exception as exc:
        logger.warning("filled slots cleanup failed", error=str(exc))

    if extraction.open_questions:
        items = "\n".join(f"• {q}" for q in extraction.open_questions)
        open_q = t("pipeline.open_questions_block", ui_lang, items=items)
    else:
        open_q = ""
    summary = t(
        "pipeline.summary",
        ui_lang,
        count=len(created),
        topic=extraction.topic,
        summary=extraction.summary,
        open_q=open_q,
    )
    logger.info("pipeline done", session_id=session.session_id, notes=len(created))
    return summary


_COMPACT_PROMPT = {
    "ru": "Сожми этот диалог в плотный нарратив, сохрани все факты.",
    "en": "Compress this dialog into a dense narrative, preserving all facts.",
    "uz": "Bu muloqotni zich hikoyaga siqib, barcha faktlarni saqla.",
}


async def _compact_msgs(msgs: list[SessionMessage]) -> list[SessionMessage]:
    """Summarise the first 40 messages into one, keep the last 10 verbatim."""
    from src.session.manager import (
        get_notes_language as _get_notes_lang,
    )
    from src.session.manager import (
        get_profile as _get_profile,
    )
    from src.session.manager import (
        get_redis as _get_redis,
    )

    try:
        _redis = await _get_redis()
        _profile = await _get_profile(_redis, settings.allowed_user_ids[0])
        notes_lang = _get_notes_lang(_profile)
    except Exception:
        notes_lang = "ru"

    client = get_client()
    head = msgs[:-10]
    tail = msgs[-10:]
    conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in head)
    resp = await client.chat.completions.create(
        model=settings.openai_model_fast,
        messages=[
            {
                "role": "system",
                "content": _COMPACT_PROMPT.get(notes_lang, _COMPACT_PROMPT["ru"]),
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
