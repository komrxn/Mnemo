"""Tests for the recall tool (M3 of memory-layers plan).

The end-to-end regression anchor: when a transcript contains "Ресторан БЕК" and
the agent calls recall("ресторан"), the tool returns the literal so the bot
can't say "I don't see it".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.config import settings as real_settings
from src.session.manager import SessionMessage
from src.tools.recall import RecallParams, _recall


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(real_settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(real_settings, "tz", "UTC")
    return tmp_path


async def _fake_write_note(
    rel_path: str, body: str, fm: dict[str, Any], session_id: str = ""
) -> str:
    full = Path(real_settings.vault_path) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    full.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")
    return "fake-sha"


def _msg(role: str, content: str) -> SessionMessage:
    return SessionMessage(role=role, content=content, ts=datetime.now())


# ── end-to-end: recall finds the literal across layers ───────────────────────


@pytest.mark.asyncio
async def test_recall_finds_bek_in_transcripts(vault: Path) -> None:
    """The headline regression: after seal, recall surfaces 'Ресторан БЕК'."""
    from src.vault import transcripts

    msgs = [
        _msg("user", "Ресторан БЕК — наш семейный."),
        _msg("assistant", "Записал."),
    ]
    with patch("src.vault.transcripts.writer.write_note", side_effect=_fake_write_note):
        await transcripts.seal_session("ses_bek", msgs, lang="ru")

    # Stub out the other sources so we isolate transcript-path retrieval.
    with (
        patch("src.tools.recall._recall_session_msgs", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_vault", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_semantic", new=AsyncMock(return_value="")),
        patch("src.tools.recall._mark_recall_done", new=AsyncMock(return_value=None)),
    ):
        result = await _recall(RecallParams(query="БЕК"), session_id="")

    assert "transcripts" in result
    assert "БЕК" in result


@pytest.mark.asyncio
async def test_recall_reports_empty_when_nothing_found(vault: Path) -> None:
    """No source has anything — the tool must say so explicitly, not return ''.

    The whole point of the gate is that the agent can verify recall ran. Empty-
    string silence would look indistinguishable from skipping the call.
    """
    with (
        patch("src.tools.recall._recall_session_msgs", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_vault", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_semantic", new=AsyncMock(return_value="")),
        patch("src.tools.recall._mark_recall_done", new=AsyncMock(return_value=None)),
    ):
        result = await _recall(RecallParams(query="unknown-thing"), session_id="")

    assert "nothing found" in result.lower()


@pytest.mark.asyncio
async def test_recall_aggregates_multiple_sources(vault: Path) -> None:
    """All sources contribute; output has section headers per source."""
    with (
        patch(
            "src.tools.recall._recall_session_msgs",
            new=AsyncMock(return_value="## current session (literal)\n  - [user] hi БЕК"),
        ),
        patch(
            "src.tools.recall._recall_vault",
            new=AsyncMock(
                return_value="## vault (entity notes)\n  - 30_Jobs/bek.md — ..."
            ),
        ),
        patch("src.tools.recall._recall_semantic", new=AsyncMock(return_value="")),
        patch("src.tools.recall._mark_recall_done", new=AsyncMock(return_value=None)),
    ):
        # No transcripts on disk in this test → transcript section is empty
        result = await _recall(RecallParams(query="БЕК"), session_id="ses_x")

    assert "current session" in result
    assert "vault" in result
    assert "30_Jobs/bek.md" in result


@pytest.mark.asyncio
async def test_recall_sets_marker_after_run(vault: Path) -> None:
    """`was_recall_done` flips true for the session_id used."""
    from src.tools import recall as recall_module

    marker_storage: dict[str, bytes] = {}

    class _FakeRedis:
        async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
            marker_storage[key] = value

        async def get(self, key: str) -> bytes | None:
            return marker_storage.get(key)

    fake = _FakeRedis()

    with (
        patch("src.session.manager.get_redis", new=AsyncMock(return_value=fake)),
        patch("src.tools.recall._recall_session_msgs", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_vault", new=AsyncMock(return_value="")),
        patch("src.tools.recall._recall_semantic", new=AsyncMock(return_value="")),
    ):
        await _recall(RecallParams(query="xx"), session_id="ses_marker")
        done = await recall_module.was_recall_done("ses_marker")

    assert done is True


@pytest.mark.asyncio
async def test_recall_vault_excludes_transcripts(vault: Path) -> None:
    """ripgrep matches in 90_Transcripts/ are filtered — they have their own section,
    we don't want them double-listed under 'vault (entity notes)'."""
    from src.tools.recall import _recall_vault

    fake_results = [
        {"path": "90_Transcripts/2026/05/ses_x.md", "context": "БЕК mentioned"},
        {"path": "30_Jobs/bek.md", "context": "BEK family restaurant"},
    ]
    with patch("src.vault.search.search", new=AsyncMock(return_value=fake_results)):
        out = await _recall_vault("БЕК", top_k=5)

    assert "30_Jobs/bek.md" in out
    assert "90_Transcripts" not in out


@pytest.mark.asyncio
async def test_recall_session_msgs_swallows_redis_error() -> None:
    """Each per-source helper has its own try/except — Redis hiccup must NOT
    crash the orchestrator. The helper returns '' and the rest continues."""
    from src.tools.recall import _recall_session_msgs

    with patch(
        "src.session.manager.get_redis",
        new=AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        # Should not raise — returns "" so the orchestrator can still produce output.
        out = await _recall_session_msgs("ses_x", "БЕК")

    assert out == ""
