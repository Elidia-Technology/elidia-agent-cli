import logging
from urllib.parse import urlparse

import httpx

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 512_000
DEFAULT_TIMEOUT = 30


async def _http_fetch(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> ToolResult:
    logger.debug(f"Entered into _http_fetch: url={url}, method={method}")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ToolResult(content=f"Unsupported scheme: {parsed.scheme}", is_error=True)
    if not parsed.hostname:
        return ToolResult(content="Invalid URL: no hostname", is_error=True)

    req_headers = {
        "User-Agent": "Elidia/0.1 (https://aiutils.io)",
        "Accept": "text/html, application/json, */*",
    }
    if headers:
        req_headers.update(headers)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.request(
                method.upper(),
                url,
                headers=req_headers,
                content=body.encode() if body else None,
            )

        content_type = resp.headers.get("content-type", "")
        body_bytes = resp.content[:MAX_BODY_BYTES]
        body_text = body_bytes.decode(errors="replace")

        parts: list[str] = [
            f"HTTP {resp.status_code}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(resp.content)}",
            "",
            body_text,
        ]

        if len(resp.content) > MAX_BODY_BYTES:
            parts.append(f"\n... (truncated, {len(resp.content) - MAX_BODY_BYTES} bytes omitted)")

        return ToolResult(
            content="\n".join(parts),
            is_error=resp.status_code >= 400,
            metadata={
                "status_code": resp.status_code,
                "content_type": content_type,
                "content_length": len(resp.content),
            },
        )
    except httpx.TimeoutException:
        return ToolResult(content=f"Request timed out after {timeout}s", is_error=True)
    except httpx.TooManyRedirects:
        return ToolResult(content="Too many redirects (max 5)", is_error=True)
    except Exception as e:
        return ToolResult(content=f"Fetch error: {e}", is_error=True)


def register_fetch_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_fetch_tools")

    registry.register(ToolDefinition(
        name="http_fetch",
        description="Fetch a URL and return its content",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "method": {"type": "string", "description": "HTTP method", "default": "GET", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
                "headers": {"type": "object", "description": "Additional HTTP headers", "additionalProperties": {"type": "string"}},
                "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": DEFAULT_TIMEOUT},
            },
            "required": ["url"],
        },
        handler=_http_fetch,
        category="fetch",
    ))
