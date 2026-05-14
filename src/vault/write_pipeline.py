"""Single write pipeline — every vault write goes through here.

Replaces two parallel write paths (onboarding `_create_note` and extractor
`_apply_entity`) with one: `write_entity(entity)`. Steps:

  1. dedup_check → if similar exists, route to it instead of creating
  2. validate_relations → drop relations whose targets don't exist
  3. render_body programmatically (no agent-supplied markdown)
  4. write_note or append_to_note
  5. add_typed_link for non-owner relations (generates inverse)
  6. enqueue LightRAG reindex
"""

from __future__ import annotations

import structlog

from src.vault import reader, writer
from src.vault.dedup import find_similar
from src.vault.entity import Entity, render_body, split_relations
from src.vault.frontmatter import make_note_path
from src.vault.paths import is_inside_vault, resolve_target_path

logger = structlog.get_logger()

# Score thresholds: ≥ HARD → must reuse, BLOCK new creation.
#                   ≥ SOFT → log warning but allow.
_DEDUP_HARD = 85
_DEDUP_SOFT = 70
# Person merges are catastrophic (links are bidirectional, undo is manual),
# so we ratchet the gate higher AND we already use Levenshtein-only scoring
# for persons in `find_similar` (no substring boost). With those two combined,
# "Лола" vs "Хилола" drops to 80 — below the 92 gate — and stays as a SOFT
# warning instead of silently rerouting mother's note into daughter's.
_DEDUP_HARD_PERSON = 92


def _hard_threshold(note_type: str) -> int:
    return _DEDUP_HARD_PERSON if note_type == "person" else _DEDUP_HARD


class WriteResult:
    """Outcome of write_entity."""

    def __init__(self, *, path: str, action: str, sha: str = "") -> None:
        self.path = path
        self.action = action  # "created" | "appended" | "deduped"
        self.sha = sha


async def write_entity(entity: Entity, session_id: str = "") -> WriteResult:
    """Write an `Entity` to vault, with dedup and inverse-link generation.

    Returns WriteResult describing what happened. Never raises on relation
    issues — invalid relations are logged and skipped, not fatal.
    """
    from src.session.manager import get_notes_language

    # Owner-name guard: if canonical_name contains owner_name, the agent
    # probably wrote a description like "профиль komron khakimov" instead
    # of a real entity name. We can't catch this in pydantic Entity
    # validator (no profile context), but we can here.
    notes_lang = "ru"
    try:
        from src.config import settings as _s
        from src.session.manager import get_profile, get_redis

        _redis = await get_redis()
        _user_id = _s.allowed_user_ids[0]
        _profile = await get_profile(_redis, _user_id)
        notes_lang = get_notes_language(_profile)
        owner_name = str(_profile.get("owner_name", "")).strip()
        if owner_name and len(owner_name) >= 3:
            low_name = entity.canonical_name.lower()
            low_owner = owner_name.lower()
            if low_owner in low_name and len(low_name) > len(low_owner) + 5:
                # Description like "профиль komron khakimov" — refuse
                logger.warning(
                    "entity name contains owner_name (likely a description)",
                    name=entity.canonical_name,
                    owner_name=owner_name,
                )
                return WriteResult(path="", action="rejected_owner_name")
    except Exception:
        pass  # profile read failure → skip the guard, don't block writes

    rel_path = make_note_path(entity.type, entity.canonical_name)

    # Step 1 — dedup
    candidates = await find_similar(
        entity.type, entity.canonical_name, entity.aliases, threshold=_DEDUP_SOFT
    )
    if candidates and candidates[0].score >= _hard_threshold(entity.type):
        existing_path = candidates[0].path
        if existing_path != rel_path:
            logger.info(
                "write_pipeline dedup: routing to existing",
                wanted=rel_path,
                existing=existing_path,
                score=candidates[0].score,
            )
            rel_path = existing_path
            # Merge: agent's name becomes an alias of canonical existing note
            new_aliases = sorted({*entity.aliases, entity.canonical_name})
            try:
                from pathlib import Path

                from src.config import settings
                from src.vault.frontmatter import parse

                raw = (Path(settings.vault_path) / existing_path).read_text("utf-8")
                fm_old, _ = parse(raw)
                existing_aliases = fm_old.get("aliases", []) or []
                merged = sorted({*existing_aliases, *new_aliases})
                await writer.update_frontmatter(existing_path, {"aliases": merged}, session_id)
            except Exception as exc:
                logger.warning("alias merge failed", error=str(exc))

    # Step 2 — body
    body = render_body(entity, notes_lang=notes_lang)

    # Step 3 — frontmatter (singles + aliases + type)
    # Owner-anchor is now guaranteed at Entity level (model_validator), so
    # split_relations always returns owner in fm_singles for non-person types.
    fm_singles, typed_links = split_relations(entity.relations)
    fm: dict[str, object] = {"type": entity.type, "aliases": entity.aliases}
    fm.update(fm_singles)

    # Step 4 — write or append
    if reader.note_exists(rel_path):
        # Existing note — append body and merge frontmatter
        if entity.facts:
            await writer.append_to_note(rel_path, _facts_block(entity, notes_lang), session_id)
        await writer.update_frontmatter(rel_path, fm, session_id)
        action = "appended"
        sha = "0" * 8  # update returns sha; we re-fetch after
    else:
        sha = await writer.write_note(rel_path, body, fm, session_id)
        action = "created"

    # Step 5 — typed links (non-owner) with inverse generation
    if typed_links:
        from src.vault.linking import add_typed_link

        for field, target_raw in typed_links:
            resolved = resolve_target_path(target_raw)
            if resolved is None:
                logger.warning(
                    "relation target not found, skipped",
                    from_=rel_path,
                    target=target_raw,
                    field=field,
                )
                continue
            if not is_inside_vault(resolved):
                logger.warning(
                    "relation target outside vault, rejected",
                    from_=rel_path,
                    target=target_raw,
                )
                continue
            try:
                await add_typed_link(rel_path, resolved, field, session_id)
            except Exception as exc:
                logger.warning(
                    "typed_link failed",
                    from_=rel_path,
                    to=resolved,
                    field=field,
                    error=str(exc),
                )

    # Step 6 — reindex (debounced)
    try:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue([rel_path])
    except Exception as exc:
        logger.warning("reindex enqueue skipped", path=rel_path, error=str(exc))

    return WriteResult(path=rel_path, action=action, sha=sha)


def _facts_block(entity: Entity, notes_lang: str = "ru") -> str:
    """Produce just the facts block (used when appending to existing note)."""
    from src.vault.section_headers import facts_header

    if not entity.facts:
        return entity.one_liner
    lines = [entity.one_liner, "", facts_header(notes_lang), ""]
    for fact in entity.facts:
        lines.append(f"- {fact}")
    return "\n".join(lines)
