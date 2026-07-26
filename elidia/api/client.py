import asyncio
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from elidia.api.streaming import SSEEvent, parse_sse_stream

logger = logging.getLogger(__name__)

# Images this large or larger get resized server-side on upload, but there's
# no point sending something absurd over the wire first — same cap as the
# gateway's MAX_UPLOAD_BYTES (developer/common/media_storage.py).
MAX_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@dataclass
class ChatMessage:
    role: str  # "user", "assistant", "system"
    # str for plain text, or a list of OpenAI-style content blocks for a
    # multimodal (vision) message — see AiUtilsClient.upload_image().
    content: str | list[dict]


def extract_text(content: str | list[dict]) -> str:
    """Get the plain-text portion of a message's content.

    Code that needs to reason about the message as text (mode classification,
    routing, token-count estimation, session titles, caching keys) can't
    operate on a list of multimodal content blocks — this pulls out just the
    text block(s), joined, ignoring any image_url blocks. Plain string
    content passes through unchanged.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    return " ".join(
        block.get("text", "") for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


@dataclass
class ChatResponse:
    content: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_dt: float = 0.0
    finish_reason: str = ""
    elapsed_ms: int = 0


class AiUtilsClient:
    """Client for the AiUtils Developer API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://developer.aiutils.io/v1",
        timeout: int = 120,
        max_retries: int = 3,
    ):
        logger.debug(f"Entered into AiUtilsClient.__init__: base_url={base_url}")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        logger.debug("Entered into _get_client")
        if self._client is None or self._client.is_closed:
            pool_limits = httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "elidia-cli/0.1.0",
                },
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                limits=pool_limits,
                http2=True,
            )
        return self._client

    async def close(self) -> None:
        logger.debug("Entered into AiUtilsClient.close")
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def upload_image(self, file_path: str | Path) -> str:
        """Upload a local image to the gateway for use as vision input.

        Server-side resizes if needed. Returns the resulting CDN URL to
        reference in a message's content as an image_url block.

        Raises:
            ValueError: unreadable file, unsupported type, or too large.
            httpx.HTTPStatusError: upload rejected by the gateway.
        """
        path = Path(file_path)
        logger.debug(f"Entered into upload_image: path={path}")
        if not path.is_file():
            raise ValueError(f"Not a file: {path}")

        content_type, _ = mimetypes.guess_type(str(path))
        if content_type not in IMAGE_MIME_TYPES:
            raise ValueError(
                f"Unsupported image type '{content_type}' for {path.name}. "
                f"Supported: {', '.join(sorted(IMAGE_MIME_TYPES))}"
            )

        size = path.stat().st_size
        if size > MAX_IMAGE_UPLOAD_BYTES:
            raise ValueError(f"Image too large: {size} bytes (max {MAX_IMAGE_UPLOAD_BYTES})")

        data = path.read_bytes()
        # Deliberately NOT using the shared client here: it carries a default
        # Content-Type: application/json header (for the JSON chat
        # endpoints), and httpx merges rather than replaces client-level
        # headers on a per-request basis — passing `files=` does not make it
        # drop that default, so form parsing on the server breaks. A
        # dedicated client with only auth headers lets httpx compute the
        # correct multipart/form-data boundary itself.
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}", "User-Agent": "elidia-cli/0.1.0"},
            timeout=self._timeout,
        ) as upload_client:
            resp = await upload_client.post(
                "/files",
                files={"file": (path.name, data, content_type)},
            )
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Image uploaded: {path.name} -> {result.get('url')} ({result.get('dt_consumed', 0)} DT)")
        return result["url"]

    async def chat_completion_stream(
        self,
        messages: list[ChatMessage],
        model: str = "deepseek-v4-flash",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Stream a chat completion from the AiUtils API."""
        logger.debug(f"Entered into chat_completion_stream: model={model}, messages={len(messages)}")

        client = await self._get_client()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        last_error: object = None
        for attempt in range(self._max_retries):
            try:
                async with client.stream("POST", "/chat/completions", json=payload) as response:
                    if response.status_code == 401:
                        yield SSEEvent(
                            event_type="error",
                            data={"message": "Invalid API key. Run 'elidia auth login' to set a valid key."},
                        )
                        return
                    if response.status_code == 402:
                        yield SSEEvent(
                            event_type="error",
                            data={"message": "Insufficient balance. Top up at developer.aiutils.io"},
                        )
                        return
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("retry-after", 2 ** attempt))
                        last_error = "rate limited (429)"
                        logger.warning(f"Rate limited, retrying in {retry_after}s (attempt {attempt + 1})")
                        await asyncio.sleep(retry_after)
                        continue
                    if response.status_code >= 500:
                        last_error = f"server error ({response.status_code})"
                        logger.warning(f"Server error {response.status_code}, retrying (attempt {attempt + 1})")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" in content_type:
                        async for event in parse_sse_stream(response):
                            yield event
                    else:
                        # Gateway returns non-streaming JSON — parse it as a single response
                        body = await response.aread()
                        async for event in _parse_json_response(body):
                            yield event
                    return
            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP error {e.response.status_code} from chat_completion_stream: {e}")
                yield SSEEvent(
                    event_type="error",
                    data={"message": f"API error {e.response.status_code}: {e}"},
                )
                return
            except httpx.ConnectError as e:
                last_error = e
                logger.warning(f"Connection failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2 ** attempt)
            except httpx.ReadTimeout as e:
                last_error = e
                logger.warning(f"Read timeout (attempt {attempt + 1})")
                await asyncio.sleep(1)

        yield SSEEvent(event_type="error", data={"message": f"Failed after {self._max_retries} attempts: {last_error}"})

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        model: str = "deepseek-v4-flash",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Non-streaming chat completion (collects full response)."""
        logger.debug(f"Entered into chat_completion: model={model}")
        start = time.monotonic()
        result = ChatResponse(model=model)
        async for event in self.chat_completion_stream(messages, model, temperature, max_tokens):
            if event.event_type == "content":
                result.content += event.data
            elif event.event_type == "usage" and isinstance(event.data, dict):
                result.tokens_in = event.data.get("prompt_tokens", 0)
                result.tokens_out = event.data.get("completion_tokens", 0)
            elif event.event_type == "error":
                msg = event.data.get("message", str(event.data)) if isinstance(event.data, dict) else str(event.data)
                raise RuntimeError(msg)
        result.elapsed_ms = int((time.monotonic() - start) * 1000)
        return result

    async def list_models(self) -> list[dict]:
        """Fetch available models from the API."""
        logger.debug("Entered into list_models")
        client = await self._get_client()
        response = await client.get("/models")
        response.raise_for_status()
        data = response.json()
        return data.get("data", data.get("models", []))

    async def get_balance(self) -> dict:
        """Fetch current DT balance."""
        logger.debug("Entered into get_balance")
        client = await self._get_client()
        try:
            response = await client.get("/wallet/balance")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch balance: {e}")
            return {"balance_dt": -1, "error": str(e)}


async def _parse_json_response(body: bytes) -> AsyncIterator[SSEEvent]:
    """Parse a non-streaming JSON chat completion response into SSEEvents."""
    logger.debug("Entered into _parse_json_response")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        yield SSEEvent(event_type="error", data={"message": f"Invalid JSON response: {body[:200].decode()}"})
        return

    if "error" in data:
        yield SSEEvent(event_type="error", data=data["error"])
        return

    choices = data.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if content:
            yield SSEEvent(event_type="content", data=content)

    usage = data.get("usage")
    if usage:
        yield SSEEvent(event_type="usage", data=usage)

    yield SSEEvent(event_type="done")
