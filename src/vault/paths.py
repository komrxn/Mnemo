"""Path utilities — all vault path resolution goes through this module.

Closes Group F (path traversal): every place that handles agent-supplied
paths must use `resolve_inside_vault` to ensure we never read/write outside
the vault root. Previously only `writer._resolve` did this; reader.py and
linking.py read raw concatenated paths.
"""

from __future__ import annotations

from pathlib import Path

from slugify import slugify

from src.config import settings
from src.vault.frontmatter import TYPE_FOLDERS


class VaultPathError(ValueError):
    """Raised when a path resolves outside the vault root."""


def vault_root() -> Path:
    return Path(settings.vault_path)


def resolve_inside_vault(rel_path: str) -> Path:
    """Resolve `rel_path` relative to vault root, refusing path traversal.

    Returns absolute Path. Raises VaultPathError on `..` escapes or absolute paths.
    Absolute paths are rejected outright (they wouldn't navigate into vault anyway).
    """
    if not rel_path:
        raise VaultPathError("empty path")
    s = rel_path.strip()
    # Reject absolute paths (Unix /, Windows drive letter)
    if s.startswith("/") and "/" in s[1:]:
        # If it looks like /etc/foo or /usr/bar — reject. But /20_People/anna.md
        # is a special "leading slash on relative path" case → strip it.
        # Heuristic: if first segment is a known vault folder pattern (digits_Word
        # or _meta), treat as relative; otherwise reject.
        first = s.lstrip("/").split("/", 1)[0]
        from src.vault.frontmatter import TYPE_FOLDERS as _TF

        valid_first = set(_TF.values()) | {"_meta", "90_Attachments"}
        if first not in valid_first:
            raise VaultPathError(f"absolute path rejected: {rel_path!r}")
    s = s.lstrip("/").lstrip("\\")
    root = vault_root().resolve()
    candidate = (root / s).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise VaultPathError(f"path traversal rejected: {rel_path!r}") from e
    return candidate


def is_inside_vault(rel_path: str) -> bool:
    """Check `resolve_inside_vault` without raising. True iff path is safe."""
    try:
        resolve_inside_vault(rel_path)
        return True
    except VaultPathError:
        return False


def resolve_target_path(target: str) -> str | None:
    """Resolve agent-supplied target path to a real vault file.

    Tries: as-is, with `.md` appended, with basename slugified.
    Returns vault-relative path if any candidate exists, else None.
    """
    target = target.strip().lstrip("/")
    candidates: list[str] = [target]
    if not target.endswith(".md"):
        candidates.append(target + ".md")

    if "/" in target:
        folder, _, name = target.rpartition("/")
        stem = name.removesuffix(".md")
        slug = slugify(stem, allow_unicode=True, separator="-", lowercase=True)
        candidates.append(f"{folder}/{slug}.md")

    for c in candidates:
        if not is_inside_vault(c):
            continue
        if resolve_inside_vault(c).exists():
            return c
    return None


def folder_for_type(note_type: str) -> str:
    return TYPE_FOLDERS.get(note_type, "00_Inbox")
