from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "20_People").mkdir(parents=True)
    (tmp_path / "_meta").mkdir(parents=True)
    (tmp_path / "20_People" / "alice.md").write_text(
        "---\ntype: person\nstatus: open\n---\n\nAlice is a designer.\n", encoding="utf-8"
    )
    (tmp_path / "20_People" / "bob.md").write_text(
        "---\ntype: person\nstatus: open\n---\n\nBob is an engineer.\n", encoding="utf-8"
    )
    (tmp_path / "20_People" / "charlie.md").write_text(
        "---\ntype: person\nstatus: archived\n---\n\nCharlie left the team.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_regenerate_moc_creates_file(vault: Path) -> None:
    async def fake_write_note(
        rel_path: str,
        body: str,
        fm: dict,
        session_id: str = "",  # type: ignore[type-arg]
    ) -> str:
        full = vault / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False)
        full.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")
        return "abc123"

    with (
        patch("src.vault.moc.settings") as ms,
        patch("src.vault.moc.writer.write_note", side_effect=fake_write_note),
    ):
        ms.vault_path = str(vault)
        from src.vault.moc import regenerate_moc

        result = await regenerate_moc("person")

    assert result == "_meta/MOC_People.md"
    content = (vault / "_meta" / "MOC_People.md").read_text(encoding="utf-8")
    assert "Активные (2)" in content
    assert "Архив (1)" in content
    assert "alice" in content
    assert "charlie" in content


@pytest.mark.asyncio
async def test_regenerate_moc_unsupported_type(vault: Path) -> None:
    with patch("src.vault.moc.settings") as ms:
        ms.vault_path = str(vault)
        from src.vault.moc import regenerate_moc

        result = await regenerate_moc("task")

    assert result is None
