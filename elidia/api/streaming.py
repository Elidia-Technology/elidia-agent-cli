import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class SSEEvent:
    event_type: str  # "content", "usage", "error", "done"
    data: dict | str | None = None


async def parse_sse_stream(response) -> AsyncIterator[SSEEvent]:
    """Parse SSE lines from an httpx streaming response.

    Expected format (OpenAI-compatible):
    data: {"choices": [{"delta": {"content": "Hello"}}]}
    data: {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    data: [DONE]
    """
    logger.debug("Entered into parse_sse_stream")
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    yield SSEEvent(event_type="done")
                    return
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content is not None:
                            yield SSEEvent(event_type="content", data=content)
                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason:
                            usage = data.get("usage")
                            if usage:
                                yield SSEEvent(event_type="usage", data=usage)
                    elif "usage" in data:
                        yield SSEEvent(event_type="usage", data=data["usage"])
                    elif "error" in data:
                        yield SSEEvent(event_type="error", data=data["error"])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SSE data: {data_str[:100]}")
