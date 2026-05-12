"""Read-before-ask guard — structural enforcement of memory-first behavior.

Mnemo's core promise is "the bot never forgets." A prompt instruction asking
the LLM to call `recall` before claiming ignorance is *soft*: the LLM may
ignore it and the bug returns. This module makes the rule *hard*:

1. `is_ignorance_claim(text)` — pattern match over ru/en/uz phrases the bot
   uses when it lacks knowledge ("не помню", "I don't see it", "menda yo'q").
2. `agent_loop.run_chat` consults `is_ignorance_claim` on every final-text
   round. If the claim was made WITHOUT a prior `recall` (Redis marker
   `session:recall_done:{session_id}`), the loop retries the LLM call with a
   guard system message forcing a recall first. Max 1 guard retry per turn.
3. The `set_pending_slot` tool similarly refuses to register a clarifying-
   question slot until `recall` was called — so the bot can't ask the user
   something it could have looked up.

The cost is +1 LLM round in the worst case (ignorance asserted blindly →
guard retry → LLM tries recall → final answer). The benefit is that the
"Ресторан БЕК" failure mode is *structurally* impossible, not just
discouraged in the prompt.
"""

from __future__ import annotations

import re

# Patterns we consider "ignorance about memory" — bot is saying it doesn't
# have/remember/know information the user is asking about. Conservative on
# Russian (the primary dev language); permissive on English/Uzbek to catch
# obvious cases regardless of code-switching.
#
# Each entry is a regex with word-boundary anchors where applicable. We do not
# use `re.IGNORECASE` because Cyrillic word-boundaries with Python's `re` are
# fragile when mixed with `\b`; instead each variant covers its own case.
_IGNORANCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Russian — ignorance verbs
    re.compile(r"не\s+пом(ню|нится|нит)", re.IGNORECASE),
    re.compile(r"не\s+зна(ю|ем|ет)", re.IGNORECASE),
    re.compile(r"не\s+ви(жу|дел|дно)", re.IGNORECASE),
    re.compile(r"не\s+на(шёл|шла|шли)", re.IGNORECASE),
    # Russian — "I only have X" / "nothing about Y" — direct from BEK bug
    re.compile(r"у\s+меня\s+(нет|только)", re.IGNORECASE),
    re.compile(r"в\s+пам(яти|ятке)\s+(нет|пусто)", re.IGNORECASE),
    re.compile(r"(ты|вы)\s+не\s+(упомина|говори|рассказ|писа)", re.IGNORECASE),
    re.compile(r"впервые\s+слыш", re.IGNORECASE),
    # English
    re.compile(
        r"\b(do(n['’]t| not)|cannot|can't)\s+(remember|recall|know|see|find)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bno\s+(record|memory|info|mention)\b", re.IGNORECASE),
    re.compile(
        r"\byou\s+(haven['’]t|did(n['’]t| not))\s+"
        r"(mention(ed)?|tell|told|say|said)\b",
        re.IGNORECASE,
    ),
    # Uzbek
    re.compile(r"eslolma(yman|y)", re.IGNORECASE),
    re.compile(r"bilma(yman|y)", re.IGNORECASE),
    re.compile(r"ko['’]rmaya?p", re.IGNORECASE),
    re.compile(r"menda\s+yo['’]?q", re.IGNORECASE),
    re.compile(r"(sen|siz)\s+aytma(gansan|gansiz)", re.IGNORECASE),
)


def is_ignorance_claim(text: str) -> bool:
    """True iff `text` reads like the bot is claiming it doesn't have / can't
    find information the user is asking about.

    Over-fires intentionally: a false positive costs one extra recall round
    (cheap), a false negative leaves the BEK-class bug open (costly). Designed
    to be paired with a `was_recall_done` check — together they form the gate.
    """
    if not text:
        return False
    sample = text[:600]  # check the head; tail is usually elaboration
    return any(p.search(sample) for p in _IGNORANCE_PATTERNS)


# System message injected into the LLM context when the guard fires. English
# is deliberate — it's a system directive, more reliably parsed by gpt-5.4
# than mixed-language prose, and never reaches the user.
GUARD_RETRY_SYSTEM_MESSAGE = (
    "[INTERNAL GUARD — read-before-ask]\n\n"
    "Your previous draft response claimed you don't have / don't remember / "
    "don't see information the user is asking about — but you did NOT call "
    "`recall` first. This violates the read-before-ask invariant of this "
    "system.\n\n"
    "DO NOW (in this exact order):\n"
    "1. Call `recall(query=...)` with the key term from the user's question "
    "(name, topic, person, project). Use the literal term, not a paraphrase.\n"
    "2. Examine recall's output. If it returns a literal match — use it in "
    "your reply (quote it exactly, do not normalize names).\n"
    "3. If recall confirms nothing exists for that term across all memory "
    "layers (current session, transcripts, vault, semantic), only THEN you "
    "may honestly say you don't have that information.\n\n"
    "Now produce the final reply to the user, in their dialog language."
)
