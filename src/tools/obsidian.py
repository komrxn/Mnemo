from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from src.config import settings
from src.tools.registry import ToolDef, get_registry
from src.vault import reader, writer
from src.vault import search as vsearch
from src.vault.frontmatter import NoteType, parse, serialize

logger = structlog.get_logger()


# ── param models ──────────────────────────────────────────────────────────────


class ListNotesParams(BaseModel):
    folder: str = ""
    type: NoteType | None = None
    tag: str = ""
    limit: int = 50


class ReadNoteParams(BaseModel):
    path: str


class SearchNotesParams(BaseModel):
    query: str
    type: NoteType | None = None
    top_k: int = 10


class CreateNoteParams(BaseModel):
    """Strict-typed creation. The `entity` field carries the full Entity contract.

    Note for the agent: legacy `body` / `frontmatter` / `links` are kept as
    *optional* compatibility fields, but the canonical path is `entity`.
    Anything passed via `body`/`frontmatter`/`links` is best-effort merged
    into the Entity, then validated.
    """

    type: NoteType
    title: str
    body: str = ""
    frontmatter: dict[str, Any] = {}
    links: list[str] = []


class SearchExistingParams(BaseModel):
    type: NoteType
    query: str
    aliases: list[str] = []


class AppendToNoteParams(BaseModel):
    path: str
    block: str


class UpdateFrontmatterParams(BaseModel):
    path: str
    patch: dict[str, Any]


class AddLinkParams(BaseModel):
    from_path: str
    to_path: str
    bidirectional: bool = True


class MoveNoteParams(BaseModel):
    old_path: str
    new_path: str


class DeleteNoteParams(BaseModel):
    path: str


class GetVaultTreeParams(BaseModel):
    depth: int = 2


# ── handlers ──────────────────────────────────────────────────────────────────


async def _list_notes(p: ListNotesParams, session_id: str = "") -> str:
    files = reader.list_md_files(p.folder)
    results: list[str] = []
    vault = Path(settings.vault_path)
    for f in files:
        if len(results) >= p.limit:
            break
        rel = str(f.relative_to(vault))
        raw = f.read_text(encoding="utf-8")
        fm, _ = parse(raw)
        if p.type and fm.get("type") != p.type:
            continue
        if p.tag and p.tag not in fm.get("tags", []):
            continue
        results.append(f"{rel} [type={fm.get('type', '?')}]")
    return "\n".join(results) if results else "заметок нет"


async def _read_note(p: ReadNoteParams, session_id: str = "") -> str:
    if not reader.note_exists(p.path):
        return f"заметка {p.path!r} не найдена"
    note = reader.read_note(p.path)
    fm_lines = []
    for k, v in note.frontmatter.model_dump(exclude_none=True).items():
        fm_lines.append(f"  {k}: {v}")
    return "--- frontmatter ---\n" + "\n".join(fm_lines) + f"\n--- body ---\n{note.body}"


async def _search_notes(p: SearchNotesParams, session_id: str = "") -> str:
    hits = await vsearch.search(p.query, top_k=p.top_k)
    if p.type:
        hits = [h for h in hits if p.type in h["path"]]
    if not hits:
        return f"ничего не найдено по запросу {p.query!r}"
    return "\n".join(f"{h['path']}: {h['context']}" for h in hits)


# READ-before-WRITE enforcement: agent must call search_existing_entities
# within this many seconds before create_note for the same (type, query).
# If not, create_note refuses and points the agent at search.
_READ_GATE_TTL_SECONDS = 60


async def _record_search(note_type: str, query: str) -> None:
    """Mark that the agent has searched for this (type, query) recently."""
    from src.session.manager import get_redis

    redis = await get_redis()
    key = f"read_gate:{note_type}:{query.lower().strip()}"
    await redis.set(key, b"1", ex=_READ_GATE_TTL_SECONDS)


async def _check_search_was_done(note_type: str, query: str) -> bool:
    from src.session.manager import get_redis

    redis = await get_redis()
    key = f"read_gate:{note_type}:{query.lower().strip()}"
    return bool(await redis.get(key))


