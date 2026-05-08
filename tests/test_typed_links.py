from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "50_Tasks").mkdir(parents=True)
    (tmp_path / "40_Projects").mkdir(parents=True)
    (tmp_path / "50_Tasks" / "foo.md").write_text(
        "---\ntype: task\naliases: []\n---\n\nDo something.\n", encoding="utf-8"
    )
    (tmp_path / "40_Projects" / "bar.md").write_text(
        "---\ntype: project\naliases: []\n---\n\nBar project.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_add_typed_link_creates_forward_and_inverse(vault: Path) -> None:
    committed: list[tuple[list[str], str]] = []

    async def fake_commit(path: Path, files: list[str], msg: str) -> str:
        committed.append((files, msg))
        return "abc123"

    # Patch settings in linking AND reader (reader.note_exists uses its own settings ref)
    with (
        patch("src.vault.linking.settings") as ms_link,
        patch("src.vault.reader.settings") as ms_read,
        patch("src.vault.linking.git_ops.stage_and_commit", side_effect=fake_commit),
    ):
        ms_link.vault_path = str(vault)
        ms_read.vault_path = str(vault)

        from src.vault.linking import add_typed_link

        result = await add_typed_link(
            "50_Tasks/foo.md", "40_Projects/bar.md", "for_project", "test"
        )

    assert result is True

    foo_raw = (vault / "50_Tasks" / "foo.md").read_text(encoding="utf-8")
    bar_raw = (vault / "40_Projects" / "bar.md").read_text(encoding="utf-8")

    assert (
        "for_project: '[[40_Projects/bar]]'" in foo_raw
        or "for_project: [[40_Projects/bar]]" in foo_raw
    )
    assert "[[50_Tasks/foo]]" in bar_raw
    assert "## Связи" in foo_raw
    assert "## Связи" in bar_raw

    assert len(committed) == 1
    assert "for_project" in committed[0][1]
    assert "50_Tasks/foo.md" in committed[0][0]
    assert "40_Projects/bar.md" in committed[0][0]


@pytest.mark.asyncio
async def test_add_typed_link_idempotent(vault: Path) -> None:
    committed: list[tuple[list[str], str]] = []

    async def fake_commit(path: Path, files: list[str], msg: str) -> str:
        committed.append((files, msg))
        return "abc123"

    with (
        patch("src.vault.linking.settings") as ms_link,
        patch("src.vault.reader.settings") as ms_read,
        patch("src.vault.linking.git_ops.stage_and_commit", side_effect=fake_commit),
    ):
        ms_link.vault_path = str(vault)
        ms_read.vault_path = str(vault)

        from src.vault.linking import add_typed_link

        await add_typed_link("50_Tasks/foo.md", "40_Projects/bar.md", "for_project", "t1")
        result2 = await add_typed_link("50_Tasks/foo.md", "40_Projects/bar.md", "for_project", "t2")

    assert result2 is False
    assert len(committed) == 1
