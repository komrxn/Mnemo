"""Unit tests for the strict Entity contract — covers former defense bugs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.vault.entity import Entity, Relation, render_body, split_relations


def test_entity_rejects_third_person_canonical_name() -> None:
    """`у-пользователя-есть-X` style names blocked at validation."""
    with pytest.raises(ValidationError, match="description"):
        Entity(
            type="memory",
            canonical_name="у пользователя есть pet projects",
            one_liner="У него куча проектов.",
        )


def test_entity_rejects_markdown_in_one_liner() -> None:
    with pytest.raises(ValidationError):
        Entity(
            type="project",
            canonical_name="Mnemo",
            one_liner="# Mnemo project",
        )


def test_entity_rejects_newlines_in_one_liner() -> None:
    with pytest.raises(ValidationError):
        Entity(
            type="project",
            canonical_name="Mnemo",
            one_liner="Line1\nLine2",
        )


def test_entity_rejects_yaml_marker_in_one_liner() -> None:
    with pytest.raises(ValidationError):
        Entity(
            type="project",
            canonical_name="Mnemo",
            one_liner="--- yaml separator",
        )


def test_entity_rejects_too_short_one_liner() -> None:
    with pytest.raises(ValidationError):
        Entity(type="project", canonical_name="Mnemo", one_liner="too short")


def test_entity_normalizes_facts() -> None:
    """Facts split multi-line, strip leading dashes."""
    e = Entity(
        type="project",
        canonical_name="Mnemo",
        one_liner="Personal AI assistant with eternal memory.",
        facts=["- ML-pipeline", "* uses LightRAG\n* uses Obsidian"],
    )
    assert e.facts == ["ML-pipeline", "uses LightRAG", "uses Obsidian"]


def test_relation_strips_wikilink_brackets() -> None:
    """Quad-brackets and single-brackets normalize to bare path."""
    r1 = Relation(field="owner", target_path="[[[[_meta/owner]]]]")
    r2 = Relation(field="owner", target_path="[[_meta/owner]]")
    r3 = Relation(field="owner", target_path="_meta/owner.md")

    assert r1.target_path == "_meta/owner"
    assert r2.target_path == "_meta/owner"
    assert r3.target_path == "_meta/owner.md"


def test_split_relations_owner_separate() -> None:
    """`owner` goes to fm-singles (no inverse), other relations to typed_links."""
    e = Entity(
        type="project",
        canonical_name="Mnemo",
        one_liner="Personal AI assistant with eternal memory.",
        relations=[
            Relation(field="owner", target_path="_meta/owner"),
            Relation(field="for_job", target_path="30_Jobs/legai.md"),
            Relation(field="themes", target_path="80_Themes/ai.md"),
            Relation(field="themes", target_path="80_Themes/forbes.md"),
        ],
    )
    fm_singles, typed_links = split_relations(e.relations)
    assert fm_singles == {"owner": "[[_meta/owner]]"}
    assert ("for_job", "30_Jobs/legai.md") in typed_links
    assert sum(1 for f, _ in typed_links if f == "themes") == 2


def test_render_body_no_link_section() -> None:
    """render_body produces clean markdown — no `## Связи`, no wikilinks."""
    e = Entity(
        type="project",
        canonical_name="Mnemo",
        one_liner="Personal AI assistant with eternal memory.",
        facts=["uses LightRAG", "uses Obsidian"],
    )
    body = render_body(e)
    assert "## Связи" not in body
    assert "[[" not in body
    assert "## Факты" in body
    assert "uses LightRAG" in body


def test_render_body_no_facts_just_one_liner() -> None:
    e = Entity(
        type="project",
        canonical_name="Mnemo",
        one_liner="Personal AI assistant with eternal memory.",
    )
    body = render_body(e)
    assert body.strip() == "Personal AI assistant with eternal memory."


def test_aliases_max_length() -> None:
    with pytest.raises(ValidationError):
        Entity(
            type="project",
            canonical_name="Mnemo",
            one_liner="Personal AI assistant with eternal memory.",
            aliases=[f"alias{i}" for i in range(15)],
        )
