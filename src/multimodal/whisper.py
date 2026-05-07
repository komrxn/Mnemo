from __future__ import annotations

from pathlib import Path

from src.agent.loop import get_client
from src.config import settings


async def transcribe(audio_path: Path) -> str:
    """Transcribe audio file via OpenAI Whisper API."""
    client = get_client()
    with audio_path.open("rb") as f:
        response = await client.audio.transcriptions.create(
            model=settings.openai_whisper_model,
            file=f,
            language="ru",
        )
    return response.text
