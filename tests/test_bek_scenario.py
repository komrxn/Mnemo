"""End-to-end regression test for the 'Ресторан БЕК' bug.

This test reproduces the exact scenario from the screenshots:
1. Bot asks 'what's the restaurant called?' — registers a pending slot.
2. User answers 'Ресторан БЕК'.
3. Slot is consumed → literal stored in `slot:filled:onboarding`.
4. Bot tries to create the entity with a paraphrased title 'ресторан' (dropping БЕК).
5. The Entity validator (M4) sourced via filled_slots MUST reject this.

The bug was: step 5 succeeded silently, the LLM-normalized 'ресторан' was
persisted, and 'БЕК' was lost forever. With the M1-M5 fixes, the literal is
preserved in transcripts AND the validator refuses the lossy entity at write
time.
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from pydantic import ValidationError

from src.session import slots
from src.vault.entity import Entity, extract_proper_noun_candidates

# Same FakeRedis stub as test_slots.py — kept local to avoid cross-test imports.


class FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, bytes] = {}
        self._lists: dict[str, list[bytes]] = defaultdict(list)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._kv[key] = value

    async def get(self, key: str) -> bytes | None:
        return self._kv.get(key)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
            if k in self._lists:
                del self._lists[k]
                n += 1
        return n

    async def rpush(self, key: str, *values: bytes) -> int:
        self._lists[key].extend(values)
        return len(self._lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = self._lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


@pytest.mark.asyncio
async def test_bek_end_to_end_validator_blocks_lossy_entity() -> None:
    """Full scenario chain: slot filled → entity attempt with dropped БЕК → ValidationError."""
    redis = FakeRedis()

    # Step 1-2: bot asks, user answers literally.
    await slots.set_pending(
        redis,
        user_id=999,
        field="canonical_name",
        question="как называется ресторан?",
        entity_hint="family restaurant",
    )
    filled = await slots.consume_pending(redis, 999, "onboarding", "Ресторан БЕК")
    assert filled is not None
    assert filled.literal_value == "Ресторан БЕК"

    # Step 3: simulate what _create_note's _source_tokens_from_slots does.
    all_filled = await slots.list_filled(redis, "onboarding")
    source_tokens: set[str] = set()
    for f in all_filled:
        source_tokens |= extract_proper_noun_candidates(f.literal_value)
    assert "БЕК" in source_tokens

    # Step 4: agent tries to write a job entity with paraphrased title.
    # This is the bug shape — 'ресторан' has no trace of 'БЕК'.
    with pytest.raises(ValidationError, match=r"proper nouns dropped.*БЕК"):
        Entity.model_validate(
            {
                "type": "job",
                "canonical_name": "ресторан",
                "aliases": [],
                "one_liner": "семейный ресторан.",
                "facts": [],
            },
            context={"source_tokens": source_tokens},
        )


@pytest.mark.asyncio
async def test_bek_end_to_end_validator_accepts_preserving_entity() -> None:
    """The agent CAN write the entity if it preserves the literal — happy path."""
    redis = FakeRedis()

    await slots.set_pending(
        redis,
        user_id=999,
        field="canonical_name",
        question="как называется ресторан?",
        entity_hint="family restaurant",
    )
    await slots.consume_pending(redis, 999, "onboarding", "Ресторан БЕК")

    all_filled = await slots.list_filled(redis, "onboarding")
    source_tokens: set[str] = set()
    for f in all_filled:
        source_tokens |= extract_proper_noun_candidates(f.literal_value)

    # Agent uses literal in canonical_name → accepted.
    e = Entity.model_validate(
        {
            "type": "job",
            "canonical_name": "Ресторан БЕК",
            "aliases": [],
            "one_liner": "семейный ресторан.",
            "facts": [],
        },
        context={"source_tokens": source_tokens},
    )
    assert e.canonical_name == "Ресторан БЕК"


@pytest.mark.asyncio
async def test_bek_end_to_end_accepts_when_literal_in_aliases() -> None:
    """Even if canonical_name is a sanitized slug, putting the literal in
    aliases is enough to satisfy the validator."""
    redis = FakeRedis()
    await slots.set_pending(
        redis, user_id=1, field="canonical_name", question="?"
    )
    await slots.consume_pending(redis, 1, "onboarding", "Ресторан БЕК")

    all_filled = await slots.list_filled(redis, "onboarding")
    source_tokens: set[str] = set()
    for f in all_filled:
        source_tokens |= extract_proper_noun_candidates(f.literal_value)

    e = Entity.model_validate(
        {
            "type": "job",
            "canonical_name": "ресторан-бек",  # slugified
            "aliases": ["БЕК", "Ресторан БЕК"],
            "one_liner": "семейный ресторан.",
            "facts": [],
        },
        context={"source_tokens": source_tokens},
    )
    assert "БЕК" in e.aliases
