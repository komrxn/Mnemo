"""Tests for slot-binding (M2 of memory-layers plan).

Covers ADR-0001 invariants:
- A pending slot is recorded by `set_pending` and read back by `get_pending`.
- The next user message fills the slot with LITERAL text (no normalization).
- A filled slot is queryable per-session via `list_filled`.
- Counter-questions (ending with `?`) do NOT consume the slot.
- The BEK scenario: after consume, literal_value contains "БЕК" verbatim.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import orjson
import pytest

from src.session import slots

# ── tiny in-memory Redis stub ─────────────────────────────────────────────────
#
# We need only get/set/delete/rpush/lrange/expire to satisfy slots.py. Mocking
# the real redis client is overkill; a small dict-backed stub keeps tests
# fast and dependency-free.


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
        return True  # TTL is not modelled in tests


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


# ── pending ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_pending_persists_question_and_metadata(redis: Any) -> None:
    slot = await slots.set_pending(
        redis,
        user_id=123,
        field="canonical_name",
        question="как называется ресторан?",
        entity_hint="family restaurant",
    )
    assert slot.field == "canonical_name"
    assert slot.entity_hint == "family restaurant"
    assert slot.question == "как называется ресторан?"
    assert slot.slot_id  # non-empty

    fetched = await slots.get_pending(redis, 123)
    assert fetched is not None
    assert fetched.slot_id == slot.slot_id


@pytest.mark.asyncio
async def test_set_pending_overwrites_prior_slot(redis: Any) -> None:
    """Only one pending slot per user at a time — second set replaces first."""
    first = await slots.set_pending(
        redis, user_id=1, field="canonical_name", question="q1?"
    )
    second = await slots.set_pending(
        redis, user_id=1, field="alias", question="q2?"
    )
    fetched = await slots.get_pending(redis, 1)
    assert fetched is not None
    assert fetched.slot_id == second.slot_id
    assert fetched.slot_id != first.slot_id


@pytest.mark.asyncio
async def test_clear_pending_removes_slot(redis: Any) -> None:
    await slots.set_pending(redis, user_id=1, field="value", question="q?")
    await slots.clear_pending(redis, 1)
    assert await slots.get_pending(redis, 1) is None


# ── consume ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consume_pending_fills_with_literal_bek(redis: Any) -> None:
    """The regression anchor: the answer 'Ресторан БЕК' must be preserved verbatim."""
    await slots.set_pending(
        redis,
        user_id=1,
        field="canonical_name",
        question="как называется ресторан?",
        entity_hint="family restaurant",
    )

    filled = await slots.consume_pending(redis, 1, "ses_bek", "Ресторан БЕК")

    assert filled is not None
    assert filled.literal_value == "Ресторан БЕК"  # NO normalization, NO loss
    assert filled.entity_hint == "family restaurant"
    assert filled.field == "canonical_name"

    # Pending was cleared
    assert await slots.get_pending(redis, 1) is None

    # Persisted to the per-session list
    all_filled = await slots.list_filled(redis, "ses_bek")
    assert len(all_filled) == 1
    assert all_filled[0].literal_value == "Ресторан БЕК"


@pytest.mark.asyncio
async def test_consume_pending_noop_without_pending(redis: Any) -> None:
    filled = await slots.consume_pending(redis, 1, "ses_x", "Hello")
    assert filled is None


@pytest.mark.asyncio
async def test_consume_pending_skips_counter_questions(redis: Any) -> None:
    """If user replies with their own question, don't bind — pending stays alive."""
    await slots.set_pending(
        redis, user_id=1, field="canonical_name", question="как называется?"
    )
    filled = await slots.consume_pending(redis, 1, "ses_x", "а зачем тебе это знать?")
    assert filled is None
    # Pending must still be there so the next direct answer can bind
    assert await slots.get_pending(redis, 1) is not None


