from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal, cast

import structlog
import yaml
from jinja2 import Template

logger = structlog.get_logger()

Language = Literal["ru", "en", "uz"]
LANGUAGES: tuple[Language, ...] = ("ru", "en", "uz")
DEFAULT_LANGUAGE: Language = "ru"

_LOCALES_DIR = Path(__file__).parent / "locales"


@cache
def _load(lang: str) -> dict[str, object]:
    path = _LOCALES_DIR / f"{lang}.yaml"
    if not path.exists():
        logger.warning("locale file missing", lang=lang)
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return cast(dict[str, object], data)


def _lookup(data: dict[str, object], key: str) -> str | None:
    node: object = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def normalize_lang(lang: str | None) -> Language:
    if lang == "ru":
        return "ru"
    if lang == "en":
        return "en"
    if lang == "uz":
        return "uz"
    return DEFAULT_LANGUAGE


def t(key: str, lang: str | None = None, **vars: object) -> str:
    """Translate `key` into `lang`.

    Fallback chain: lang → DEFAULT_LANGUAGE → key itself.
    Variables interpolated via Jinja2.
    """
    resolved_lang = normalize_lang(lang)

    raw = _lookup(_load(resolved_lang), key)
    if raw is None and resolved_lang != DEFAULT_LANGUAGE:
        raw = _lookup(_load(DEFAULT_LANGUAGE), key)
    if raw is None:
        logger.warning("i18n key missing", key=key, lang=resolved_lang)
        return key

    if not vars:
        return raw
    return Template(raw, autoescape=False, keep_trailing_newline=False).render(**vars)


def reset_cache() -> None:
    """Drop cached locale data. Use in tests after editing yaml files."""
    _load.cache_clear()
