from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from openai import APIConnectionError, AsyncOpenAI, RateLimitError

from src.config import settings

logger = structlog.get_logger()

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def build_messages(
    system_prompt: str,
    user_profile: dict[str, Any],
    history: list[dict[str, str]],
    new_content: str,
) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if user_profile:
        import orjson

        msgs.append(
            {
                "role": "system",
                "content": f"Профиль пользователя: {orjson.dumps(user_profile).decode()}",
            }
        )
    msgs.extend(history)
    msgs.append({"role": "user", "content": new_content})
    return msgs


async def _call_with_retry(
    client: AsyncOpenAI,
    **kwargs: Any,
) -> Any:
    """Call OpenAI with exponential backoff on connection/rate-limit errors."""
    for attempt in range(3):
        try:
            return await client.chat.completions.create(**kwargs)
        except (APIConnectionError, RateLimitError) as exc:
            if attempt == 2:
                raise
            wait = 2**attempt
            logger.warning("openai retry", attempt=attempt + 1, error=str(exc), wait=wait)
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


async def run_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    dispatch: Callable[[str, dict[str, Any]], Awaitable[str]],
    max_rounds: int = 15,
) -> str:
    """Main agent loop: sends messages, dispatches tool calls, returns final text."""
    client = get_client()
    msgs = list(messages)

    for _ in range(max_rounds):
        kwargs: dict[str, Any] = {
            "model": settings.openai_model_main,
            "messages": msgs,
        }
        if tools:
            kwargs["tools"] = tools

        response = await _call_with_retry(client, **kwargs)
        choice = response.choices[0]

        if response.usage:
            logger.debug(
                "openai usage",
                prompt=response.usage.prompt_tokens,
                completion=response.usage.completion_tokens,
            )

        msg = choice.message

        if msg.tool_calls:
            # Append assistant message with tool_calls (serialize carefully)
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            msgs.append(assistant_dict)

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                    result = await dispatch(tc.function.name, args)
                except Exception as exc:
                    logger.warning("tool dispatch error", tool=tc.function.name, error=str(exc))
                    result = f"ошибка: {exc}"

                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
        else:
            return msg.content or ""

    raise RuntimeError("exceeded max tool rounds")
