"""Refresh `_meta/owner.md` from the current vault state.

Called after sessions that introduce fundamental facts about the owner —
new job, life-event memory, top-level theme, close-circle person. Reads
related notes from vault, asks LLM to synthesize a fresh owner-card,
overwrites owner.md.

Why not refresh after EVERY session: most sessions add details to existing
sub-graphs (a new task in a known project, a thought, etc.) which don't
change the high-level picture of who the owner is. Refresh is gated on
whether something fundamental changed — see `should_refresh_after`.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from src.agent.loop import get_client
from src.config import settings
from src.vault.frontmatter import parse

logger = structlog.get_logger()

_OWNER_PATH = "_meta/owner.md"

_OWNER_REFRESH_PROMPTS: dict[str, str] = {
    "ru": (
        "Ты обновляешь карточку владельца Mnemo. На вход — дамп его "
        "работ, важных воспоминаний и тем интересов из vault'а. "
        'Верни JSON: {"facts": ["факт1", ...]}.\n\n'
        "facts: 5-10 КЛЮЧЕВЫХ актуальных фактов про САМОГО владельца "
        "(не про его проекты или знакомых). Конкретно: текущая работа/"
        "учёба, ключевые жизненные события, главные интересы, цели. "
        "Если что-то поменялось (новая работа, переезд) — отрази "
        "актуальное состояние, старое в прошедшем времени."
    ),
    "en": (
        "You're refreshing the owner card for Mnemo. Input — a dump of "
        "their jobs, important memories and themes from the vault. "
        'Return JSON: {"facts": ["fact1", ...]}.\n\n'
        "facts: 5-10 KEY current facts about the OWNER themselves "
        "(not about their projects or contacts). Concrete: current "
        "job/study, key life events, main interests, goals. If "
        "something changed (new job, moved) — reflect the current "
        "state, with the old one in past tense."
    ),
    "uz": (
        "Sen Mnemo egasining kartochkasini yangilayapsan. Kirish — "
        "uning ishlari, muhim xotiralari va vault'dagi qiziqish "
        'mavzularining damp\'i. JSON qaytar: {"facts": ["fakt1", ...]}.\n\n'
        "facts: EGA haqida 5-10 ta ASOSIY dolzarb fakt (loyihalari "
        "yoki tanishlari haqida emas). Aniq: hozirgi ish/o'qish, "
        "asosiy hayot voqealari, asosiy qiziqishlar, maqsadlar. Agar "
        "biror narsa o'zgargan bo'lsa (yangi ish, ko'chish) — joriy "
        "holatni ko'rsat, eskisi o'tgan zamonda."
    ),
}

_OWNER_REFRESH_LABELS: dict[str, tuple[str, str]] = {
    "ru": ("Имя владельца", "Дамп vault'а"),
    "en": ("Owner name", "Vault dump"),
    "uz": ("Ega ismi", "Vault damp'i"),
}


def should_refresh_after(touched_paths: list[str]) -> bool:
    """Decide whether the freshly-touched paths warrant an owner.md refresh.

    Triggers:
      - any new/updated job (work life)
      - any new/updated memory (life event)
      - any new/updated top-level theme (interests/values)
    """
    triggers = ("30_Jobs/", "70_Memories/", "80_Themes/")
    for path in touched_paths:
        if any(path.startswith(t) for t in triggers):
            return True
    return False


def _read_relevant_notes() -> str:
    """Read all jobs + top memories + top themes — give LLM the picture.

    Returns a compact text dump (path + body excerpt) capped at ~6KB to keep
    LLM input cheap.
    """
    vault = Path(settings.vault_path)
    pieces: list[str] = []

    # Jobs — all (usually 1-3)
    for md in sorted((vault / "30_Jobs").rglob("*.md")) if (vault / "30_Jobs").exists() else []:
        try:
            raw = md.read_text(encoding="utf-8")
            _, body = parse(raw)
            rel = md.relative_to(vault)
            pieces.append(f"--- {rel} ---\n{body[:500]}")
        except OSError:
            continue

    # Memories — most recent 8 (life events shape "who am I")
    if (vault / "70_Memories").exists():
        memories = sorted(
            (vault / "70_Memories").rglob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:8]
        for md in memories:
            try:
                raw = md.read_text(encoding="utf-8")
                _, body = parse(raw)
                rel = md.relative_to(vault)
                pieces.append(f"--- {rel} ---\n{body[:400]}")
            except OSError:
                continue

    # Themes — top 8 by mtime
    if (vault / "80_Themes").exists():
        themes = sorted(
            (vault / "80_Themes").rglob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:8]
        for md in themes:
            try:
                raw = md.read_text(encoding="utf-8")
                _, body = parse(raw)
                rel = md.relative_to(vault)
                pieces.append(f"--- {rel} ---\n{body[:300]}")
            except OSError:
                continue

    return "\n\n".join(pieces)[:6000]


async def refresh_owner_from_vault(owner_name: str) -> bool:
    """Pass the relevant vault snapshot to LLM, get fresh facts, overwrite owner.md.

    Returns True if refreshed, False if skipped (no notes / LLM failure).
    Idempotent — safe to call multiple times.
    """
    snapshot = _read_relevant_notes()
    if not snapshot.strip():
        return False

    # Pull notes_language so the regenerated facts match vault language
    from src.session.manager import get_notes_language, get_profile, get_redis

    notes_lang = "ru"
    try:
        _redis = await get_redis()
        _profile = await get_profile(_redis, settings.allowed_user_ids[0])
        notes_lang = get_notes_language(_profile)
    except Exception:
        pass

    sys_prompt = _OWNER_REFRESH_PROMPTS.get(notes_lang, _OWNER_REFRESH_PROMPTS["ru"])
    owner_label, dump_label = _OWNER_REFRESH_LABELS.get(notes_lang, _OWNER_REFRESH_LABELS["ru"])

    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model_fast,
            messages=[
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": f"{owner_label}: {owner_name}\n\n{dump_label}:\n\n{snapshot}",
                },
            ],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("owner refresh LLM failed", error=str(exc))
        return False

    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except Exception:
        return False
    facts: list[str] = data.get("facts", []) or []
    if not facts:
        return False

    # Read current owner.md to preserve aliases + is_owner flag
    from src.vault import reader, writer

    aliases = [owner_name]
    if reader.note_exists(_OWNER_PATH):
        try:
            current = reader.read_note(_OWNER_PATH)
            existing_aliases = current.frontmatter.aliases or []
            if existing_aliases:
                aliases = existing_aliases
        except Exception:
            pass

    body = "\n".join(f"- {f}" for f in facts)
    fm: dict[str, object] = {"type": "person", "is_owner": True, "aliases": aliases}

    try:
        await writer.write_note(_OWNER_PATH, body, fm)
        logger.info("owner.md refreshed from vault", facts=len(facts))
        return True
    except Exception as exc:
        logger.warning("owner.md refresh write failed", error=str(exc))
        return False
