from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "20_People").mkdir(parents=True)
    (tmp_path / "40_Projects").mkdir(parents=True)
    (tmp_path / "10_Daily").mkdir(parents=True)

    (tmp_path / "20_People" / "anna.md").write_text(
        "---\n"
        "type: person\n"
        "aliases:\n- Anya\n- Аня\n"
        "works_at: '[[40_Projects/legai]]'\n"
        "---\n\n"
        "## Факты\n\n- CTO LegAI\n- работает с командой 5 лет\n",
        encoding="utf-8",
    )
    (tmp_path / "40_Projects" / "legai.md").write_text(
        "---\ntype: project\naliases: [LegAI]\n---\n\nAI startup.\n",
        encoding="utf-8",
    )
    (tmp_path / "10_Daily" / "2026-05-07.md").write_text(
        "---\ntype: daily\n---\n\nSession notes.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_converter_skips_daily(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg

        kg = vault_to_custom_kg(
            [
                "20_People/anna.md",
                "40_Projects/legai.md",
                "10_Daily/2026-05-07.md",
            ]
        )

    entity_paths = {e["file_path"] for e in kg["entities"]}
    assert "10_Daily/2026-05-07.md" not in entity_paths


def test_converter_creates_typed_relationship(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg

        kg = vault_to_custom_kg(["20_People/anna.md", "40_Projects/legai.md"])

    works_at = [r for r in kg["relationships"] if r["keywords"] == "works_at"]
    assert len(works_at) == 1
    assert works_at[0]["src_id"] == "20_People/anna"
    assert works_at[0]["tgt_id"] == "40_Projects/legai"


def test_converter_chunk_excludes_frontmatter_and_links(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg

        kg = vault_to_custom_kg(["20_People/anna.md"])

    chunk = kg["chunks"][0]["content"]
    assert "type: person" not in chunk
    assert "works_at:" not in chunk
    assert "## Связи" not in chunk
    assert "CTO LegAI" in chunk


def test_converter_entity_name_stable_across_runs(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg

        kg1 = vault_to_custom_kg(["20_People/anna.md"])
        kg2 = vault_to_custom_kg(["20_People/anna.md"])

    assert kg1["entities"][0]["entity_name"] == kg2["entities"][0]["entity_name"]
    assert kg1["entities"][0]["entity_name"] == "20_People/anna"
