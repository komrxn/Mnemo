from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


@dataclass
class ToolDef:
    name: str
    description: str
    params_cls: type[BaseModel]
    handler: Callable[..., Awaitable[str]]

    def to_openai(self) -> dict[str, Any]:
        schema = self.params_cls.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def openai_specs(self) -> list[dict[str, Any]]:
        return [t.to_openai() for t in self._tools.values()]

    async def call(self, name: str, args: dict[str, Any], *, session_id: str = "") -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"unknown tool: {name!r}"
        try:
            params = tool.params_cls.model_validate(args)
            return await tool.handler(params, session_id=session_id)
        except Exception as exc:
            logger.warning("tool error", tool=name, error=str(exc))
            return f"ошибка инструмента {name!r}: {exc}"


# Singleton — populated by tools/obsidian.py and tools/misc.py at import time
_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _registry
