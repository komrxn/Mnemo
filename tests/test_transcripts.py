"""Tests for the transcript layer (M1 of memory-layers plan).

Covers ADR-0001 invariants:
- seal_session writes a literal, dated transcript file
- wikilinks in user/bot text are escaped (no Obsidian graph pollution)
- converter.vault_to_custom_kg skips transcripts (no LightRAG KG pollution)
- full_reindex excludes 90_Transcripts/
- search_literal recovers exact tokens (the "БЕК" recall scenario)
- seal_session is idempotent and skips empty sessions
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from src.config import settings as real_settings
from src.session.manager import SessionMessage
from src.vault import transcripts

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty vault rooted at tmp_path. Patches real settings.vault_path + tz=UTC."""
    monkeypatch.setattr(real_settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(real_settings, "tz", "UTC")
    return tmp_path


def _msg(role: str, content: str, *, when: str = "2026-05-12T11:36:00+00:00") -> SessionMessage:
    return SessionMessage(role=role, content=content, ts=datetime.fromisoformat(when))


async def _fake_write_note(
    rel_path: str,
    body: str,
    fm: dict[str, Any],
    session_id: str = "",
) -> str:
    """Stand-in for writer.write_note that skips git but writes the file."""
    full = Path(real_settings.vault_path) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    full.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")
    return "fake-sha"


# ── escape_wikilinks ──────────────────────────────────────────────────────────


def test_escape_wikilinks_escapes_simple() -> None:
    assert transcripts.escape_wikilinks("see [[Foo]] now") == "see \\[\\[Foo\\]\\] now"


def test_escape_wikilinks_escapes_aliased() -> None:
    assert (
        transcripts.escape_wikilinks("ping [[20_People/anna|Anna]]")
        == "ping \\[\\[20_People/anna|Anna\\]\\]"
    )


def test_escape_wikilinks_idempotent_on_plain_text() -> None:
    assert transcripts.escape_wikilinks("no links here") == "no links here"


def test_escape_wikilinks_leaves_single_brackets_alone() -> None:
    assert transcripts.escape_wikilinks("[a] and [b]") == "[a] and [b]"


# ── transcript_path ───────────────────────────────────────────────────────────


def test_transcript_path_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(real_settings, "tz", "UTC")
    path = transcripts.transcript_path(
        "ses_2026-05-12_11-36-00",
        datetime(2026, 5, 12, 11, 36, tzinfo=UTC),
    )
    assert path == "90_Transcripts/2026/05/2026-05-12_ses_2026-05-12_11-36-00.md"


# ── seal_session ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seal_session_writes_literal_content(vault: Path) -> None:
    msgs = [
        _msg("user", "Ресторан БЕК — наш семейный."),
        _msg("assistant", "Записал.", when="2026-05-12T11:37:00+00:00"),
    ]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        rel = await transcripts.seal_session(
            "ses_2026-05-12_11-36-00",
            msgs,
            lang="ru",
        )

    assert rel.startswith("90_Transcripts/2026/05/")
    content = (vault / rel).read_text(encoding="utf-8")
    # Literal preservation — this is the regression-anchor for the "БЕК" bug
    assert "БЕК" in content
    assert "семейный" in content
    # Roles are tagged
    assert "*user*" in content
    assert "*assistant*" in content
    # Frontmatter sanity
    fm_raw = content.split("---", 2)[1]
    fm = yaml.safe_load(fm_raw)
    assert fm["type"] == "transcript"
    assert fm["session_id"] == "ses_2026-05-12_11-36-00"
    assert fm["lang"] == "ru"
    assert fm["message_count"] == 2


@pytest.mark.asyncio
async def test_seal_session_escapes_wikilinks_in_body(vault: Path) -> None:
    msgs = [_msg("user", "see [[40_Projects/mnemo]] please")]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        rel = await transcripts.seal_session("ses_x", msgs, lang="ru")

    body = (vault / rel).read_text(encoding="utf-8")
    # The raw double-bracket must NOT appear — would create an Obsidian edge
    assert "[[40_Projects/mnemo]]" not in body
    assert "\\[\\[40_Projects/mnemo\\]\\]" in body


@pytest.mark.asyncio
async def test_seal_session_empty_msgs_is_noop(vault: Path) -> None:
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note) as wn:
        rel = await transcripts.seal_session("ses_empty", [], lang="ru")
    assert rel == ""
    wn.assert_not_called()


