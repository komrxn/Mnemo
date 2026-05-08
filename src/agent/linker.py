from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel

from src.agent.loop import get_client
from src.config import settings
from src.vault.frontmatter import parse

logger = structlog.get_logger()

RelationType = Literal[
    "owner",
    "works_at",
    "for_job",
    "for_project",
    "themes",
    "about_person",
    "related_people",
    "parent_theme",
]


class LinkProposal(BaseModel):
    from_path: str
    to_path: str
    relation: RelationType


class LinkBatch(BaseModel):
    links: list[LinkProposal]


async def propose_links(
    touched_paths: list[str],
    owner_path: str,
) -> list[LinkProposal]:
    """Ask LLM to propose typed links between touched notes and the existing vault."""
    if not touched_paths:
        return []

    touched_context = _build_touched_context(touched_paths)
    candidates_context = _build_candidates_context(touched_paths)
    if not candidates_context:
        return []

    system = (
        "Ты — построитель графа знаний на подхвате. Extractor уже извлёк сущности "
        "и проставил основные типизированные связи в их frontmatter. Твоя задача: "
        "найти СВЯЗИ КОТОРЫЕ EXTRACTOR УПУСТИЛ — между затронутыми сейчас "
        "заметками и существующим vault'ом.\n\n"
        "Допустимые типы связей и их семантика:\n"
        "- owner: «эта сущность — часть жизни владельца». Применяй для "
        "job/project/task/thought/memory/theme если у заметки нет owner. НЕ для person.\n"
        "- works_at: «X работает в Y». Только person → job.\n"
        "- for_job: «X делается в рамках работы Y». project/task → job.\n"
        "- for_project: «X относится к проекту Y». task/thought/memory → project.\n"
        "- themes: «X — про тему Y». project/thought/memory → theme (список).\n"
        "- about_person: «X — про человека Y». thought/memory → person.\n"
        "- related_people: «в X упоминается человек Y, не как главный». "
        "project/job/memory → person (список).\n"
        "- parent_theme: «X — подтема Y». theme → theme.\n\n"
        "Принципы:\n"
        f"- owner всегда указывает на: {owner_path}\n"
        "- person НЕ имеет owner-поля. Person связан транзитивно через "
        "works_at/about_person/related_people.\n"
        "- НЕ предлагай связи которые уже есть в frontmatter (existing_links "
        "показаны в контексте).\n"
        "- НЕ выдумывай. Только то что прямо следует из текста заметки.\n"
        "- НЕ возвращай связи где from_path == to_path.\n"
        "- Если уверенности нет — НЕ предлагай.\n"
        "- Один проход. Не пытайся переписать всё что сделал extractor — только "
        "дотяни упущенное.\n"
    )
    user = (
        f"=== Заметки этой сессии ===\n{touched_context}\n\n"
        f"=== Существующие заметки vault'а ===\n{candidates_context}\n\n"
        "Верни JSON по схеме LinkBatch."
    )

    client = get_client()
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_main,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=LinkBatch,
        )
    except Exception as exc:
        logger.warning("propose_links LLM call failed", error=str(exc))
        return []

    parsed = response.choices[0].message.parsed
    return parsed.links if parsed else []


async def apply_links(links: list[LinkProposal], session_id: str) -> int:
    """Apply link proposals via add_typed_link. Returns count of applied links."""
    from src.vault.linking import add_typed_link

    applied = 0
    for link in links:
        try:
            ok = await add_typed_link(
                from_path=link.from_path,
                to_path=link.to_path,
                relation=link.relation,
                session_id=session_id,
            )
            if ok:
                applied += 1
        except Exception as exc:
            logger.warning(
                "link apply failed",
                from_=link.from_path,
                to=link.to_path,
                relation=link.relation,
                error=str(exc),
            )
    return applied


def _build_touched_context(paths: list[str]) -> str:
    vault = Path(settings.vault_path)
    blocks: list[str] = []
    for rel in paths:
        full = vault / rel
        if not full.exists() or full.suffix != ".md":
            continue
        raw = full.read_text(encoding="utf-8")
        fm, body = parse(raw)
        snippet = " ".join(body.split()[:200])
        existing = _extract_existing_links(fm)
        blocks.append(
            f"--- {rel} ---\n"
            f"type: {fm.get('type', '?')}\n"
            f"aliases: {fm.get('aliases', [])}\n"
            f"existing_links: {existing}\n"
            f"body: {snippet}\n"
        )
    return "\n".join(blocks)


def _build_candidates_context(touched_paths: list[str]) -> str:
    vault = Path(settings.vault_path)
    touched_set = set(touched_paths)
    by_type: dict[str, list[str]] = {}

    for md in vault.rglob("*.md"):
        rel = str(md.relative_to(vault))
        if rel in touched_set:
            continue
        raw = md.read_text(encoding="utf-8")
        fm, _ = parse(raw)
        typ = str(fm.get("type", "inbox"))
        aliases: list[str] = fm.get("aliases", []) or []
        ali_str = f" (alias: {aliases})" if aliases else ""
        by_type.setdefault(typ, []).append(f"- {rel}{ali_str}")

    blocks: list[str] = []
    for typ, items in sorted(by_type.items()):
        blocks.append(f"## {typ}")
        blocks.extend(items[:30])
    return "\n".join(blocks)


def _extract_existing_links(fm: dict) -> list[str]:  # type: ignore[type-arg]
    keys = [
        "owner",
        "works_at",
        "for_job",
        "for_project",
        "themes",
        "about_person",
        "related_people",
        "parent_theme",
    ]
    result: list[str] = []
    for k in keys:
        v = fm.get(k)
        if isinstance(v, str) and v:
            result.append(v)
        elif isinstance(v, list):
            result.extend(v)
    return result
