from __future__ import annotations

from pathlib import Path

from src.config import settings
from src.vault.frontmatter import Frontmatter, Note, parse
from src.vault.paths import VaultPathError, resolve_inside_vault


def _vault() -> Path:
    return Path(settings.vault_path)


def note_exists(rel_path: str) -> bool:
    """Safe existence check — returns False on path traversal."""
    try:
        return resolve_inside_vault(rel_path).exists()
    except VaultPathError:
        return False


def read_note(rel_path: str) -> Note:
    path = resolve_inside_vault(rel_path)
    raw = path.read_text(encoding="utf-8")
    fm_dict, body = parse(raw)
    try:
        fm = Frontmatter.model_validate(fm_dict)
    except Exception:
        fm = Frontmatter()
    return Note(path=rel_path, frontmatter=fm, body=body)


def list_md_files(folder: str = "") -> list[Path]:
    """Return all .md files under folder (relative to vault root)."""
    base = _vault() / folder if folder else _vault()
    if not base.exists():
        return []
    return sorted(base.rglob("*.md"))
