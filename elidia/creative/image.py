"""Image generation via AiUtils Developer API.

Supports multiple models (FLUX, DALL-E, Stable Diffusion) through the
unified /v1/images/generations endpoint. Saves generated images to disk
and optionally displays them inline in supported terminals.
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

IMAGE_MODELS = {
    "flux-1.1-pro": {"vendor": "bfl", "supports_size": True, "max_size": 2048},
    "flux-schnell": {"vendor": "bfl", "supports_size": True, "max_size": 1024},
    "dall-e-3": {"vendor": "openai", "supports_size": True, "max_size": 1792},
    "dall-e-2": {"vendor": "openai", "supports_size": True, "max_size": 1024},
    "sd-3.5-large": {"vendor": "stability", "supports_size": True, "max_size": 1024},
    "sdxl-turbo": {"vendor": "stability", "supports_size": True, "max_size": 1024},
}

DEFAULT_MODEL = "flux-schnell"
DEFAULT_OUTPUT_DIR = Path.home() / ".elidia" / "generated" / "images"


@dataclass
class ImageResult:
    file_path: str
    model: str
    prompt: str
    width: int
    height: int
    format: str = "png"
    size_bytes: int = 0
    elapsed_ms: int = 0
    base64_data: str = ""


async def generate_image(
    client: AiUtilsClient,
    prompt: str,
    model: str = DEFAULT_MODEL,
    width: int = 1024,
    height: int = 1024,
    output_dir: Path | None = None,
) -> ImageResult:
    logger.debug(f"Entered into generate_image: model={model}, prompt={prompt[:60]!r}")
    start = time.monotonic()

    save_dir = output_dir or DEFAULT_OUTPUT_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    model_info = IMAGE_MODELS.get(model, IMAGE_MODELS[DEFAULT_MODEL])
    max_size = model_info["max_size"]
    width = min(width, max_size)
    height = min(height, max_size)

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }

    if model_info["supports_size"]:
        payload["size"] = f"{width}x{height}"

    async with httpx.AsyncClient(
        base_url=client._base_url,
        headers={
            "Authorization": f"Bearer {client._api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as http:
        response = await http.post("/images/generations", json=payload)

        if response.status_code == 401:
            raise RuntimeError("Invalid API key for image generation")
        if response.status_code == 402:
            raise RuntimeError("Insufficient balance for image generation")
        if response.status_code == 429:
            raise RuntimeError("Rate limited — try again in a moment")
        response.raise_for_status()

        data = response.json()

    images = data.get("data", [])
    if not images:
        raise RuntimeError("No image returned from API")

    image_data = images[0]
    b64 = image_data.get("b64_json", "")
    if not b64:
        url = image_data.get("url", "")
        if url:
            async with httpx.AsyncClient(timeout=30.0) as dl:
                img_resp = await dl.get(url)
                img_resp.raise_for_status()
                img_bytes = img_resp.content
        else:
            raise RuntimeError("No image data or URL in response")
    else:
        img_bytes = base64.b64decode(b64)

    timestamp = int(time.time())
    safe_prompt = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt[:40]).strip().replace(" ", "_")
    filename = f"{timestamp}_{safe_prompt}.png"
    file_path = save_dir / filename
    file_path.write_bytes(img_bytes)

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Image generated: {file_path} ({len(img_bytes)} bytes, {elapsed}ms)")

    return ImageResult(
        file_path=str(file_path),
        model=model,
        prompt=prompt,
        width=width,
        height=height,
        format="png",
        size_bytes=len(img_bytes),
        elapsed_ms=elapsed,
        base64_data=b64 if b64 else base64.b64encode(img_bytes).decode("ascii"),
    )


def list_image_models() -> list[dict[str, Any]]:
    logger.debug("Entered into list_image_models")
    return [
        {"model": name, "vendor": info["vendor"], "max_size": info["max_size"]}
        for name, info in IMAGE_MODELS.items()
    ]
