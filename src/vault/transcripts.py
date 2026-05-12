"""Transcript layer — immutable literal log of every session.

This is the *primary* memory layer. Graph layer (entities + relations) is a
lossy LLM-derived projection of what's here. See `docs/adr/0001-memory-layers.md`.

Invariants (enforced by code in this module + indexer/converter):

1. Every closed session produces exactly one transcript file at
   `90_Transcripts/YYYY/MM/YYYY-MM-DD_<session_id>.md`.
2. The body is dialog text, *literal*. Wikilinks inside user/bot text are
   escaped so they don't generate edges in Obsidian or LightRAG graph.
3. Frontmatter carries `type: transcript` — converter.vault_to_custom_kg and
   full_reindex both filter on it (skip from KG entity/relationship generation).
4. After `seal_session`, the file is treated as read-only: no append/edit path
   in this module. (Filesystem-level read-only is not enforced — it's a
   contract, like 90_Transcripts being excluded from vault_map.)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from src.config import settings
from src.session.manager import SessionMessage
from src.vault import writer
from src.vault.paths import resolve_inside_vault

logger = structlog.get_logger()


TRANSCRIPTS_ROOT = "90_Transcripts"

# Matches `[[anything-not-brackets]]` — used to escape wikilinks in literal text
# so transcript bodies don't generate Obsidian graph edges. Pipes (`[[X|alias]]`)
# are included by the broad pattern.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def escape_wikilinks(text: str) -> str:
    """Neutralize `[[X]]` syntax in literal text.

    `[[Foo]]` → `\\[\\[Foo\\]\\]` — Obsidian renders the brackets but does not
    parse them as a link target. Idempotent on already-escaped strings because
    the regex requires unescaped `[[`.
    """
    return _WIKILINK_RE.sub(r"\\[\\[\1\\]\\]", text)


def transcript_path(session_id: str, started_at: datetime) -> str:
    """Vault-relative path for the transcript of a given session.

    Layout: `90_Transcripts/YYYY/MM/YYYY-MM-DD_<session_id>.md`.
    Month folder keeps directory listings manageable when sessions accumulate.
    """
    tz = ZoneInfo(settings.tz)
    local = started_at.astimezone(tz) if started_at.tzinfo else started_at.replace(tzinfo=tz)
    year = local.strftime("%Y")
    month = local.strftime("%m")
    date = local.strftime("%Y-%m-%d")
    return f"{TRANSCRIPTS_ROOT}/{year}/{month}/{date}_{session_id}.md"


def _render_body(msgs: list[SessionMessage]) -> str:
    """Render messages as dialog with literal text and escaped wikilinks."""
    tz = ZoneInfo(settings.tz)
    lines: list[str] = []
    for m in msgs:
        ts = m.ts.astimezone(tz) if m.ts.tzinfo else m.ts.replace(tzinfo=tz)
        time = ts.strftime("%H:%M")
        role = m.role
        kind_suffix = f" ({m.kind})" if m.kind != "text" else ""
        content = escape_wikilinks(m.content).rstrip()
        lines.append(f"**{time}** *{role}*{kind_suffix}:\n{content}\n")
    return "\n".join(lines).rstrip() + "\n"


async def seal_session(
    session_id: str,
    msgs: list[SessionMessage],
    *,
    lang: str = "ru",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> str:
    """Write the transcript file and commit. Returns vault-relative path.

    Idempotent on session_id: if the path already exists, it is overwritten —
    intentional, so a retried seal after a crash produces the same artifact.
    """
    if not msgs:
        logger.info("transcripts: skip empty session", session_id=session_id)
        return ""

    start = started_at or msgs[0].ts
    end = ended_at or msgs[-1].ts
    rel = transcript_path(session_id, start)

    fm: dict[str, object] = {
        "type": "transcript",
        "session_id": session_id,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "lang": lang,
        "message_count": len(msgs),
    }
    body = _render_body(msgs)
    await writer.write_note(rel, body, fm, session_id=session_id)
    logger.info(
        "transcript sealed",
        path=rel,
        messages=len(msgs),
        session_id=session_id,
    )
    return rel


# ── recall ───────────────────────────────────────────────────────────────────


class TranscriptHit:
    """One literal-match hit inside a transcript file."""

    __slots__ = ("line_no", "path", "session_id", "snippet")

    def __init__(self, path: str, session_id: str, snippet: str, line_no: int) -> None:
        self.path = path
        self.session_id = session_id
        self.snippet = snippet
        self.line_no = line_no

    def __repr__(self) -> str:
        return f"TranscriptHit({self.path}:{self.line_no} {self.snippet[:60]!r})"


def _list_transcript_files(limit: int | None = None) -> list[Path]:
    """All transcript .md files, newest-first by mtime."""
    root = resolve_inside_vault(TRANSCRIPTS_ROOT)
    if not root.exists():
        return []
    files = sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit] if limit else files


def search_literal(query: str, *, k: int = 5, scan_limit: int = 200) -> list[TranscriptHit]:
    """Case-insensitive substring search across recent transcript files.

    Returns up to `k` hits, newest-first. Scans at most `scan_limit` files to
    bound latency on very large vaults — sufficient for the recall-before-ask
    use case (we care about recent context, not 5-year-old sessions).
    """
    if not query.strip():
        return []
    needle = query.strip().lower()
    vault = Path(settings.vault_path)
    hits: list[TranscriptHit] = []

    for f in _list_transcript_files(limit=scan_limit):
        if len(hits) >= k:
            break
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("transcript read failed", path=str(f), error=str(exc))
            continue
        session_id, body, body_offset = _split_frontmatter(text)
        if needle not in body.lower():
            continue
        rel = str(f.relative_to(vault))
        for i, line in enumerate(body.splitlines(), body_offset):
            if needle in line.lower():
                hits.append(
                    TranscriptHit(
                        path=rel,
                        session_id=session_id,
                        snippet=line.strip(),
                        line_no=i,
                    )
                )
                if len(hits) >= k:
                    break
    return hits


def _split_frontmatter(text: str) -> tuple[str, str, int]:
    """Return (session_id, body_text, body_start_line_no).

    Searches only the body; frontmatter lines (`session_id: ses_legai`) would
    otherwise cause false positives for substring queries that happen to appear
    in metadata. Avoids YAML parsing for speed.
    """
    if not text.startswith("---"):
        return "", text, 1
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text, 1
    fm_block, body = parts[1], parts[2]
    session_id = ""
    for line in fm_block.splitlines():
        if line.startswith("session_id:"):
            session_id = line.split(":", 1)[1].strip()
            break
    # body_offset = lines consumed by `---\n<fm>\n---\n` so reported line_no
    # matches what an editor would show.
    body_offset = 2 + fm_block.count("\n") + 1
    return session_id, body.lstrip("\n"), body_offset
