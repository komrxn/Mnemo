"""Topic-coherence gate for appends to existing notes.

Background — production bug observed 2026-05-18: a note titled
"Бакалавриат в финансовом" received facts about the user's trout business
("форели") because nothing along the write path checked that the appended
block was on-topic with the target note. After the bleed, "форели" became
an alias of the bachelor's note → smart linker created a false edge in
the graph between two unrelated entities.

This module provides the gate every append site must call before writing:
`is_block_coherent_with_note(block, path) -> 'ok' | 'mismatch' | 'unsure'`.

Two-stage design:
  - Stage 1 (always, ~0ms): rapidfuzz over title + aliases + first ~100 body
    words. Decisive both ways at the extremes (≥75 = ok, <40 = mismatch).
  - Stage 2 (only on borderline 40-75, ~400ms): tight YES/NO call to the
    fast model. The mini-LLM has full context the fuzzy step can't capture
    (semantic similarity, e.g. "новый поставщик мяса" fits a restaurant
    note even though the literal tokens don't overlap).

Caller policy on the verdict (set by each call site):
  - `ok`        → proceed with append.
  - `mismatch`  → refuse the append.
  - `unsure`    → callers without an agent loop (extractor / write_pipeline)
                  treat as `ok` to avoid blocking legitimate evolution.
                  The tool-layer caller passes `unsure` to the agent as
                  a soft warning so the agent can decide.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from rapidfuzz import fuzz

from src.vault import reader  # module-level so tests can patch via this module

logger = structlog.get_logger()

CoherenceVerdict = Literal["ok", "mismatch", "unsure"]

# Tuned on the bleed example (Лола/Хилола taught us partial_ratio is too
# generous on substrings; token_set is more robust here because we compare
# multi-word blocks to multi-word titles).
_FUZZY_OK_THRESHOLD = 75
_FUZZY_MISMATCH_THRESHOLD = 40
# Pull only the first chunk of body for fuzzy compare — enough to capture
# what the note is "about", not so much that we drown the signal in noise.
_BODY_PREVIEW_WORDS = 100
# LLM-fallback only on borderline scores. Cap how many chars we send.
_LLM_BODY_PREVIEW_CHARS = 600
_LLM_BLOCK_PREVIEW_CHARS = 600


def _summarize_block(block: str) -> str:
    """Pull a representative chunk of the block for fuzzy compare.

    Markdown headers / bullets are noise — strip the markdown leaders but
    keep the words. First 200 chars is usually enough to characterize topic.
    """
    cleaned = re.sub(r"[#*\-`>\[\]()]+", " ", block)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:200]


def _body_preview(body: str, max_words: int = _BODY_PREVIEW_WORDS) -> str:
    """First N words of body, with markdown stripped."""
    cleaned = re.sub(r"[#*\-`>\[\]()]+", " ", body)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    return " ".join(words[:max_words])


def _fuzzy_score(block: str, title: str, aliases: list[str], body: str) -> int:
    """Best-of-many score: block vs title / aliases / body preview.

    Different scorers for different signals:
      - title / aliases (short, often 1-2 tokens): partial_ratio — we want
        "title appears as substring in block". token_set_ratio is too strict
        here because a long block vs short title has asymmetric token sets
        (most block tokens don't appear in title, dragging the score down).
      - body (long, descriptive): max(partial, token_set) — block overlaps
        with body's domain words either as substring or as shared tokens.

    Why not WRatio: WRatio bakes in partial_ratio which gives 100 to any
    substring containment ("лола" ⊂ "хилола"). We learned in person dedup
    that's too eager. Here we want partial for title (intentional) but not
    blended with other metrics that fire on coincidental overlaps.
    """
    block_norm = _summarize_block(block).lower()
    if not block_norm:
        return 0

    candidates: list[int] = []
    if title.strip():
        candidates.append(int(fuzz.partial_ratio(block_norm, title.lower())))
    for alias in aliases:
        if alias.strip():
            candidates.append(int(fuzz.partial_ratio(block_norm, alias.lower())))
    body_norm = _body_preview(body).lower()
    if body_norm:
        candidates.append(int(fuzz.partial_ratio(block_norm, body_norm)))
        candidates.append(int(fuzz.token_set_ratio(block_norm, body_norm)))

    return max(candidates) if candidates else 0


_LLM_SYSTEM_PROMPT = (
    "You decide whether a block of text belongs in an existing note. "
    "Answer with EXACTLY one word: YES or NO. "
    "YES = the block is about the same subject/entity the note is about, "
    "even if the specific facts are new (a restaurant note getting a new "
    "supplier fact is YES). "
    "NO = the block is about a completely different subject (a bachelor's "
    "degree note getting trout business facts is NO). "
    "When in doubt, prefer NO — the user can always create a separate note."
)


async def _llm_verdict(block: str, title: str, body: str) -> CoherenceVerdict:
    """Borderline-only fast call to the mini model. ~400ms p50."""
    try:
        from src.agent import loop as agent_loop
        from src.config import settings

        client = agent_loop.get_client()
        body_snip = _body_preview(body, max_words=200)[:_LLM_BODY_PREVIEW_CHARS]
        block_snip = block.strip()[:_LLM_BLOCK_PREVIEW_CHARS]
        user_msg = (
            f"Note title: {title!r}\n"
            f"Note body (excerpt): {body_snip!r}\n"
            f"Block to append: {block_snip!r}"
        )
        resp = await client.chat.completions.create(
            model=settings.openai_model_fast,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_completion_tokens=4,
        )
        text = (resp.choices[0].message.content or "").strip().upper()
        # Some models wrap with punctuation / quotes.
        text = re.sub(r"[^A-Z]", "", text)
        if text.startswith("YES"):
            return "ok"
        if text.startswith("NO"):
            return "mismatch"
        # Anything else → treat as unsure rather than guess.
        logger.warning("coherence LLM returned unparseable verdict", text=text[:32])
        return "unsure"
    except Exception as exc:
        # Network/model failure must not block writes — degrade to unsure.
        logger.warning("coherence LLM call failed, degrading to unsure", error=str(exc))
        return "unsure"


async def is_block_coherent_with_note(
    block: str,
    note_path: str,
) -> CoherenceVerdict:
    """Verdict on whether `block` belongs in the note at `note_path`.

    Returns `ok` / `mismatch` / `unsure`. Caller decides what to do on each.

    Safe by default: if the note can't be read for any reason, returns `ok`
    — refusing writes because of an unrelated I/O hiccup would be worse than
    the bleed we're trying to prevent.
    """
    if not block or not block.strip():
        # Empty block is benign — let the underlying writer reject it if needed.
        return "ok"

    try:
        if not reader.note_exists(note_path):
            # No existing note → this isn't an append we're guarding, the
            # caller will end up creating a fresh note.
            return "ok"
        note = reader.read_note(note_path)
    except Exception as exc:
        logger.warning(
            "coherence: note read failed, defaulting to ok",
            path=note_path,
            error=str(exc),
        )
        return "ok"

    # Title = filename stem with separators humanized.
    title = note_path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ")
    aliases = list(note.frontmatter.aliases or [])
    score = _fuzzy_score(block, title, aliases, note.body)

    if score >= _FUZZY_OK_THRESHOLD:
        return "ok"
    if score < _FUZZY_MISMATCH_THRESHOLD:
        logger.info(
            "coherence: fuzzy mismatch",
            path=note_path,
            score=score,
        )
        return "mismatch"

    # Borderline — pay for the LLM.
    logger.info("coherence: LLM fallback", path=note_path, score=score)
    return await _llm_verdict(block, title, note.body)
