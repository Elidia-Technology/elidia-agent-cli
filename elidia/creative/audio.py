"""Audio generation via AiUtils Developer API.

Text-to-speech (ElevenLabs, OpenAI TTS) and music generation (Suno).
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from elidia.api.client import AiUtilsClient

logger = logging.getLogger(__name__)

TTS_MODELS = {
    "elevenlabs-v2": {"vendor": "elevenlabs", "voices": ["rachel", "adam", "josh", "bella", "elli"]},
    "tts-1": {"vendor": "openai", "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]},
    "tts-1-hd": {"vendor": "openai", "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]},
}

MUSIC_MODELS = {
    "suno-v4": {"vendor": "suno", "max_duration": 240},
}

DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_MUSIC_MODEL = "suno-v4"
DEFAULT_OUTPUT_DIR = Path.home() / ".elidia" / "generated" / "audio"


@dataclass
class AudioResult:
    file_path: str
    model: str
    text: str
    voice: str
    format: str = "mp3"
    size_bytes: int = 0
    elapsed_ms: int = 0


@dataclass
class MusicResult:
    file_path: str
    model: str
    prompt: str
    duration: int
    format: str = "mp3"
    size_bytes: int = 0
    elapsed_ms: int = 0


async def generate_speech(
    client: AiUtilsClient,
    text: str,
    model: str = DEFAULT_TTS_MODEL,
    voice: str = "alloy",
    output_dir: Path | None = None,
) -> AudioResult:
    logger.debug(f"Entered into generate_speech: model={model}, voice={voice}, text_len={len(text)}")
    start = time.monotonic()

    save_dir = output_dir or DEFAULT_OUTPUT_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }

    async with httpx.AsyncClient(
        base_url=client._base_url,
        headers={
            "Authorization": f"Bearer {client._api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(60.0, connect=10.0),
    ) as http:
        response = await http.post("/audio/speech", json=payload)

        if response.status_code == 401:
            raise RuntimeError("Invalid API key for audio generation")
        if response.status_code == 402:
            raise RuntimeError("Insufficient balance for audio generation")
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "audio" in content_type or "octet-stream" in content_type:
            audio_bytes = response.content
        else:
            data = response.json()
            b64 = data.get("audio", data.get("data", ""))
            if b64:
                audio_bytes = base64.b64decode(b64)
            else:
                raise RuntimeError("No audio data in response")

    timestamp = int(time.time())
    safe_text = "".join(c if c.isalnum() or c in " -_" else "" for c in text[:30]).strip().replace(" ", "_")
    filename = f"{timestamp}_{voice}_{safe_text}.mp3"
    file_path = save_dir / filename
    file_path.write_bytes(audio_bytes)

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Speech generated: {file_path} ({len(audio_bytes)} bytes, {elapsed}ms)")

    return AudioResult(
        file_path=str(file_path),
        model=model,
        text=text,
        voice=voice,
        format="mp3",
        size_bytes=len(audio_bytes),
        elapsed_ms=elapsed,
    )


async def generate_music(
    client: AiUtilsClient,
    prompt: str,
    model: str = DEFAULT_MUSIC_MODEL,
    duration: int = 30,
    output_dir: Path | None = None,
) -> MusicResult:
    logger.debug(f"Entered into generate_music: model={model}, prompt={prompt[:60]!r}")
    start = time.monotonic()

    save_dir = output_dir or DEFAULT_OUTPUT_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    model_info = MUSIC_MODELS.get(model, MUSIC_MODELS[DEFAULT_MUSIC_MODEL])
    duration = min(duration, model_info["max_duration"])

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
    }

    async with httpx.AsyncClient(
        base_url=client._base_url,
        headers={
            "Authorization": f"Bearer {client._api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as http:
        response = await http.post("/audio/music", json=payload)

        if response.status_code == 401:
            raise RuntimeError("Invalid API key for music generation")
        if response.status_code == 402:
            raise RuntimeError("Insufficient balance for music generation")
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "audio" in content_type or "octet-stream" in content_type:
            audio_bytes = response.content
        else:
            data = response.json()
            b64 = data.get("audio", data.get("data", ""))
            if b64:
                audio_bytes = base64.b64decode(b64)
            elif data.get("url"):
                async with httpx.AsyncClient(timeout=60.0) as dl:
                    dl_resp = await dl.get(data["url"])
                    dl_resp.raise_for_status()
                    audio_bytes = dl_resp.content
            else:
                raise RuntimeError("No audio data or URL in response")

    timestamp = int(time.time())
    safe_prompt = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt[:30]).strip().replace(" ", "_")
    filename = f"{timestamp}_music_{safe_prompt}.mp3"
    file_path = save_dir / filename
    file_path.write_bytes(audio_bytes)

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Music generated: {file_path} ({len(audio_bytes)} bytes, {elapsed}ms)")

    return MusicResult(
        file_path=str(file_path),
        model=model,
        prompt=prompt,
        duration=duration,
        format="mp3",
        size_bytes=len(audio_bytes),
        elapsed_ms=elapsed,
    )


async def transcribe_audio(
    client: AiUtilsClient,
    file_path: str | Path,
    model: str = "whisper-1",
    language: str | None = None,
) -> str:
    """Transcribe audio to text via AiUtils API (Whisper).

    Supports mp3, wav, m4a, ogg, webm, flac.
    """
    logger.debug(f"Entered into transcribe_audio: model={model}, file={file_path!r}")
    start = time.monotonic()

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    file_size = path.stat().st_size
    if file_size > 25 * 1024 * 1024:
        raise ValueError(f"Audio file too large: {file_size} bytes (max 25MB)")

    import httpx

    payload: dict[str, Any] = {
        "model": model,
        "response_format": "text",
    }
    if language:
        payload["language"] = language

    file_bytes = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    mime_map = {
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
        "ogg": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
        "mp4": "audio/mp4", "mpeg": "audio/mpeg",
    }
    content_type = mime_map.get(ext, "application/octet-stream")

    async with httpx.AsyncClient(
        base_url=client._base_url,
        headers={"Authorization": f"Bearer {client._api_key}"},
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as http:
        response = await http.post(
            "/audio/transcriptions",
            data=payload,
            files={"file": (path.name, file_bytes, content_type)},
        )

        if response.status_code == 401:
            raise RuntimeError("Invalid API key for audio transcription")
        if response.status_code == 402:
            raise RuntimeError("Insufficient balance for audio transcription")
        if response.status_code == 413:
            raise RuntimeError(f"Audio file too large (max 25MB)")
        response.raise_for_status()

        content_type_resp = response.headers.get("content-type", "")
        if "application/json" in content_type_resp:
            data = response.json()
            text = data.get("text", "")
        else:
            text = response.text.strip()

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Audio transcribed in {elapsed}ms: {len(text)} chars")

    return text


def list_tts_voices(model: str = DEFAULT_TTS_MODEL) -> list[str]:
    logger.debug(f"Entered into list_tts_voices: model={model}")
    info = TTS_MODELS.get(model, {})
    return info.get("voices", [])


def list_audio_models() -> list[dict[str, Any]]:
    logger.debug("Entered into list_audio_models")
    result: list[dict[str, Any]] = []
    for name, info in TTS_MODELS.items():
        result.append({"model": name, "type": "tts", "vendor": info["vendor"], "voices": info["voices"]})
    for name, info in MUSIC_MODELS.items():
        result.append({"model": name, "type": "music", "vendor": info["vendor"], "max_duration": info["max_duration"]})
    return result
