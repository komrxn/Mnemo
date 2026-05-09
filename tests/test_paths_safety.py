"""Path-traversal guard tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.vault.paths import VaultPathError, is_inside_vault, resolve_inside_vault


@pytest.fixture(autouse=True)
def _vault(tmp_path: Path) -> Path:
    (tmp_path / "20_People").mkdir()
    (tmp_path / "20_People" / "anna.md").write_text("x", encoding="utf-8")
    with patch("src.vault.paths.settings") as ms:
        ms.vault_path = str(tmp_path)
        yield tmp_path


def test_resolve_inside_vault_accepts_simple_path() -> None:
    p = resolve_inside_vault("20_People/anna.md")
    assert p.exists()


def test_resolve_inside_vault_rejects_dotdot() -> None:
    with pytest.raises(VaultPathError):
        resolve_inside_vault("../etc/passwd")


def test_resolve_inside_vault_rejects_absolute_path() -> None:
    with pytest.raises(VaultPathError):
        resolve_inside_vault("/etc/passwd")


def test_resolve_inside_vault_rejects_empty() -> None:
    with pytest.raises(VaultPathError):
        resolve_inside_vault("")


def test_resolve_inside_vault_strips_leading_slash() -> None:
    """Leading slash is treated as relative, not absolute."""
    p = resolve_inside_vault("/20_People/anna.md")
    assert p.exists()


def test_is_inside_vault_returns_bool() -> None:
    assert is_inside_vault("20_People/anna.md") is True
    assert is_inside_vault("../escape") is False
