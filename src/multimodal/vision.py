from __future__ import annotations

import base64
from pathlib import Path

from src.agent.loop import get_client
from src.config import settings

_VISION_PROMPT = (
    "Опиши что на изображении подробно и конкретно. "
    "Если есть текст — воспроизведи его точно (OCR). "
    "Если это скриншот — опиши содержимое экрана."
)

_MIME_MAP = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


async def describe(image_path: Path) -> str:
    """Get GPT-vision description + OCR of an image.

    File read is offloaded to a thread to avoid blocking the event loop on
    large images.
    """
    import asyncio

    client = get_client()
    raw = await asyncio.to_thread(image_path.read_bytes)
    data = base64.standard_b64encode(raw).decode()
    mime = _MIME_MAP.get(image_path.suffix.lstrip(".").lower(), "image/jpeg")

    response = await client.chat.completions.create(
        model=settings.openai_model_vision,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"},
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }
        ],
        max_completion_tokens=1500,
    )
    return response.choices[0].message.content or ""
