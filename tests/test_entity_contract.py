"""Unit tests for the strict Entity contract — covers former defense bugs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.vault.entity import (
    Entity,
    Relation,
    extract_proper_noun_candidates,
    render_body,
    split_relations,
)

# ── proper-noun preservation (M4 of memory-layers plan) ──────────────────────


def test_extract_proper_nouns_catches_allcaps() -> None:
    tokens = extract_proper_noun_candidates("работаю в БЕК и GPT")
    assert "БЕК" in tokens
    assert "GPT" in tokens


def test_extract_proper_nouns_catches_internal_caps() -> None:
    tokens = extract_proper_noun_candidates("LegAI and iPhone are good")
    assert "LegAI" in tokens
    assert "iPhone" in tokens


def test_extract_proper_nouns_skips_plain_title_case() -> None:
    """Forbes / Mnemo / Анна survive normalization in practice — flagging them
    would produce too many false positives on ordinary capitalized words."""
    tokens = extract_proper_noun_candidates("Forbes Mnemo Анна Москва")
    # None of these have an internal cap or are all-caps
    assert "Forbes" not in tokens
    assert "Mnemo" not in tokens
    assert "Анна" not in tokens


def test_entity_rejects_when_proper_noun_dropped() -> None:
    """The BEK regression anchor — Entity construction MUST fail if a known
    proper noun from the source is missing from all fields."""
    with pytest.raises(ValidationError, match="proper nouns dropped"):
        Entity.model_validate(
            {
                "type": "job",
                "canonical_name": "ресторан",
                "aliases": [],
                "one_liner": "семейный ресторан.",
                "facts": [],
            },
            context={"source_tokens": {"БЕК"}},
        )


def test_entity_accepts_when_proper_noun_in_aliases() -> None:
    """Preserving the token in aliases is sufficient — name doesn't have to be it."""
    e = Entity.model_validate(
        {
            "type": "job",
            "canonical_name": "ресторан БЕК",
            "aliases": ["BEK"],
            "one_liner": "семейный ресторан.",
            "facts": [],
        },
        context={"source_tokens": {"БЕК", "BEK"}},
    )
    assert e.canonical_name == "ресторан БЕК"


def test_entity_accepts_when_proper_noun_in_facts() -> None:
    e = Entity.model_validate(
        {
            "type": "memory",
            "canonical_name": "переезд 2024",
            "aliases": [],
            "one_liner": "переехал в Лиссабон.",
            "facts": ["работал в LegAI до переезда"],
        },
        context={"source_tokens": {"LegAI"}},
    )
    assert e.canonical_name == "переезд 2024"


def test_entity_skips_check_without_source_tokens() -> None:
    """No context = legacy behavior. Existing callers don't need to change."""
    e = Entity(
        type="job",
        canonical_name="ресторан",
        one_liner="семейный ресторан.",
    )
    assert e.canonical_name == "ресторан"


def test_entity_lists_all_dropped_tokens_in_error() -> None:
    """If multiple tokens are missing, error message names them all (for retry)."""
    with pytest.raises(ValidationError) as excinfo:
        Entity.model_validate(
            {
                "type": "memory",
                "canonical_name": "переезд",
                "aliases": [],
                "one_liner": "большой переезд осенью.",
                "facts": [],
            },
            context={"source_tokens": {"БЕК", "LegAI"}},
        )
    msg = str(excinfo.value)
    assert "БЕК" in msg
    assert "LegAI" in msg


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