@pytest.mark.asyncio
async def test_consume_pending_skips_very_long_replies(redis: Any) -> None:
    """A 500+ char reply is probably an essay, not a direct answer to a name question."""
    await slots.set_pending(redis, user_id=1, field="canonical_name", question="q?")
    long_reply = "x" * 600
    filled = await slots.consume_pending(redis, 1, "ses_x", long_reply)
    assert filled is None


@pytest.mark.asyncio
async def test_list_filled_preserves_order(redis: Any) -> None:
    """Multiple filled slots accumulate in insertion order, per session."""
    for i, value in enumerate(["БЕК", "MNEMO", "LegAI"]):
        await slots.set_pending(
            redis, user_id=1, field="canonical_name", question=f"q{i}?"
        )
        await slots.consume_pending(redis, 1, "ses_multi", value)

    filled = await slots.list_filled(redis, "ses_multi")
    assert [f.literal_value for f in filled] == ["БЕК", "MNEMO", "LegAI"]


@pytest.mark.asyncio
async def test_clear_filled(redis: Any) -> None:
    await slots.set_pending(redis, user_id=1, field="canonical_name", question="q?")
    await slots.consume_pending(redis, 1, "ses_x", "value")
    assert await slots.list_filled(redis, "ses_x")
    await slots.clear_filled(redis, "ses_x")
    assert await slots.list_filled(redis, "ses_x") == []


# ── prompt formatting ─────────────────────────────────────────────────────────


def test_format_filled_for_prompt_contains_literal_and_rule() -> None:
    filled = slots.FilledSlot(
        slot_id="abc",
        field="canonical_name",
        question="как называется?",
        entity_hint="family restaurant",
        literal_value="Ресторан БЕК",
        filled_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    rendered = slots.format_filled_for_prompt(filled, lang="ru")
    assert "Ресторан БЕК" in rendered
    assert "family restaurant" in rendered
    assert "БУКВАЛЬНО" in rendered  # the directive must be present


def test_format_filled_list_empty_is_empty_string() -> None:
    assert slots.format_filled_list_for_extraction([], lang="ru") == ""


def test_format_filled_list_for_extraction_includes_all_literals() -> None:
    from datetime import UTC, datetime

    filled_list = [
        slots.FilledSlot(
            slot_id="a",
            field="canonical_name",
            question="ресторан?",
            entity_hint="restaurant",
            literal_value="Ресторан БЕК",
            filled_at=datetime.now(UTC),
        ),
        slots.FilledSlot(
            slot_id="b",
            field="canonical_name",
            question="бренд?",
            entity_hint="crypto product",
            literal_value="личный бренд",
            filled_at=datetime.now(UTC),
        ),
    ]
    rendered = slots.format_filled_list_for_extraction(filled_list, lang="ru")
    assert "Ресторан БЕК" in rendered
    assert "личный бренд" in rendered
    assert "restaurant" in rendered
    assert "crypto product" in rendered


# ── Redis-level guards: keys never collide across users/sessions ─────────────


def test_key_namespacing_is_distinct() -> None:
    assert slots.key_pending(1) != slots.key_pending(2)
    assert slots.key_filled("ses_a") != slots.key_filled("ses_b")
    # Pending is per-user, filled is per-session — schemas must not collide.
    assert not slots.key_pending(1).startswith(slots.key_filled("1"))
    assert not slots.key_filled("1").startswith(slots.key_pending(1))


# ── orjson round-trip ────────────────────────────────────────────────────────


def test_pending_slot_orjson_roundtrip() -> None:
    """Pydantic model_dump(mode='json') + orjson must round-trip cleanly."""
    from datetime import UTC, datetime

    original = slots.PendingSlot(
        slot_id="abc",
        field="canonical_name",
        question="q?",
        entity_hint="h",
        asked_at=datetime.now(UTC),
    )
    raw = orjson.dumps(original.model_dump(mode="json"))
    restored = slots.PendingSlot.model_validate(orjson.loads(raw))
    assert restored.slot_id == original.slot_id
    assert restored.question == original.question
