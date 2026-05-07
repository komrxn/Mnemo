from __future__ import annotations

import random
from pathlib import Path

import structlog

from src.agent.loop import get_client
from src.config import settings
from src.vault import writer as vault_writer

logger = structlog.get_logger()

_PROMPT = """\
ACT AS: Senior Data Ontologist & Knowledge Graph Architect.
TASK: Analyze the provided sample of user's vault notes to extract a domain ontology.
GOAL: Define a concise list of high-level Entity Types covering the user's domain.

GUIDELINES:
- Abstraction: prefer broad categories ("Organization" not "Company"/"Startup").
- Relevance: include abstract concepts ("Concept", "Methodology", "Goal").
- Coverage: types should classify >=90% of key nouns in the text.

RULES:
1. Output ONLY a comma-separated list of types. NO preamble, NO markdown.
2. Types: singular, PascalCase (Person, Project, ResearchPaper).
3. 8-15 types total.
4. Output MUST be in English.

SAMPLE:
{sample}

YOUR OUTPUT (comma-separated types only):"""


async def generate_ontology(sample_size: int = 10) -> list[str]:
    """Generate entity_types from a vault note sample and save to _meta/ontology.md."""
    vault = Path(settings.vault_path)
    all_md = [
        m for m in vault.rglob("*.md")
        if not str(m.relative_to(vault)).startswith(("_meta/", "10_Daily/", "00_Inbox/"))
    ]
    if len(all_md) < 5:
        logger.info("ontology gen skipped: too few notes", count=len(all_md))
        return []

    sample = random.sample(all_md, min(sample_size, len(all_md)))
    sample_text = ""
    for md in sample:
        rel = str(md.relative_to(vault))
        content = md.read_text(encoding="utf-8")[:1000]
        sample_text += f"--- {rel} ---\n{content}\n...\n\n"

    client = get_client()
    response = await client.chat.completions.create(
        model=settings.openai_model_main,
        messages=[{"role": "user", "content": _PROMPT.format(sample=sample_text)}],
    )
    raw = (response.choices[0].message.content or "").strip()
    raw = raw.removeprefix("Output:").strip().strip("[]")
    types = [t.strip() for t in raw.split(",") if t.strip()]

    if not types:
        logger.warning("ontology gen returned empty")
        return []

    body = (
        "Авто-сгенерированная онтология для LightRAG. "
        "Не редактируй — перезаписывается при еженедельном full_reindex.\n\n"
        f"## Types\n\n{', '.join(types)}\n"
    )
    await vault_writer.write_note(
        "_meta/ontology.md",
        body,
        {"type": "inbox", "auto_generated": True},
    )
    logger.info("ontology generated", types=types, count=len(types))
    return types
