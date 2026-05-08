from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import structlog

from src.config import settings
from src.vault.frontmatter import FORWARD_RELATIONS, parse

logger = structlog.get_logger()

_NOTE_TYPE_TO_ENTITY: dict[str, str] = {
    "person": "Person",
    "project": "Project",
    "task": "Task",
    "job": "Organization",
    "theme": "Theme",
    "memory": "Memory",
    "thought": "Thought",
}


def _entity_name_from_path(rel_path: str) -> str:
    """Стабильное имя сущности из vault path. Совпадает с src_id/tgt_id в relationships."""
    return rel_path.removesuffix(".md")


def _doc_id(rel_path: str, content: str) -> str:
    h = hashlib.sha256(f"{rel_path}|{content}".encode()).hexdigest()[:12]
    return f"doc-{h}"


def _strip_frontmatter_for_chunk(body: str, title: str) -> str:
    """Тело без YAML и без секции `## Связи` — для чистого embedding."""
    if "## Связи" in body:
        body = body.split("## Связи", 1)[0].rstrip()
    return f"# {title}\n\n{body}".strip()


def _wikilink_to_entity_name(wikilink: str) -> str | None:
    """`[[40_Projects/legai]]` → `40_Projects/legai`. Игнорирует pipe-aliases."""
    s = wikilink.strip()
    if not (s.startswith("[[") and s.endswith("]]")):
        return None
    inner = s[2:-2].split("|", 1)[0].strip()
    return inner or None


def vault_to_custom_kg(rel_paths: list[str]) -> dict[str, Any]:
    """Build a LightRAG custom_kg from a list of vault notes.

    Each note → 1 chunk + 1 entity + N relationships (one per forward link in frontmatter).
    daily and inbox notes are skipped — they are not graph entities.
    """
    vault = Path(settings.vault_path)
    chunks: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for rel in rel_paths:
        full = vault / rel
        if not full.exists() or full.suffix != ".md":
            continue
        raw = full.read_text(encoding="utf-8")
        fm, body = parse(raw)
        note_type = str(fm.get("type", "inbox"))
        if note_type in {"daily", "inbox"}:
            continue

        title = full.stem.replace("-", " ")
        clean_body = _strip_frontmatter_for_chunk(body, title)
        source_id = _doc_id(rel, clean_body)

        chunks.append(
            {
                "content": clean_body,
                "source_id": source_id,
                "file_path": rel,
            }
        )

        entity_name = _entity_name_from_path(rel)
        entity_type = _NOTE_TYPE_TO_ENTITY.get(note_type, "Concept")
        first_facts = "\n".join(
            line for line in body.splitlines() if line.strip().startswith("- ")
        )[:400]
        aliases = fm.get("aliases", []) or []
        description = (
            f"{title}. Aliases: {', '.join(aliases)}. {first_facts}"
            if aliases
            else f"{title}. {first_facts}"
        )

        entities.append(
            {
                "entity_name": entity_name,
                "entity_type": entity_type,
                "description": description.strip() or title,
                "source_id": source_id,
                "file_path": rel,
            }
        )

        for field in FORWARD_RELATIONS:
            v = fm.get(field)
            if not v:
                continue
            targets = v if isinstance(v, list) else [v]
            for tgt_wikilink in targets:
                tgt_name = _wikilink_to_entity_name(str(tgt_wikilink))
                if not tgt_name or tgt_name == entity_name:
                    continue
                relationships.append(
                    {
                        "src_id": entity_name,
                        "tgt_id": tgt_name,
                        "description": f"{title} {field.replace('_', ' ')} {tgt_name}",
                        "keywords": field,
                        "source_id": source_id,
                        "weight": 1.0,
                        "file_path": rel,
                    }
                )

    logger.info(
        "vault_to_custom_kg built",
        chunks=len(chunks),
        entities=len(entities),
        relationships=len(relationships),
    )
    return {"chunks": chunks, "entities": entities, "relationships": relationships}
