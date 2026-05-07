from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel
from rapidfuzz import fuzz

from src.config import settings
from src.vault.frontmatter import TYPE_FOLDERS, parse


class DedupCandidate(BaseModel):
    path: str
    title: str
    score: int  # 0-100


async def find_similar(
    note_type: str,
    title: str,
    aliases: list[str] | None = None,
    threshold: int = 70,
) -> list[DedupCandidate]:
    """Find existing notes of the same type with similar title or aliases.

    Returns candidates sorted by score descending.
    Only compares within the same note type folder — never cross-type.
    """
    folder = TYPE_FOLDERS.get(note_type)
    if not folder:
        return []

    vault = Path(settings.vault_path)
    folder_path = vault / folder
    if not folder_path.exists():
        return []

    def _scan() -> list[DedupCandidate]:
        queries = [title.lower()] + [a.lower() for a in (aliases or [])]
        candidates: dict[str, int] = {}

        for md_file in folder_path.rglob("*.md"):
            rel = str(md_file.relative_to(vault))
            raw = md_file.read_text(encoding="utf-8")
            fm, _ = parse(raw)
            existing_title = md_file.stem.replace("-", " ")
            existing_aliases: list[str] = fm.get("aliases", []) or []
            names = [existing_title.lower()] + [a.lower() for a in existing_aliases]

            best = 0
            for q in queries:
                for name in names:
                    score = int(fuzz.WRatio(q, name))
                    if score > best:
                        best = score

            if best >= threshold:
                candidates[rel] = max(candidates.get(rel, 0), best)

        return [
            DedupCandidate(path=p, title=Path(p).stem, score=s)
            for p, s in sorted(candidates.items(), key=lambda x: -x[1])
        ]

    return await asyncio.to_thread(_scan)
