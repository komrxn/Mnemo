from __future__ import annotations

from pathlib import Path

import structlog

from src.config import settings
from src.vault import git_ops, reader
from src.vault.frontmatter import LINK_FIELDS, get_inverse_field, parse, serialize

logger = structlog.get_logger()

_LIST_FIELDS = frozenset({
    "themes", "related_people", "employs", "projects", "tasks",
    "examples", "mentions", "involved_in", "sub_themes", "memories", "thoughts",
})


async def add_typed_link(
    from_path: str,
    to_path: str,
    relation: str,
    session_id: str = "",
) -> bool:
    """Add a typed relation from from_path to to_path. Returns True if link was added."""
    if from_path == to_path:
        return False
    if not reader.note_exists(from_path) or not reader.note_exists(to_path):
        logger.warning("typed link skipped: missing note", from_=from_path, to=to_path)
        return False

    wikilink_to = f"[[{to_path.removesuffix('.md')}]]"
    vault = Path(settings.vault_path)

    from_full = vault / from_path
    raw = from_full.read_text(encoding="utf-8")
    fm, body = parse(raw)

    is_list = relation in _LIST_FIELDS
    if is_list:
        existing: list[str] = fm.get(relation, []) or []
        if wikilink_to in existing:
            return False
        fm[relation] = sorted(set(existing + [wikilink_to]))
    else:
        if fm.get(relation) == wikilink_to:
            return False
        fm[relation] = wikilink_to

    body = render_links_section(fm, body)
    from_full.write_text(serialize(fm, body), encoding="utf-8")

    source_type = str(fm.get("type", ""))
    inverse = get_inverse_field(relation, source_type)
    paths_to_commit = [from_path]

    if inverse and to_path != "_meta/owner.md":
        to_full = vault / to_path
        raw_to = to_full.read_text(encoding="utf-8")
        fm_to, body_to = parse(raw_to)
        wikilink_from = f"[[{from_path.removesuffix('.md')}]]"
        existing_inv: list[str] = fm_to.get(inverse, []) or []
        if wikilink_from not in existing_inv:
            fm_to[inverse] = sorted(set(existing_inv + [wikilink_from]))
            body_to = render_links_section(fm_to, body_to)
            to_full.write_text(serialize(fm_to, body_to), encoding="utf-8")
            paths_to_commit.append(to_path)

    await git_ops.stage_and_commit(
        vault,
        paths_to_commit,
        f"link {from_path} -[{relation}]-> {to_path} [session={session_id}]",
    )
    return True


def render_links_section(fm: dict, body: str) -> str:  # type: ignore[type-arg]
    """Regenerate the '## Связи' section in body from LINK_FIELDS frontmatter."""
    marker = "\n## Связи\n"
    if marker in body:
        body = body.split(marker)[0].rstrip() + "\n"

    items: list[str] = []
    for field in LINK_FIELDS:
        v = fm.get(field)
        if not v:
            continue
        if isinstance(v, str):
            items.append(f"- **{field}**: {v}")
        elif isinstance(v, list):
            for item in v:
                items.append(f"- **{field}**: {item}")

    if not items:
        return body

    return body.rstrip() + "\n\n## Связи\n\n" + "\n".join(items) + "\n"
