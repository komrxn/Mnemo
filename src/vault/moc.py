from __future__ import annotations

from pathlib import Path

import structlog

from src.config import settings
from src.vault import writer
from src.vault.frontmatter import parse

logger = structlog.get_logger()

MOC_TYPES: dict[str, str] = {
    "person": "_meta/MOC_People.md",
    "project": "_meta/MOC_Projects.md",
    "job": "_meta/MOC_Jobs.md",
    "theme": "_meta/MOC_Themes.md",
}

_FOLDER_FOR_TYPE: dict[str, str] = {
    "person": "20_People",
    "project": "40_Projects",
    "job": "30_Jobs",
    "theme": "80_Themes",
}


async def regenerate_moc(note_type: str, session_id: str = "") -> str | None:
    """Regenerate the MOC file for the given type. Returns MOC path or None."""
    moc_path = MOC_TYPES.get(note_type)
    folder_name = _FOLDER_FOR_TYPE.get(note_type)
    if moc_path is None or folder_name is None:
        return None

    vault = Path(settings.vault_path)
    folder_path = vault / folder_name
    if not folder_path.exists():
        return None

    active: list[str] = []
    archived: list[str] = []

    for md in sorted(folder_path.rglob("*.md")):
        rel = str(md.relative_to(vault))
        raw = md.read_text(encoding="utf-8")
        fm, body = parse(raw)
        title = md.stem.replace("-", " ")
        first_line = next((line for line in body.splitlines() if line.strip()), "")
        summary = first_line[:120]
        link = f"[[{rel.removesuffix('.md')}|{title}]]"
        entry = f"- {link} — {summary}" if summary else f"- {link}"
        if fm.get("status") == "archived":
            archived.append(entry)
        else:
            active.append(entry)

    body_parts = [
        f"Автоматически сгенерированная карта типа «{note_type}». "
        "Не редактируй — перезаписывается после каждой сессии.\n\n"
        f"## Активные ({len(active)})\n\n" + ("\n".join(active) if active else "_пусто_"),
    ]
    if archived:
        body_parts.append(f"\n\n## Архив ({len(archived)})\n\n" + "\n".join(archived))

    await writer.write_note(
        moc_path,
        "".join(body_parts),
        {"type": "inbox", "auto_generated": True, "moc_for": note_type},
        session_id,
    )
    return moc_path
