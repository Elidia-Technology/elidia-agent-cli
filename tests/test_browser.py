"""Tests for elidia.tools.browser — headless browser automation."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elidia.tools import ToolRegistry, create_default_registry
from elidia.tools.browser import (
    BrowserSession,
    _browser_click,
    _browser_extract_links,
    _browser_navigate,
    _browser_screenshot,
    _browser_type,
    close_browser_session,
    register_browser_tools,
)


def _mock_page():
    page = AsyncMock()
    page.title = AsyncMock(return_value="Example Page")
    page.inner_text = AsyncMock(return_value="Hello from the page body.")
    page.url = "https://example.com/"
    page.goto = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    page.eval_on_selector_all = AsyncMock(return_value=[
        {"text": "Docs", "href": "https://example.com/docs"},
        {"text": "About", "href": "https://example.com/about"},
    ])
    return page


class TestRegistration:
    def test_registers_five_tools(self):
        registry = ToolRegistry()
        register_browser_tools(registry)
        names = {t.name for t in registry.list_tools()}
        assert names == {
            "browser_navigate", "browser_click", "browser_type",
            "browser_screenshot", "browser_extract_links",
        }

    def test_wired_into_default_registry(self):
        registry = create_default_registry()
        assert registry.get("browser_navigate") is not None


class TestBrowserNavigate:
    @pytest.mark.asyncio
    async def test_navigate_success(self):
        page = _mock_page()
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_navigate("https://example.com")
        assert not result.is_error
        assert "Example Page" in result.content
        assert "Hello from the page body." in result.content
        assert result.metadata["url"] == "https://example.com/"
        page.goto.assert_awaited_once_with("https://example.com", wait_until="domcontentloaded")

    @pytest.mark.asyncio
    async def test_navigate_truncates_long_text(self):
        page = _mock_page()
        page.inner_text = AsyncMock(return_value="x" * 20_000)
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_navigate("https://example.com")
        assert "truncated" in result.content

    @pytest.mark.asyncio
    async def test_navigate_failure_returns_error_result(self):
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(side_effect=RuntimeError("boom"))
            result = await _browser_navigate("https://bad.example")
        assert result.is_error
        assert "boom" in result.content


class TestBrowserInteract:
    @pytest.mark.asyncio
    async def test_click_success(self):
        page = _mock_page()
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_click("#submit")
        assert not result.is_error
        page.click.assert_awaited_once_with("#submit")

    @pytest.mark.asyncio
    async def test_type_success(self):
        page = _mock_page()
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_type("#search", "hello world")
        assert not result.is_error
        page.fill.assert_awaited_once_with("#search", "hello world")

    @pytest.mark.asyncio
    async def test_click_missing_selector_is_error(self):
        page = _mock_page()
        page.click = AsyncMock(side_effect=Exception("selector not found"))
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_click("#nope")
        assert result.is_error


class TestBrowserScreenshotAndLinks:
    @pytest.mark.asyncio
    async def test_screenshot_returns_base64(self):
        page = _mock_page()
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_screenshot()
        assert not result.is_error
        assert result.metadata["content_type"] == "image/png"
        assert len(result.metadata["image_base64"]) > 0

    @pytest.mark.asyncio
    async def test_extract_links(self):
        page = _mock_page()
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_extract_links()
        assert not result.is_error
        assert "Docs: https://example.com/docs" in result.content
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_extract_links_empty(self):
        page = _mock_page()
        page.eval_on_selector_all = AsyncMock(return_value=[])
        with patch("elidia.tools.browser._get_session") as get_session:
            get_session.return_value._ensure_page = AsyncMock(return_value=page)
            result = await _browser_extract_links()
        assert "No links found" in result.content


class TestBrowserSessionLifecycle:
    @pytest.mark.asyncio
    async def test_ensure_page_missing_playwright_raises_clear_error(self):
        session = BrowserSession()
        with patch.dict("sys.modules", {"playwright.async_api": None}):
            with pytest.raises(RuntimeError, match="Playwright"):
                await session._ensure_page()

    @pytest.mark.asyncio
    async def test_close_is_safe_when_never_started(self):
        session = BrowserSession()
        await session.close()  # must not raise

    @pytest.mark.asyncio
    async def test_close_browser_session_module_singleton(self):
        import elidia.tools.browser as browser_mod
        fake_session = MagicMock()
        fake_session.close = AsyncMock()
        browser_mod._session = fake_session
        await close_browser_session()
        fake_session.close.assert_awaited_once()
        assert browser_mod._session is None
