"""Compact vault snapshot for LLM system prompts.

Closes Group A (blind writes): when extractor / onboarding agent gets a
short list of *existing* entities (canonical_name + aliases), it stops
generating parallel duplicates ("AI" vs "Искусственный интеллект").

Output is intentionally short (≤ 30 entries per type) so token cost stays low.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from src.config import settings
from src.vault.frontmatter import TYPE_FOLDERS, parse

logger = structlog.get_logger()

# Types that matter for graph navigation. inbox/daily/attachments are skipped.
_GRAPH_TYPES = ("person", "job", "project", "task", "thought", "memory", "theme")
_PER_TYPE_LIMIT = 30


def build_vault_map() -> str:
    """Return a compact text summary of existing vault entities.

    Format:
        Existing notes in vault (use these instead of creating duplicates):

        Jobs:
          - 30_Jobs/it-park-university (alias: ITPU)
          - 30_Jobs/baraka-ai

        Themes:
          - 80_Themes/ai (alias: AI, ИИ)
          ...

    Empty vault → returns explanatory empty marker, not silence.
    """
    vault = Path(settings.vault_path)
    if not vault.exists():
        return "Vault is empty (no notes yet)."

    by_type: dict[str, list[tuple[str, list[str]]]] = {t: [] for t in _GRAPH_TYPES}

    for note_type in _GRAPH_TYPES:
        folder = TYPE_FOLDERS.get(note_type)
        if not folder:
            continue
        folder_path = vault / folder
        if not folder_path.exists():
            continue
        for md in sorted(folder_path.rglob("*.md")):
            rel = str(md.relative_to(vault))
            try:
                raw = md.read_text(encoding="utf-8")
                fm, _ = parse(raw)
                aliases: list[str] = fm.get("aliases", []) or []
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("vault_map skipped note", path=rel, error=str(exc))
                continue
            by_type[note_type].append((rel.removesuffix(".md"), aliases))

    if not any(by_type.values()):
        return "Vault is empty (no notes yet). You can freely create new entities."

    lines = ["Existing notes in vault (USE these instead of creating duplicates):", ""]
    pretty_label = {
        "person": "People",
        "job": "Jobs",
        "project": "Projects",
        "task": "Tasks",
        "thought": "Thoughts",
        "memory": "Memories",
        "theme": "Themes",
    }
    for note_type in _GRAPH_TYPES:
        items = by_type[note_type]
        if not items:
            continue
        lines.append(f"{pretty_label[note_type]}:")
        for path, aliases in items[:_PER_TYPE_LIMIT]:
            ali_str = f" (alias: {', '.join(aliases)})" if aliases else ""
            lines.append(f"  - {path}{ali_str}")
        if len(items) > _PER_TYPE_LIMIT:
            lines.append(f"  - … and {len(items) - _PER_TYPE_LIMIT} more")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
