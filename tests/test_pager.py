"""Tests for elidia.cli.pager — auto-pager for long output."""
import pytest

from elidia.cli.pager import AutoPager


class TestAutoPager:
    def test_default_enabled(self):
        ap = AutoPager()
        assert ap.enabled

    def test_disable(self):
        ap = AutoPager(enabled=False)
        assert not ap.enabled

    def test_toggle_enabled(self):
        ap = AutoPager()
        ap.enabled = False
        assert not ap.enabled
        ap.enabled = True
        assert ap.enabled

    def test_terminal_height_positive(self):
        ap = AutoPager()
        h = ap.get_terminal_height()
        assert h > 0

    def test_short_content_no_page(self):
        ap = AutoPager(threshold=0.8)
        assert not ap.should_page("short")

    def test_empty_no_page(self):
        ap = AutoPager()
        assert not ap.should_page("")

    def test_long_content_pages(self):
        ap = AutoPager(threshold=0.8)
        h = ap.get_terminal_height()
        long_content = "\n".join(["line"] * (h * 2))
        assert ap.should_page(long_content)

    def test_disabled_never_pages(self):
        ap = AutoPager(enabled=False)
        long_content = "\n".join(["line"] * 500)
        assert not ap.should_page(long_content)

    def test_custom_threshold(self):
        ap = AutoPager(threshold=0.1)
        h = ap.get_terminal_height()
        medium_content = "\n".join(["line"] * (h // 2))
        # With threshold=0.1, even h//2 lines > h*0.1 → should page
        if h // 2 > int(h * 0.1):
            assert ap.should_page(medium_content)

    def test_get_pager_command(self):
        ap = AutoPager()
        cmd = ap.get_pager_command()
        assert isinstance(cmd, str)
        assert len(cmd) > 0
