"""Strict-typed entity contract — single source of truth for vault writes.

Replaces the freeform `body: str` + `links: list[str]` of `CreateNoteParams`
with structured fields that pydantic validates BEFORE the agent's input
reaches the filesystem. The result: most of the bug categories from the
audit (double frontmatter, quad-bracket wikilinks, third-person titles,
markdown-in-body) become structurally impossible.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from src.vault.frontmatter import NoteType

# Single-value relations: exactly one target each.
SINGLE_VALUE_RELATIONS = frozenset(
    {
        "owner",
        "works_at",
        "for_job",
        "for_project",
        "about_person",
        "parent_theme",
    }
)
# List-value relations: 0+ targets, may repeat the field across multiple Relations.
LIST_VALUE_RELATIONS = frozenset({"themes", "related_people"})

ALL_RELATION_FIELDS = SINGLE_VALUE_RELATIONS | LIST_VALUE_RELATIONS

RelationField = Literal[
    "owner",
    "works_at",
    "for_job",
    "for_project",
    "themes",
    "about_person",
    "related_people",
    "parent_theme",
]


class Relation(BaseModel):
    """One typed edge from this entity to a target note."""

    field: RelationField
    target_path: str = Field(
        min_length=3,
        max_length=200,
        description="vault-relative path to target, e.g. '30_Jobs/legai.md'",
    )

    @field_validator("target_path")
    @classmethod
    def _normalize(cls, v: str) -> str:
        s = v.strip().lstrip("/")
        # Strip wikilink wrapping if agent included it
        while s.startswith("[[") and s.endswith("]]"):
            s = s[2:-2].strip()
        return s


# Forbidden fragments in canonical_name — third-person/descriptive phrasings.
# These all signal "the agent wrote a sentence-summary instead of a name".
# Block at validation time; agent gets a clear error and retries.
_FORBIDDEN_NAME_FRAGMENTS = (
    # Third-person markers
    "пользовател",
    "владел",
    "у-меня",
    "у меня",
    # Compound descriptive phrasings (memories named like "X и Y и Z")
    " и ",
    "-и-",
    # Connecting prepositions are usually summarizing the contents
    "-как-",  # "X как Y" — descriptive
    " как ",
)


def _has_owner_name_fragment(name: str, owner_name: str | None = None) -> bool:
    """Check if `name` literally contains the owner_name (signals 3rd person)."""
    if not owner_name:
        return False
    return owner_name.lower() in name.lower() and len(name) > len(owner_name) + 5


# Proper-noun detection — closes the BEK bug. The LLM extractor sometimes
# drops uppercase / mixed-case tokens it doesn't recognize ("Ресторан БЕК"
# becomes "ресторан (семейный)"). We reject Entities that have dropped any
# such token from the source.
#
# Two patterns:
#   1. ALL-CAPS ≥2 letters — BEK, GPT, ИИ, FBI, LegAI's "AI" half.
#   2. Mixed-case with internal capital — LegAI, TikTok, iPhone, jPanel.
# Title-case words (Forbes, Mnemo, Anna) are NOT flagged: they survive
# normalization in practice, and flagging them produces too many false
# positives on ordinary capitalized words (Я, Это, The).
_ALLCAPS_RE = re.compile(r"\b[A-ZА-ЯЁ]{2,}\b")
_INTERNAL_CAP_RE = re.compile(
    r"\b[A-ZА-ЯЁa-zа-яё]*[a-zа-яё][A-ZА-ЯЁ][A-ZА-ЯЁa-zа-яё0-9]*\b"
)


def extract_proper_noun_candidates(text: str) -> set[str]:
    """Return tokens from `text` that look like preservation-critical proper nouns.

    Includes ALL-CAPS acronyms (БЕК, GPT) and mixed-case names with an
    internal capital (LegAI, iPhone). Excludes plain Title-case words.
    Returned tokens are stripped of trailing punctuation.
    """
    candidates: set[str] = set()
    for m in _ALLCAPS_RE.findall(text):
        candidates.add(m)
    for m in _INTERNAL_CAP_RE.findall(text):
        candidates.add(m)
    return candidates


class Entity(BaseModel):
    """The single contract for writing a note to vault.

    All fields are strictly typed — agents physically cannot smuggle markdown
    or YAML into structural slots. Body of the note is rendered programmatically
    from `one_liner` + `facts`.
    """

    type: NoteType
    canonical_name: str = Field(
        min_length=2,
        max_length=80,
        description="Short canonical name; becomes the filename slug.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Alternative names/spellings — used for dedup and search.",
    )
    one_liner: str = Field(
        min_length=10,
        max_length=200,
        description="One-sentence description of the entity. NO markdown, NO newlines.",
    )
    facts: list[str] = Field(
        default_factory=list,
        max_length=25,
        description="Bullet facts — one short statement per item, no markdown.",
    )
    relations: list[Relation] = Field(
        default_factory=list,
        max_length=20,
        description="Typed edges to other notes (replaces freeform wikilinks).",
    )

    @field_validator("canonical_name")
    @classmethod
    def _no_third_person(cls, v: str) -> str:
        low = v.lower().strip()
        for frag in _FORBIDDEN_NAME_FRAGMENTS:
            if frag in low:
                raise ValueError(
                    f"canonical_name looks like a description ('{frag}' detected). "
                    f"Use a concrete short name instead."
                )
        return v.strip()

    @field_validator("one_liner")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        s = v.strip()
        if "\n" in s:
            raise ValueError("one_liner must be a single line (no newlines)")
        # Block obvious markdown markers
        if s.startswith("#") or s.startswith("---") or s.startswith("```"):
            raise ValueError("one_liner must be plain text, no markdown")
        return s

    @model_validator(mode="after")
    def _no_lost_proper_nouns(self, info: ValidationInfo) -> Entity:
        """Reject Entities that dropped a proper noun from their source context.

        Activated only when caller passes `context={"source_tokens": {...}}` to
        `Entity.model_validate(...)`. The extractor populates `source_tokens`
        from raw user messages (`extract_proper_noun_candidates`). If any token
        is missing from canonical_name / aliases / one_liner / facts, we raise.

        This is the structural guarantee for M4: even if the LLM ignores the
        slot-binding nudge (M2) and the prompt instruction, the entity can't
        be written to disk with "БЕК" silently removed — pydantic refuses.
        """
        ctx = info.context or {}
        source_tokens: set[str] = ctx.get("source_tokens") or set()
        if not source_tokens:
            return self

        haystack_parts = [self.canonical_name, self.one_liner, *self.aliases, *self.facts]
        haystack = " ".join(haystack_parts).lower()

        lost = [tok for tok in source_tokens if tok.lower() not in haystack]
        if lost:
            lost_list = ", ".join(sorted(lost))
            raise ValueError(
                f"proper nouns dropped from source: {lost_list}. "
                f"Preserve them in canonical_name or aliases (these are likely "
                f"names, acronyms, or brands the user explicitly mentioned)."
            )
        return self

    @model_validator(mode="after")
    def _enforce_owner_anchor(self) -> Entity:
        """Every type except `person` MUST have an owner relation.

        If agent forgot to add it, we backfill — agent physically cannot
        produce a non-person entity without owner-anchor. Closes Root #1.
        """
        if self.type == "person":
            return self
        has_owner = any(r.field == "owner" for r in self.relations)
        if not has_owner:
            self.relations = [
                *self.relations,
                Relation(field="owner", target_path="_meta/owner.md"),
            ]
        return self

    @field_validator("facts")
    @classmethod
    def _facts_clean(cls, v: list[str]) -> list[str]:
        def _clean_line(line: str) -> str:
            s = line.strip()
            # Strip up to 2 leading bullet markers (`- `, `* `, `• `)
            for _ in range(2):
                if s.startswith(("- ", "* ", "• ")):
                    s = s[2:].strip()
                elif s.startswith(("-", "*", "•")):
                    s = s[1:].strip()
            return s

        cleaned: list[str] = []
        for item in v:
            if "\n" in item:
                # Multi-line "fact" — split into separate facts, clean each
                for line in item.splitlines():
                    c = _clean_line(line)
                    if c:
                        cleaned.append(c)
            else:
                c = _clean_line(item)
                if c:
                    cleaned.append(c)
        return cleaned[:25]


def render_body(entity: Entity, notes_lang: str = "ru") -> str:
    """Programmatic markdown render — single source of body formatting.

    Agents never write the body string. We assemble it from one_liner + facts.
    The links section is appended later by `linking.render_links_section`
    based on frontmatter, so we don't include it here.
    """
    from src.vault.section_headers import facts_header

    lines = [entity.one_liner]
    if entity.facts:
        lines.append("")
        lines.append(facts_header(notes_lang))
        lines.append("")
        for fact in entity.facts:
            lines.append(f"- {fact}")
    return "\n".join(lines).rstrip() + "\n"


def split_relations(
    relations: list[Relation],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split relations into (single-value-fm-dict, list-of-typed-link-tuples).

    `owner` goes straight to frontmatter (no inverse — Phase C decision).
    Other single-value fields go through add_typed_link to generate inverse.
    List-value fields go through add_typed_link multi-times.
    """
    fm_singles: dict[str, str] = {}
    typed_links: list[tuple[str, str]] = []

    for rel in relations:
        target = rel.target_path.removesuffix(".md")
        wikilink = f"[[{target}]]"
        if rel.field == "owner":
            # Owner intentionally has no inverse — Phase C
            fm_singles["owner"] = wikilink
        else:
            # Everything else flows through add_typed_link to generate the inverse
            typed_links.append((rel.field, rel.target_path))

    return fm_singles, typed_links