async def _search_existing_entities(p: SearchExistingParams, session_id: str = "") -> str:
    """Tool: returns existing entities of the same type that resemble query+aliases.

    Two-gate search:
      1) FUZZY (rapidfuzz) — catches typos, transliteration, case differences
      2) SEMANTIC (LightRAG embeddings) — catches synonyms, paraphrases,
         cross-language equivalents (e.g. "AI" ↔ "искусственный интеллект",
         "ЗОЖ" ↔ "здоровый образ жизни")

    Agent MUST call this before create_note. Marker stored in Redis with TTL=60s.
    """
    from src.vault.dedup import find_similar

    fuzzy = await find_similar(p.type, p.query, p.aliases, threshold=60)
    await _record_search(p.type, p.query)

    # Semantic gate via LightRAG — only chunks/entities, no LLM generation
    semantic_paths: list[str] = []
    try:
        from src.lightrag_svc.client import query as kg_query

        kg_text = await kg_query(
            f"{p.query} {' '.join(p.aliases)}".strip(),
            mode="mix",
            only_need_context=True,
            top_k=8,
        )
        # LightRAG context references file_path entries — extract paths matching this type's folder
        from src.vault.frontmatter import TYPE_FOLDERS

        type_folder = TYPE_FOLDERS.get(p.type, "")
        if type_folder:
            import re as _re

            for match in _re.finditer(rf"{_re.escape(type_folder)}/[\w\-./а-яёА-ЯЁ]+\.md", kg_text):
                path = match.group(0)
                if path not in semantic_paths and reader.note_exists(path):
                    semantic_paths.append(path)
    except Exception as exc:
        logger.warning("semantic search failed", error=str(exc))

    # Combine — fuzzy candidates take priority (have scores), semantic appended
    fuzzy_paths = {c.path for c in fuzzy}
    semantic_only = [p for p in semantic_paths if p not in fuzzy_paths][:5]

    if not fuzzy and not semantic_only:
        return f"ничего похожего не найдено в {p.type}. Можешь создавать новую."

    lines: list[str] = []
    if fuzzy:
        lines.append(f"Fuzzy match ({len(fuzzy)}):")
        for c in fuzzy[:5]:
            lines.append(f"  - {c.path} (score={c.score})")
    if semantic_only:
        lines.append(f"Semantic match ({len(semantic_only)}, через embeddings):")
        for path in semantic_only:
            lines.append(f"  - {path}")
    if any(c.score >= 85 for c in fuzzy):
        lines.append(
            "Fuzzy ≥85 → почти точно то же. Используй append_to_note или "
            "create_note с canonical_name = существующего."
        )
    elif fuzzy and any(c.score >= 70 for c in fuzzy):
        lines.append("Fuzzy 70-85 → возможно та же сущность. Решай по контексту.")
    if semantic_only:
        lines.append(
            "Semantic match — это синонимы/перефразирование. Если про то же — "
            "переиспользуй существующую заметку, не плоди дубль."
        )
    return "\n".join(lines)


