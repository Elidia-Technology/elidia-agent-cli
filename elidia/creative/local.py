"""Local creative generation — runs models on the user's machine.

Uses diffusers for image generation (SD-turbo, SDXL-lightning) when
available. Falls back gracefully when dependencies are not installed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path.home() / ".elidia" / "generated" / "local"

LOCAL_IMAGE_MODELS = {
    "sd-turbo": {
        "repo": "stabilityai/sd-turbo",
        "steps": 4,
        "guidance": 0.0,
        "description": "Fast 4-step image generation (512x512)",
    },
    "sdxl-lightning-4step": {
        "repo": "ByteDance/SDXL-Lightning",
        "steps": 4,
        "guidance": 0.0,
        "description": "Fast SDXL variant (1024x1024)",
    },
}


@dataclass
class LocalImageResult:
    file_path: str
    model: str
    prompt: str
    width: int
    height: int
    steps: int
    elapsed_ms: int = 0
    size_bytes: int = 0


def check_local_available() -> dict[str, bool]:
    logger.debug("Entered into check_local_available")
    status: dict[str, bool] = {}

    try:
        import torch  # noqa: F401
        status["torch"] = True
    except ImportError:
        status["torch"] = False

    try:
        import diffusers  # noqa: F401
        status["diffusers"] = True
    except ImportError:
        status["diffusers"] = False

    status["gpu"] = False
    if status["torch"]:
        try:
            import torch
            status["gpu"] = torch.cuda.is_available() or (hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        except Exception:
            pass

    status["ready"] = status["torch"] and status["diffusers"]
    return status


async def generate_local_image(
    prompt: str,
    model: str = "sd-turbo",
    width: int = 512,
    height: int = 512,
    output_dir: Path | None = None,
    num_steps: int | None = None,
) -> LocalImageResult:
    logger.debug(f"Entered into generate_local_image: model={model}, prompt={prompt[:60]!r}")
    start = time.monotonic()

    avail = check_local_available()
    if not avail["ready"]:
        missing = []
        if not avail["torch"]:
            missing.append("torch")
        if not avail["diffusers"]:
            missing.append("diffusers")
        raise RuntimeError(
            f"Local generation requires: {', '.join(missing)}. "
            f"Install with: pip install {' '.join(missing)}"
        )

    model_info = LOCAL_IMAGE_MODELS.get(model)
    if not model_info:
        raise RuntimeError(f"Unknown local model: {model}. Available: {list(LOCAL_IMAGE_MODELS.keys())}")

    steps = num_steps or model_info["steps"]
    guidance = model_info["guidance"]

    save_dir = output_dir or DEFAULT_OUTPUT_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    import torch
    from diffusers import AutoPipelineForText2Image

    device = "cuda" if torch.cuda.is_available() else "mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else "cpu"
    dtype = torch.float16 if device != "cpu" else torch.float32

    logger.info(f"Loading {model_info['repo']} on {device} ({dtype})")
    pipe = AutoPipelineForText2Image.from_pretrained(
        model_info["repo"],
        torch_dtype=dtype,
        variant="fp16" if dtype == torch.float16 else None,
    )
    pipe = pipe.to(device)

    image = pipe(
        prompt=prompt,
        num_inference_steps=steps,
        guidance_scale=guidance,
        width=width,
        height=height,
    ).images[0]

    timestamp = int(time.time())
    safe_prompt = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt[:30]).strip().replace(" ", "_")
    filename = f"{timestamp}_local_{safe_prompt}.png"
    file_path = save_dir / filename
    image.save(str(file_path))

    size_bytes = file_path.stat().st_size
    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(f"Local image generated: {file_path} ({size_bytes} bytes, {elapsed}ms)")

    return LocalImageResult(
        file_path=str(file_path),
        model=model,
        prompt=prompt,
        width=width,
        height=height,
        steps=steps,
        elapsed_ms=elapsed,
        size_bytes=size_bytes,
    )


def list_local_models() -> list[dict[str, Any]]:
    logger.debug("Entered into list_local_models")
    avail = check_local_available()
    return [
        {
            "model": name,
            "repo": info["repo"],
            "description": info["description"],
            "steps": info["steps"],
            "available": avail["ready"],
        }
        for name, info in LOCAL_IMAGE_MODELS.items()
    ]
