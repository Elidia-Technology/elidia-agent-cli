"""Auto-pager — pages long output through the terminal pager.

When output exceeds the terminal height, it is automatically routed
through Rich's built-in pager (which uses the system's PAGER env var,
defaulting to 'less -R' on Unix or 'more' on Windows).

The pager is only engaged for non-interactive, non-streaming content.
Streaming responses are always rendered directly.
"""
from __future__ import annotations

import logging
import os
import shutil

from rich.console import Console
from rich.markdown import Markdown

logger = logging.getLogger(__name__)

DEFAULT_PAGER_THRESHOLD = 0.8


class AutoPager:
    """Automatically pages output when it exceeds terminal height."""

    def __init__(
        self,
        console: Console | None = None,
        threshold: float = DEFAULT_PAGER_THRESHOLD,
        enabled: bool = True,
    ) -> None:
        logger.debug(f"Entered into AutoPager.__init__: threshold={threshold}, enabled={enabled}")
        self._console = console or Console()
        self._threshold = threshold
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def threshold(self) -> float:
        return self._threshold

    def get_terminal_height(self) -> int:
        logger.debug("Entered into get_terminal_height")
        size = shutil.get_terminal_size(fallback=(80, 24))
        return size.lines

    def should_page(self, content: str) -> bool:
        logger.debug("Entered into should_page")
        if not self._enabled:
            return False
        if not content:
            return False

        term_height = self.get_terminal_height()
        content_lines = content.count("\n") + 1
        threshold_lines = int(term_height * self._threshold)

        return content_lines > threshold_lines

    def print_or_page(self, content: str, as_markdown: bool = True) -> None:
        logger.debug(f"Entered into print_or_page: len={len(content)}, md={as_markdown}")
        if self.should_page(content):
            self._page(content, as_markdown)
        else:
            self._print(content, as_markdown)

    def _print(self, content: str, as_markdown: bool) -> None:
        logger.debug("Entered into _print")
        if as_markdown:
            try:
                self._console.print(Markdown(content))
            except Exception:
                self._console.print(content)
        else:
            self._console.print(content)

    def _page(self, content: str, as_markdown: bool) -> None:
        logger.debug("Entered into _page")
        try:
            with self._console.pager(styles=as_markdown):
                if as_markdown:
                    self._console.print(Markdown(content))
                else:
                    self._console.print(content)
        except Exception as e:
            logger.warning(f"Pager failed, falling back to direct print: {e}")
            self._print(content, as_markdown)

    def page_lines(self, lines: list[str]) -> None:
        logger.debug(f"Entered into page_lines: count={len(lines)}")
        content = "\n".join(lines)
        self.print_or_page(content, as_markdown=False)

    def get_pager_command(self) -> str:
        logger.debug("Entered into get_pager_command")
        return os.environ.get("PAGER", "less -R")