async def _create_note(p: CreateNoteParams, session_id: str = "") -> str:
    """Create a note via the unified write_pipeline.

    Builds an Entity from legacy params (body/frontmatter/links) — pydantic
    validators will reject malformed input (markdown in body, third-person
    title, etc.) with a clear error message that the LLM can correct.
    """
    from src.vault.entity import ALL_RELATION_FIELDS, Entity, Relation
    from src.vault.write_pipeline import write_entity

    # READ-before-WRITE gate: refuse unless agent searched recently
    if not await _check_search_was_done(p.type, p.title):
        return (
            f"⛔ Сначала вызови search_existing_entities(type={p.type!r}, "
            f"query={p.title!r}). Это обязательно перед create_note чтобы не "
            f"плодить дубли. После search можешь повторить create_note."
        )

    aliases_in: list[str] = []
    relations_in: list[Relation] = []
    one_liner_fallback = ""

    if p.frontmatter:
        # aliases from frontmatter
        aliases_in = list(p.frontmatter.get("aliases", []) or [])
        # extract relation fields from frontmatter (legacy compat)
        for field, value in p.frontmatter.items():
            if field not in ALL_RELATION_FIELDS:
                continue
            targets = value if isinstance(value, list) else [value]
            for tgt in targets:
                if not isinstance(tgt, str):
                    continue
                # Strip wikilink wrapping
                s = tgt.strip()
                while s.startswith("[[") and s.endswith("]]"):
                    s = s[2:-2].strip()
                if "/" in s and not s.endswith(".md"):
                    s = s + ".md"
                if s:
                    relations_in.append(Relation(field=field, target_path=s))  # type: ignore[arg-type]

    # Body: take first non-empty line as one_liner; rest discarded (we render
    # programmatically from facts). If nothing usable, complain.
    body_lines = [ln.strip() for ln in p.body.splitlines() if ln.strip()]
    body_lines = [
        ln
        for ln in body_lines
        if not ln.startswith("---") and not ln.startswith("##") and not ln.startswith("#")
    ]
    # Drop wikilink-only lines (the fake ## Связи block)
    body_lines = [ln for ln in body_lines if not (ln.startswith("[[") and ln.endswith("]]"))]
    if body_lines:
        one_liner_fallback = body_lines[0][:200]

    if not one_liner_fallback:
        return (
            "⛔ body пустой или состоит только из YAML/wikilinks. "
            "Передай в body конкретное описание сущности одним предложением "
            "(что это, зачем, в каком статусе)."
        )

    # M4: feed proper-noun source_tokens from filled slots — the Entity
    # validator will reject this create if a slot literal (e.g. "БЕК") was
    # registered earlier but isn't preserved in title/aliases/one_liner.
    source_tokens = await _source_tokens_from_slots(session_id)

    try:
        entity = Entity.model_validate(
            {
                "type": p.type,
                "canonical_name": p.title,
                "aliases": aliases_in,
                "one_liner": one_liner_fallback,
                "facts": [],
                "relations": [r.model_dump() for r in relations_in],
            },
            context={"source_tokens": source_tokens},
        )
    except Exception as exc:
        return f"⛔ невалидные параметры: {exc}"

    result = await write_entity(entity, session_id)
    return f"{result.action}: {result.path}"


async def _source_tokens_from_slots(session_id: str) -> set[str]:
    """Extract proper-noun tokens from any filled-slot literals for this session.

    Returns empty set when session_id is empty or there are no filled slots —
    in which case the Entity validator's proper-noun guard is silent (legacy
    behavior preserved).
    """
    if not session_id:
        return set()
    try:
        from src.session import slots
        from src.session.manager import get_redis
        from src.vault.entity import extract_proper_noun_candidates

        redis = await get_redis()
        filled = await slots.list_filled(redis, session_id)
        tokens: set[str] = set()
        for f in filled:
            tokens |= extract_proper_noun_candidates(f.literal_value)
        return tokens
    except Exception as exc:
        logger.warning("source_tokens fetch failed", session_id=session_id, error=str(exc))
        return set()


async def _enqueue_index(paths: list[str]) -> None:
    """Schedule LightRAG re-index for the given vault paths (debounced)."""
    try:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(paths)
    except Exception as exc:
        logger.warning("reindex enqueue skipped", paths=paths, error=str(exc))


async def _append_to_note(p: AppendToNoteParams, session_id: str = "") -> str:
    if not reader.note_exists(p.path):
        return f"заметка {p.path!r} не найдена"
    sha = await writer.append_to_note(p.path, p.block, session_id)
    await _enqueue_index([p.path])
    return f"дописано: {p.path} (sha={sha[:8]})"


async def _update_frontmatter(p: UpdateFrontmatterParams, session_id: str = "") -> str:
    if not reader.note_exists(p.path):
        return f"заметка {p.path!r} не найдена"
    sha = await writer.update_frontmatter(p.path, p.patch, session_id)
    await _enqueue_index([p.path])
    return f"frontmatter обновлён: {p.path} (sha={sha[:8]})"