@pytest.mark.asyncio
async def test_seal_session_idempotent_overwrite(vault: Path) -> None:
    msgs1 = [_msg("user", "first version")]
    msgs2 = [_msg("user", "second version")]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        rel1 = await transcripts.seal_session("ses_dup", msgs1, lang="ru")
        rel2 = await transcripts.seal_session("ses_dup", msgs2, lang="ru")

    assert rel1 == rel2
    final = (vault / rel1).read_text(encoding="utf-8")
    assert "second version" in final
    assert "first version" not in final


# ── search_literal ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_literal_finds_bek_scenario(vault: Path) -> None:
    """The end-to-end regression anchor: after seal, recall finds the literal."""
    msgs = [
        _msg("user", "Ресторан БЕК — это наш семейный бизнес."),
        _msg("assistant", "Понял, записал."),
    ]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        await transcripts.seal_session("ses_bek", msgs, lang="ru")

    hits = transcripts.search_literal("БЕК")
    assert len(hits) == 1
    assert hits[0].session_id == "ses_bek"
    assert "БЕК" in hits[0].snippet


@pytest.mark.asyncio
async def test_search_literal_case_insensitive(vault: Path) -> None:
    msgs = [_msg("user", "I work at LegAI now.")]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        await transcripts.seal_session("ses_legai", msgs, lang="en")

    hits = transcripts.search_literal("legai")
    assert hits and "LegAI" in hits[0].snippet


def test_search_literal_empty_query_returns_nothing(vault: Path) -> None:
    assert transcripts.search_literal("") == []
    assert transcripts.search_literal("   ") == []


def test_search_literal_no_transcripts_dir(vault: Path) -> None:
    # 90_Transcripts/ doesn't exist yet in the fresh tmp_path
    assert transcripts.search_literal("anything") == []


# ── LightRAG isolation: transcripts must NOT enter KG ─────────────────────────


def test_converter_skips_transcripts_by_path(vault: Path) -> None:
    """Even with `type: transcript` frontmatter, files under 90_Transcripts/ are
    filtered before parse — cheaper and defense-in-depth."""
    p = vault / "90_Transcripts" / "2026" / "05" / "2026-05-12_ses.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ntype: transcript\nsession_id: ses\n---\n\n**11:36** *user*: hi\n",
        encoding="utf-8",
    )
    # A real entity for contrast — should still appear
    person = vault / "20_People" / "anna.md"
    person.parent.mkdir(parents=True)
    person.write_text(
        "---\ntype: person\n---\n\nAnna is a designer.\n",
        encoding="utf-8",
    )

    from src.lightrag_svc.converter import vault_to_custom_kg

    kg = vault_to_custom_kg(
        [
            "90_Transcripts/2026/05/2026-05-12_ses.md",
            "20_People/anna.md",
        ]
    )

    entity_names = {e["entity_name"] for e in kg["entities"]}
    assert "20_People/anna" in entity_names
    assert not any("90_Transcripts" in n for n in entity_names)
    assert not any("90_Transcripts" in str(c.get("file_path", "")) for c in kg["chunks"])


def test_converter_skips_transcripts_by_type_when_path_anomalous(vault: Path) -> None:
    """If a transcript-typed file somehow ends up outside 90_Transcripts/
    (e.g. user moved it), frontmatter type check is the second line of defense."""
    rogue = vault / "00_Inbox" / "stray-transcript.md"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "---\ntype: transcript\nsession_id: ses_x\n---\n\nstuff\n",
        encoding="utf-8",
    )

    from src.lightrag_svc.converter import vault_to_custom_kg

    kg = vault_to_custom_kg(["00_Inbox/stray-transcript.md"])

    assert kg["entities"] == []
    assert kg["relationships"] == []
    assert kg["chunks"] == []
