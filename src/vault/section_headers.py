"""Localized section headers for Markdown notes.

Writers pick the header in the user's notes_language. Readers must accept ALL
known variants so a vault containing notes in mixed languages (e.g. after the
user switched notes_language mid-flight) still parses correctly.
"""

from __future__ import annotations

FACTS_HEADERS: dict[str, str] = {
    "ru": "## Факты",
    "en": "## Facts",
    "uz": "## Faktlar",
}

LINKS_HEADERS: dict[str, str] = {
    "ru": "## Связи",
    "en": "## Links",
    "uz": "## Bog'lanishlar",
}


def facts_header(lang: str) -> str:
    return FACTS_HEADERS.get(lang, FACTS_HEADERS["ru"])


def links_header(lang: str) -> str:
    return LINKS_HEADERS.get(lang, LINKS_HEADERS["ru"])


def _split_on_any(body: str, candidates: list[str]) -> tuple[str, str | None]:
    """If body contains any of the candidate headers, split on the first one.

    Returns (head, header_found) where head is content before the header
    (stripped of trailing whitespace) and header_found is the matched string
    or None if no header was present.
    """
    earliest_idx = -1
    matched: str | None = None
    for h in candidates:
        idx = body.find(h)
        if idx == -1:
            continue
        if earliest_idx == -1 or idx < earliest_idx:
            earliest_idx = idx
            matched = h
    if matched is None:
        return body, None
    return body[:earliest_idx].rstrip(), matched


def strip_links_section(body: str) -> str:
    """Remove the links section (any language variant) from a note body."""
    head, _ = _split_on_any(body, list(LINKS_HEADERS.values()))
    return head


def find_links_marker(body: str) -> str | None:
    """Return whichever ## Links variant exists in body, or None."""
    for h in LINKS_HEADERS.values():
        if h in body:
            return h
    return None
