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
_client_lock = asyncio.Lock()


def get_client() -> AsyncOpenAI:
    """Sync access to the singleton client (initialized lazily).

    `timeout=300s` (5 min) bounds any single HTTP call. Generous enough for
    long completions with many tool rounds, but stops the SDK from waiting on
    truly-dead connections forever (default 600s). External-service timeout
    is fine; memory-stage timeouts are NOT — see feedback_memory_over_speed.
    """
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=300.0)
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
    on_text: Callable[[str], Awaitable[None]] | None = None,
    on_tool_start: Callable[[str], Awaitable[None]] | None = None,
    session_id: str = "",
) -> str:
    """Main agent loop: sends messages, dispatches tool calls, returns final text.

    `on_text` — when set, content tokens are streamed via this callback as they
    arrive (debouncing is the callback's responsibility). The callback receives
    the *full* accumulated text each time, not just deltas — makes
    edit_message_text usage trivial.

    `on_tool_start` — fires once per round when the model decides to call a
    tool, with the tool name. Lets the UI swap the placeholder to "ищу..." etc.

    `session_id` — when non-empty, enables the read-before-ask guard: if the
    model produces a final text that reads like "I don't remember / I don't
    see" but never called `recall`, the loop retries with a guard system
    message forcing a recall first. Max 1 guard retry per turn. See
    `src/agent/guard.py` for rationale.
    """
    from src.agent.guard import GUARD_RETRY_SYSTEM_MESSAGE, is_ignorance_claim

    client = get_client()
    msgs = list(messages)
    guard_retries_left = 1

    for _ in range(max_rounds):
        kwargs: dict[str, Any] = {
            "model": settings.openai_model_main,
            "messages": msgs,
        }
        if tools:
            kwargs["tools"] = tools

        if on_text is None:
            content_buf, tool_calls_list, usage = await _request_nonstream(client, kwargs)
        else:
            content_buf, tool_calls_list, usage = await _request_stream(
                client, kwargs, on_text=on_text
            )

        if usage:
            logger.debug(
                "openai usage",
                prompt=usage.get("prompt_tokens"),
                completion=usage.get("completion_tokens"),
            )

        if tool_calls_list:
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": content_buf,
                "tool_calls": tool_calls_list,
            }
            msgs.append(assistant_dict)

            for tc in tool_calls_list:
                fn = tc["function"]
                if on_tool_start is not None:
                    try:
                        await on_tool_start(fn["name"])
                    except Exception as exc:
                        logger.warning("on_tool_start failed", error=str(exc))
                try:
                    args = json.loads(fn["arguments"] or "{}")
                    result = await dispatch(fn["name"], args)
                except Exception as exc:
                    logger.warning("tool dispatch error", tool=fn["name"], error=str(exc))
                    result = f"ошибка: {exc}"

                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )
            continue

        # No tool calls — this is a candidate final answer. Apply the
        # read-before-ask guard before returning to the caller.
        if (
            guard_retries_left > 0
            and session_id
            and is_ignorance_claim(content_buf)
        ):
            from src.tools.recall import was_recall_done

            if not await was_recall_done(session_id):
                logger.info(
                    "guard: ignorance claim without recall — forcing retry",
                    session_id=session_id,
                    preview=content_buf[:120],
                )
                # Hint the UI that we're going back to memory. The retry will
                # almost certainly call `recall`, which itself fires
                # on_tool_start("recall") via the tool dispatch path — but we
                # set the placeholder now so the user doesn't briefly see the
                # rejected ignorance text linger.
                if on_tool_start is not None:
                    try:
                        await on_tool_start("recall")
                    except Exception as exc:
                        logger.warning("on_tool_start (guard) failed", error=str(exc))
                msgs.append({"role": "assistant", "content": content_buf})
                msgs.append({"role": "system", "content": GUARD_RETRY_SYSTEM_MESSAGE})
                guard_retries_left -= 1
                continue

        return content_buf

    raise RuntimeError("exceeded max tool rounds")


async def _request_nonstream(
    client: AsyncOpenAI,
    kwargs: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Plain non-streaming request — returns (content, tool_calls, usage)."""
    response = await _call_with_retry(client, **kwargs)
    msg = response.choices[0].message
    tool_calls_list: list[dict[str, Any]] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tool_calls_list.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )
    usage: dict[str, Any] | None = None
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
        }
    return msg.content or "", tool_calls_list, usage


async def _request_stream(
    client: AsyncOpenAI,
    kwargs: dict[str, Any],
    on_text: Callable[[str], Awaitable[None]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Streaming request — accumulates content and (incrementally-emitted)
    tool_calls, firing `on_text(full_accumulated)` as text grows.

    Tool calls arrive as positional deltas: each chunk may include partial
    `tool_calls[i].function.name` or `function.arguments`. We collect by index
    and reconstruct the full list at end.
    """
    kwargs = {**kwargs, "stream": True, "stream_options": {"include_usage": True}}
    content_buf = ""
    tool_acc: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None

    for attempt in range(3):
        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_buf += delta.content
                    try:
                        await on_text(content_buf)
                    except Exception as exc:
                        logger.warning("on_text callback failed", error=str(exc))
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        slot = tool_acc.setdefault(
                            idx,
                            {"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}},
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
            break
        except (APIConnectionError, RateLimitError) as exc:
            if attempt == 2:
                raise
            wait = 2**attempt
            logger.warning("openai stream retry", attempt=attempt + 1, error=str(exc), wait=wait)
            await asyncio.sleep(wait)
            content_buf = ""
            tool_acc = {}

    tool_calls_list = [tool_acc[i] for i in sorted(tool_acc) if tool_acc[i].get("id")]
    return content_buf, tool_calls_list, usage
