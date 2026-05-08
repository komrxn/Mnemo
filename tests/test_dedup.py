from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.vault.dedup import find_similar


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "40_Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "legai.md").write_text(
        "---\ntype: project\naliases:\n- LegAI\n---\n\nLegAI project.\n", encoding="utf-8"
    )
    (proj_dir / "echelon.md").write_text(
        "---\ntype: project\naliases: []\n---\n\nEchelon project.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_find_similar_exact_alias(vault: Path) -> None:
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("project", "LegAI")
    assert results and results[0].path.endswith("legai.md")
    assert results[0].score >= 90


@pytest.mark.asyncio
async def test_find_similar_no_match(vault: Path) -> None:
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("project", "новый-проект-xyz-123")
    assert results == []


@pytest.mark.asyncio
async def test_find_similar_wrong_type(vault: Path) -> None:
    """task folder doesn't exist — should return empty."""
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("task", "legai")
    assert results == []
