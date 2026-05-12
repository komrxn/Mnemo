from __future__ import annotations

from functools import cache
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

logger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_DEFAULT_LANG = "ru"


def _resolve_path(name: str, lang: str) -> tuple[Path, str]:
    """Locate prompts/{lang}/{name}.md with fallback to ru. Returns (path, lang_used)."""
    primary = _PROMPTS_DIR / lang / f"{name}.md"
    if primary.exists():
        return primary, lang
    fallback = _PROMPTS_DIR / _DEFAULT_LANG / f"{name}.md"
    if not fallback.exists():
        raise FileNotFoundError(f"prompt not found: {name} (lang={lang} or {_DEFAULT_LANG})")
    if lang != _DEFAULT_LANG:
        logger.info("prompt fallback", name=name, requested=lang, used=_DEFAULT_LANG)
    return fallback, _DEFAULT_LANG


@cache
def load(name: str, lang: str = _DEFAULT_LANG) -> str:
    """Load prompts/{lang}/{name}.md verbatim (cached)."""
    path, _ = _resolve_path(name, lang)
    return path.read_text(encoding="utf-8").strip()


def render(name: str, lang: str = _DEFAULT_LANG, **kwargs: object) -> str:
    """Load and render a Jinja2 template from prompts/{lang}/{name}.md."""
    _, lang_used = _resolve_path(name, lang)
    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR / lang_used)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    return env.get_template(f"{name}.md").render(**kwargs).strip()
