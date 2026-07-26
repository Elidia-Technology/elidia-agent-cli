"""Browser automation — headless Chromium via Playwright.

One browser context is kept alive per REPL session (not a fresh browser
per call — startup cost is real, ~1-2s per launch) and torn down on
/new or CLI exit. Read-only actions (navigate, extract text/links,
screenshot) are lower risk than interactive ones (click, type) that can
trigger real side effects (form submission, purchases) — see
permissions/manager.py ACTION_TIERS for the tier split.
"""
from __future__ import annotations

import base64
import logging

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 8000
NAVIGATION_TIMEOUT_MS = 30_000


class BrowserSession:
    """Lazily-launched, session-scoped Chromium instance shared across tool calls."""

    def __init__(self) -> None:
        logger.debug("Entered into BrowserSession.__init__")
        self._playwright = None
        self._browser = None
        self._page = None

    async def _ensure_page(self):
        logger.debug("Entered into BrowserSession._ensure_page")
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(
                "Browser tools require Playwright: pip install elidia-agent-cli[pdf] "
                "then `playwright install chromium`"
            ) from e

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
        return self._page

    async def close(self) -> None:
        logger.debug("Entered into BrowserSession.close")
        try:
            if self._page:
                await self._page.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning(f"Entered into BrowserSession.close: cleanup error (non-fatal): {e}")
        finally:
            self._page = None
            self._browser = None
            self._playwright = None


# Module-level singleton — mirrors how DaemonManager/MemoryStore are held as
# one instance per REPL rather than re-created per tool call.
_session: BrowserSession | None = None


def _get_session() -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession()
    return _session


async def close_browser_session() -> None:
    """Call on /new or CLI exit to release the Chromium process."""
    logger.debug("Entered into close_browser_session")
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def _browser_navigate(url: str) -> ToolResult:
    logger.debug(f"Entered into _browser_navigate: url={url}")
    try:
        page = await _get_session()._ensure_page()
        await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        text = await page.inner_text("body")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + f"\n... (truncated, {len(text) - MAX_TEXT_CHARS} more chars)"
        return ToolResult(
            content=f"# {title}\nURL: {page.url}\n\n{text}",
            metadata={"title": title, "url": page.url},
        )
    except Exception as e:
        return ToolResult(content=f"Navigation to {url} failed: {e}", is_error=True)


async def _browser_click(selector: str) -> ToolResult:
    logger.debug(f"Entered into _browser_click: selector={selector}")
    try:
        page = await _get_session()._ensure_page()
        await page.click(selector)
        return ToolResult(content=f"Clicked: {selector}", metadata={"url": page.url})
    except Exception as e:
        return ToolResult(content=f"Click on '{selector}' failed: {e}", is_error=True)


async def _browser_type(selector: str, text: str) -> ToolResult:
    logger.debug(f"Entered into _browser_type: selector={selector}, text_len={len(text)}")
    try:
        page = await _get_session()._ensure_page()
        await page.fill(selector, text)
        return ToolResult(content=f"Typed into {selector}: {text[:100]}")
    except Exception as e:
        return ToolResult(content=f"Type into '{selector}' failed: {e}", is_error=True)


async def _browser_screenshot() -> ToolResult:
    logger.debug("Entered into _browser_screenshot")
    try:
        page = await _get_session()._ensure_page()
        png_bytes = await page.screenshot()
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return ToolResult(
            content=f"Screenshot captured ({len(png_bytes)} bytes)",
            metadata={"image_base64": b64, "content_type": "image/png"},
        )
    except Exception as e:
        return ToolResult(content=f"Screenshot failed: {e}", is_error=True)


async def _browser_extract_links() -> ToolResult:
    logger.debug("Entered into _browser_extract_links")
    try:
        page = await _get_session()._ensure_page()
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: e.innerText.trim(), href: e.href})).filter(l => l.text)",
        )
        if not links:
            return ToolResult(content="No links found on the current page.")
        lines = [f"{link['text']}: {link['href']}" for link in links[:200]]
        extra = f"\n... ({len(links) - 200} more links)" if len(links) > 200 else ""
        return ToolResult(content="\n".join(lines) + extra, metadata={"count": len(links)})
    except Exception as e:
        return ToolResult(content=f"Link extraction failed: {e}", is_error=True)


def register_browser_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_browser_tools")

    registry.register(ToolDefinition(
        name="browser_navigate", description="Load a URL in a real browser (JS-rendered) and return its visible text",
        parameters={"type": "object", "properties": {
            "url": {"type": "string", "description": "URL to navigate to"},
        }, "required": ["url"]},
        handler=_browser_navigate, category="browser",
    ))
    registry.register(ToolDefinition(
        name="browser_click", description="Click an element on the current page by CSS selector",
        parameters={"type": "object", "properties": {
            "selector": {"type": "string", "description": "CSS selector of the element to click"},
        }, "required": ["selector"]},
        handler=_browser_click, category="browser",
    ))
    registry.register(ToolDefinition(
        name="browser_type", description="Type text into an input/textarea on the current page",
        parameters={"type": "object", "properties": {
            "selector": {"type": "string", "description": "CSS selector of the input element"},
            "text": {"type": "string", "description": "Text to type"},
        }, "required": ["selector", "text"]},
        handler=_browser_type, category="browser",
    ))
    registry.register(ToolDefinition(
        name="browser_screenshot", description="Capture a screenshot of the current page",
        parameters={"type": "object", "properties": {}},
        handler=_browser_screenshot, category="browser",
    ))
    registry.register(ToolDefinition(
        name="browser_extract_links", description="List all links on the current page",
        parameters={"type": "object", "properties": {}},
        handler=_browser_extract_links, category="browser",
    ))