async def _add_link(p: AddLinkParams, session_id: str = "") -> str:
    results = []
    for src, dst in [(p.from_path, p.to_path)] + (
        [(p.to_path, p.from_path)] if p.bidirectional else []
    ):
        if not reader.note_exists(src):
            results.append(f"{src!r} не найдена, пропущено")
            continue
        note = reader.read_note(src)
        wikilink = f"[[{dst}]]"
        if wikilink in note.body:
            results.append(f"{src}: ссылка уже есть")
            continue
        # Append to existing links section (any language) or add one
        from src.vault.section_headers import find_links_marker, links_header

        existing_marker = find_links_marker(note.body)
        if existing_marker:
            body = note.body.rstrip() + f"\n{wikilink}"
        else:
            # New section uses notes_language from profile (best-effort)
            try:
                from src.config import settings as _s
                from src.session.manager import (
                    get_notes_language as _gnl,
                )
                from src.session.manager import (
                    get_profile as _gp,
                )
                from src.session.manager import (
                    get_redis as _gr,
                )

                _r = await _gr()
                _p = await _gp(_r, _s.allowed_user_ids[0])
                _nl = _gnl(_p)
            except Exception:
                _nl = "ru"
            body = note.body.rstrip() + f"\n\n{links_header(_nl)}\n\n{wikilink}"
        raw = (Path(settings.vault_path) / src).read_text(encoding="utf-8")
        fm_dict, _ = parse(raw)
        (Path(settings.vault_path) / src).write_text(serialize(fm_dict, body), encoding="utf-8")
        from src.vault import git_ops

        await git_ops.stage_and_commit(
            Path(settings.vault_path), [src], f"add-link {src} -> {dst} [session={session_id}]"
        )
        results.append(f"добавлена ссылка {src} -> {dst}")
    return "\n".join(results)


async def _move_note(p: MoveNoteParams, session_id: str = "") -> str:
    if not reader.note_exists(p.old_path):
        return f"заметка {p.old_path!r} не найдена"
    sha = await writer.move_note(p.old_path, p.new_path, session_id)
    return f"перенесено: {p.old_path} -> {p.new_path} (sha={sha[:8]})"


async def _delete_note(p: DeleteNoteParams, session_id: str = "") -> str:
    from src.safety.confirmations import request_confirmation

    user_id = settings.allowed_user_ids[0]
    confirmed = await request_confirmation(user_id, f"Удалить заметку {p.path!r}?")
    if not confirmed:
        return "удаление отменено"
    sha = await writer.delete_note(p.path, confirmed=True, session_id=session_id)
    return f"удалено: {p.path} (sha={sha[:8]})"


async def _get_vault_tree(p: GetVaultTreeParams, session_id: str = "") -> str:
    vault = Path(settings.vault_path)
    lines: list[str] = []

    def _walk(path: Path, depth: int, prefix: str) -> None:
        if depth < 0 or not path.exists():
            return
        for item in sorted(path.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*.md"))
                lines.append(f"{prefix}{item.name}/ ({count})")
                _walk(item, depth - 1, prefix + "  ")
            elif item.suffix == ".md" and depth > 0:
                lines.append(f"{prefix}{item.name}")

    _walk(vault, p.depth, "")
    return "\n".join(lines) if lines else "vault пустой"


# ── registration ──────────────────────────────────────────────────────────────


def _register() -> None:
    reg = get_registry()
    specs = [
        (
            "list_notes",
            "Список заметок с фильтрами по папке, типу, тегу",
            ListNotesParams,
            _list_notes,
        ),
        ("read_note", "Прочитать заметку: frontmatter + тело", ReadNoteParams, _read_note),
        (
            "search_notes",
            "Полнотекстовый поиск по vault через ripgrep",
            SearchNotesParams,
            _search_notes,
        ),
        (
            "search_existing_entities",
            "ОБЯЗАТЕЛЬНО вызови перед create_note. Возвращает существующие "
            "сущности того же типа похожие на query — чтобы не плодить дубли.",
            SearchExistingParams,
            _search_existing_entities,
        ),
        (
            "create_note",
            "Создать новую заметку. ТРЕБУЕТ предварительного search_existing_entities. "
            "body = одно предложение про сущность. frontmatter = типизированные связи.",
            CreateNoteParams,
            _create_note,
        ),
        (
            "append_to_note",
            "Дописать блок текста в существующую заметку",
            AppendToNoteParams,
            _append_to_note,
        ),
        (
            "update_frontmatter",
            "Обновить поля frontmatter заметки",
            UpdateFrontmatterParams,
            _update_frontmatter,
        ),
        ("add_link", "Добавить wikilink между заметками", AddLinkParams, _add_link),
        ("move_note", "Переместить заметку в другую папку/путь", MoveNoteParams, _move_note),
        (
            "delete_note",
            "Удалить заметку (требует подтверждения пользователя)",
            DeleteNoteParams,
            _delete_note,
        ),
        (
            "get_vault_tree",
            "Дерево папок vault с количеством заметок",
            GetVaultTreeParams,
            _get_vault_tree,
        ),
    ]
    for name, desc, cls, handler in specs:
        reg.register(ToolDef(name=name, description=desc, params_cls=cls, handler=handler))


_register()
