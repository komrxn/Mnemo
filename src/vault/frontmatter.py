from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from slugify import slugify

NoteType = Literal[
    "task",
    "thought",
    "person",
    "job",
    "project",
    "memory",
    "theme",
    "daily",
    "inbox",
    "transcript",
]
NoteStatus = Literal["open", "done", "archived"]

# forward_field -> (inverse_field_on_target, is_list_on_target)
RELATION_INVERSE: dict[str, tuple[str, bool]] = {
    "works_at": ("employs", True),
    "themes": ("examples", True),
    "about_person": ("mentions", True),
    "related_people": ("involved_in", True),
    "parent_theme": ("sub_themes", True),
    # for_job and for_project have dynamic inverses — handled by get_inverse_field()
    # owner — intentionally no inverse (would flood owner.md)
}

LINK_FIELDS: list[str] = [
    "owner",
    "works_at",
    "for_job",
    "for_project",
    "themes",
    "about_person",
    "related_people",
    "parent_theme",
    "employs",
    "projects",
    "tasks",
    "examples",
    "mentions",
    "involved_in",
    "sub_themes",
    "memories",
    "thoughts",
]

# Прямые типизированные связи — single source of truth для конвертера LightRAG.
# Обратные поля (employs, projects, tasks, ...) не включаются — это денормализация
# для Obsidian, не для графа.
FORWARD_RELATIONS: frozenset[str] = frozenset(
    {
        "owner",
        "works_at",
        "for_job",
        "for_project",
        "themes",
        "about_person",
        "related_people",
        "parent_theme",
    }
)


def get_inverse_field(forward: str, source_type: str) -> str | None:
    """Return the inverse field name on the target note, or None if no inverse."""
    if forward == "for_project":
        return {"task": "tasks", "memory": "memories", "thought": "thoughts"}.get(source_type)
    if forward == "for_job":
        return {"task": "tasks", "project": "projects"}.get(source_type)
    inv = RELATION_INVERSE.get(forward)
    return inv[0] if inv else None


TYPE_FOLDERS: dict[str, str] = {
    "task": "50_Tasks",
    "thought": "60_Thoughts",
    "person": "20_People",
    "job": "30_Jobs",
    "project": "40_Projects",
    "memory": "70_Memories",
    "theme": "80_Themes",
    "daily": "10_Daily",
    "inbox": "00_Inbox",
}


class Frontmatter(BaseModel):
    type: NoteType = "inbox"
    created: datetime = datetime.now(UTC)
    updated: datetime = datetime.now(UTC)
    tags: list[str] = []
    status: NoteStatus | None = None
    due: str | None = None
    links: list[str] = []
    session_id: str = ""
    aliases: list[str] = []
    is_owner: bool = False


class Note(BaseModel):
    path: str
    frontmatter: Frontmatter
    body: str


def parse(raw: str) -> tuple[dict[str, Any], str]:
    """Split raw file content into (frontmatter_dict, body)."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    fm: dict[str, Any] = yaml.safe_load(parts[1]) or {}
    return fm, parts[2].lstrip("\n")


def serialize(fm: dict[str, Any], body: str) -> str:
    fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{fm_yaml}---\n\n{body}"


def make_note_path(type_: str, title: str) -> str:
    """Generate vault-relative path for a note given its type and title.

    Uses Unicode-aware slugify so Cyrillic titles stay readable in Obsidian
    file browser ("аня-петрова.md" vs the transliterated "anya-petrova.md").
    """
    folder = TYPE_FOLDERS.get(type_, "00_Inbox")
    if type_ == "daily":
        from zoneinfo import ZoneInfo

        from src.config import settings

        tz = ZoneInfo(settings.tz)
        date = datetime.now(tz).strftime("%Y-%m-%d")
        return f"{folder}/{date}.md"
    slug = slugify(title, allow_unicode=True, separator="-", lowercase=True)
    return f"{folder}/{slug}.md"


def make_unique_note_path(type_: str, title: str) -> str:
    """Like `make_note_path` but guarantees the result doesn't exist yet.

    Used when the coherence gate refuses an append: instead of bleeding
    facts into the dedup-matched existing note, we need a fresh sibling
    note. Appends `-2`, `-3`, ... until the path is free.
    """
    from pathlib import Path

    from src.config import settings

    base = make_note_path(type_, title)
    vault = Path(settings.vault_path)
    if not (vault / base).exists():
        return base
    stem = base.removesuffix(".md")
    for n in range(2, 100):
        candidate = f"{stem}-{n}.md"
        if not (vault / candidate).exists():
            return candidate
    # Pathological collision — fall through to the suffixed candidate even
    # if it would overwrite; better than an infinite loop.
    return f"{stem}-{n}.md"
