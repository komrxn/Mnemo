from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from src.config import settings
from src.vault import reader, writer, search as vsearch
from src.vault.frontmatter import NoteType, make_note_path, parse, serialize
from src.tools.registry import ToolDef, get_registry

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
    type: NoteType
    title: str
    body: str
    frontmatter: dict[str, Any] = {}
    links: list[str] = []


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


async def _create_note(p: CreateNoteParams, session_id: str = "") -> str:
    from src.vault.dedup import find_similar

    aliases: list[str] = p.frontmatter.get("aliases", []) if p.frontmatter else []
    candidates = await find_similar(p.type, p.title, aliases, threshold=85)

    if candidates and candidates[0].score >= 90:
        existing_path = candidates[0].path
        raw = (Path(settings.vault_path) / existing_path).read_text(encoding="utf-8")
        fm_dict, _ = parse(raw)
        existing_aliases: list[str] = fm_dict.get("aliases", []) or []
        merged = sorted(set(existing_aliases + [p.title] + aliases))
        await writer.update_frontmatter(existing_path, {"aliases": merged}, session_id)
        return (
            f"найден дубликат: {existing_path} (score={candidates[0].score}). "
            f"Используй append_to_note для дописывания фактов."
        )

    if candidates and candidates[0].score >= 70:
        cand_str = "\n".join(
            f"  - {c.path} (score={c.score})" for c in candidates[:3]
        )
        return (
            f"возможные дубликаты для {p.title!r}:\n{cand_str}\n"
            f"Если это та же сущность — используй append_to_note к существующей. "
            f"Если это другая сущность — повтори create_note с уточнённым title."
        )

    rel_path = make_note_path(p.type, p.title)
    fm: dict[str, Any] = {"type": p.type, **p.frontmatter}
    if "aliases" not in fm:
        fm["aliases"] = []

    body = p.body
    if p.links:
        link_section = "\n".join(f"[[{lnk}]]" for lnk in p.links)
        body = body.rstrip() + f"\n\n## Связи\n\n{link_section}"

    sha = await writer.write_note(rel_path, body, fm, session_id)
    return f"создано: {rel_path} (sha={sha[:8]})"


async def _append_to_note(p: AppendToNoteParams, session_id: str = "") -> str:
    if not reader.note_exists(p.path):
        return f"заметка {p.path!r} не найдена"
    sha = await writer.append_to_note(p.path, p.block, session_id)
    return f"дописано: {p.path} (sha={sha[:8]})"


async def _update_frontmatter(p: UpdateFrontmatterParams, session_id: str = "") -> str:
    if not reader.note_exists(p.path):
        return f"заметка {p.path!r} не найдена"
    sha = await writer.update_frontmatter(p.path, p.patch, session_id)
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
        # Append to existing links section or add one
        if "## Связи" in note.body:
            body = note.body.rstrip() + f"\n{wikilink}"
        else:
            body = note.body.rstrip() + f"\n\n## Связи\n\n{wikilink}"
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
        ("list_notes", "Список заметок с фильтрами по папке, типу, тегу", ListNotesParams, _list_notes),
        ("read_note", "Прочитать заметку: frontmatter + тело", ReadNoteParams, _read_note),
        ("search_notes", "Полнотекстовый поиск по vault через ripgrep", SearchNotesParams, _search_notes),
        ("create_note", "Создать новую заметку с автогенерацией пути по типу", CreateNoteParams, _create_note),
        ("append_to_note", "Дописать блок текста в существующую заметку", AppendToNoteParams, _append_to_note),
        ("update_frontmatter", "Обновить поля frontmatter заметки", UpdateFrontmatterParams, _update_frontmatter),
        ("add_link", "Добавить wikilink между заметками", AddLinkParams, _add_link),
        ("move_note", "Переместить заметку в другую папку/путь", MoveNoteParams, _move_note),
        ("delete_note", "Удалить заметку (требует подтверждения пользователя)", DeleteNoteParams, _delete_note),
        ("get_vault_tree", "Дерево папок vault с количеством заметок", GetVaultTreeParams, _get_vault_tree),
    ]
    for name, desc, cls, handler in specs:
        reg.register(ToolDef(name=name, description=desc, params_cls=cls, handler=handler))


_register()
