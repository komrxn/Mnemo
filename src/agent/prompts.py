from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Load prompts/<name>.md verbatim (cached after first read)."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def render(name: str, **kwargs: object) -> str:
    """Load and render a Jinja2 template from prompts/<name>.md."""
    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    return env.get_template(f"{name}.md").render(**kwargs).strip()
