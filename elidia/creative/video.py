"""Video generation via AiUtils Developer API.

Supports text-to-video and image-to-video through models like Kling and
Minimax. Video generation is asynchronous — submit a job, poll for completion.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VIDEO_MODELS = {
    "kling-v2": {"vendor": "kling", "max_duration": 10, "supports_image": True},
    "kling-v1.6": {"vendor": "kling", "max_duration": 5, "supports_image": True},
    "minimax-video-01": {"vendor": "minimax", "max_duration": 6, "supports_image": False},
}

DEFAULT_MODEL = "kling-v2"
DEFAULT_OUTPUT_DIR = Path.home() / ".elidia" / "generated" / "videos"


@dataclass
class VideoResult:
    file_path: str
    model: str
    prompt: str
    duration: int
    format: str = "mp4"
    size_bytes: int = 0
    elapsed_ms: int = 0
    job_id: str = ""


async def generate_video(
    api_key: str,
    prompt: str,
    model: str = DEFAULT_MODEL,
    duration: int = 5,
    image_path: str | None = None,
    output_dir: Path | None = None,
    base_url: str = "https://developer.aiutils.io/v1",
    poll_interval: float = 5.0,
    max_wait: float = 300.0,
) -> VideoResult:
    logger.debug(f"Entered into generate_video: model={model}, prompt={prompt[:60]!r}")
    start = time.monotonic()

    save_dir = output_dir or DEFAULT_OUTPUT_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    model_info = VIDEO_MODELS.get(model, VIDEO_MODELS[DEFAULT_MODEL])
    duration = min(duration, model_info["max_duration"])

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
    }

    if image_path and model_info["supports_image"]:
        img_bytes = Path(image_path).read_bytes()
        payload["image"] = base64.b64encode(img_bytes).decode("ascii")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.post("/videos/generations", json=payload)

        if response.status_code == 401:
            raise RuntimeError("Invalid API key for video generation")
        if response.status_code == 402:
            raise RuntimeError("Insufficient balance for video generation")
        response.raise_for_status()

        data = response.json()
        job_id = data.get("id", data.get("job_id", ""))

        if not job_id:
            video_url = data.get("url", "")
            if video_url:
                return await _download_and_save(
                    video_url, prompt, model, duration, save_dir, start, job_id
                )
            raise RuntimeError("No job ID or video URL returned")

        logger.info(f"Video job submitted: {job_id}")

        waited = 0.0
        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval

            status_resp = await client.get(f"/videos/generations/{job_id}")
            if status_resp.status_code >= 400:
                logger.warning(f"Poll error {status_resp.status_code} for job {job_id}")
                continue

            status_data = status_resp.json()
            state = status_data.get("status", "").lower()

            if state in ("completed", "succeeded", "done"):
                video_url = status_data.get("url", status_data.get("video_url", ""))
                if not video_url:
                    results = status_data.get("data", status_data.get("results", []))
                    if results and isinstance(results, list):
                        video_url = results[0].get("url", "")

                if video_url:
                    return await _download_and_save(
                        video_url, prompt, model, duration, save_dir, start, job_id
                    )
                raise RuntimeError(f"Job {job_id} completed but no video URL found")

            elif state in ("failed", "error"):
                error = status_data.get("error", status_data.get("message", "unknown error"))
                raise RuntimeError(f"Video generation failed: {error}")

            logger.debug(f"Job {job_id} status: {state}, waited {waited:.0f}s")

        raise RuntimeError(f"Video generation timed out after {max_wait}s (job: {job_id})")


async def _download_and_save(
    url: str,
    prompt: str,
    model: str,
    duration: int,
    save_dir: Path,
    start: float,
    job_id: str,
) -> VideoResult:
    logger.debug(f"Entered into _download_and_save: url={url[:80]}")
    async with httpx.AsyncClient(timeout=60.0) as dl:
        vid_resp = await dl.get(url)
        vid_resp.raise_for_status()
        vid_bytes = vid_resp.content

    timestamp = int(time.time())
    safe_prompt = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt[:30]).strip().replace(" ", "_")
    filename = f"{timestamp}_{safe_prompt}.mp4"
    file_path = save_dir / filename
    file_path.write_bytes(vid_bytes)

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Video saved: {file_path} ({len(vid_bytes)} bytes, {elapsed}ms)")

    return VideoResult(
        file_path=str(file_path),
        model=model,
        prompt=prompt,
        duration=duration,
        format="mp4",
        size_bytes=len(vid_bytes),
        elapsed_ms=elapsed,
        job_id=job_id,
    )


def list_video_models() -> list[dict[str, Any]]:
    logger.debug("Entered into list_video_models")
    return [
        {"model": name, "vendor": info["vendor"], "max_duration": info["max_duration"],
         "supports_image": info["supports_image"]}
        for name, info in VIDEO_MODELS.items()
    ]
