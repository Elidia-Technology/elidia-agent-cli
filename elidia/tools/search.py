import html
import logging
import re
from urllib.parse import quote_plus

import httpx

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


async def _web_search(query: str, max_results: int = 5) -> ToolResult:
    logger.debug(f"Entered into _web_search: query={query!r}, max_results={max_results}")

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

        body = resp.text
        results = _parse_ddg_html(body, max_results)

        if not results:
            return ToolResult(content=f"No results found for: {query}")

        lines: list[str] = []
        for i, (title, snippet, link) in enumerate(results, 1):
            lines.append(f"{i}. {title}")
            lines.append(f"   {link}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")

        return ToolResult(
            content="\n".join(lines).strip(),
            metadata={"result_count": len(results)},
        )
    except httpx.TimeoutException:
        return ToolResult(content="Search request timed out", is_error=True)
    except Exception as e:
        return ToolResult(content=f"Search error: {e}", is_error=True)


def _parse_ddg_html(body: str, max_results: int) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []

    result_blocks = re.findall(
        r'class="result__body".*?</div>\s*</div>',
        body,
        re.DOTALL,
    )

    for block in result_blocks[:max_results]:
        title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        title = _strip_html(title_match.group(1)) if title_match else ""

        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</[at]>', block, re.DOTALL)
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""

        href_match = re.search(r'class="result__url"[^>]*href="([^"]*)"', block)
        if not href_match:
            href_match = re.search(r'class="result__a"[^>]*href="([^"]*)"', block)
        link = html.unescape(href_match.group(1).strip()) if href_match else ""

        if title:
            results.append((title, snippet, link))

    return results


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return " ".join(text.split())


def register_search_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_search_tools")

    registry.register(ToolDefinition(
        name="web_search",
        description="Search the web using DuckDuckGo",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum results to return", "default": 5},
            },
            "required": ["query"],
        },
        handler=_web_search,
        category="search",
    ))
