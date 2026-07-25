"""Response renderer — Markdown, code, tables, diffs, syntax highlighting.

Provides a ResponseRenderer class that handles all output formatting for
the CLI REPL. Extracted from inline rendering in repl.py for reuse and
testability.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

logger = logging.getLogger(__name__)


class ResponseRenderer:
    """Renders AI responses with full Markdown, code, and media support."""

    def __init__(self, console: Console) -> None:
        logger.debug("Entered into ResponseRenderer.__init__")
        self._console = console
        self._editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))

    def render_response(self, content: str) -> None:
        """Render a full response, auto-detecting content type."""
        logger.debug(f"Entered into render_response: len={len(content)}")
        try:
            self._console.print(Markdown(content))
        except Exception:
            self._console.print(content)

    def render_code_block(self, code: str, language: str = "", line_numbers: bool = True) -> None:
        """Render a syntax-highlighted code block."""
        logger.debug(f"Entered into render_code_block: lang={language}, len={len(code)}")
        lang = language or _detect_language(code)
        try:
            syntax = Syntax(
                code, lang, theme="monokai", line_numbers=line_numbers,
                word_wrap=False,
            )
            self._console.print(syntax)
        except Exception:
            self._console.print(Panel(code, title=language or "code"))

    def render_table(self, headers: list[str], rows: list[list[str]], title: str = "") -> None:
        """Render a Rich-styled table."""
        logger.debug(f"Entered into render_table: cols={len(headers)}, rows={len(rows)}")
        table = Table(title=title or None, border_style="dim", show_header=True)
        for h in headers:
            table.add_column(h, style="bold")
        for row in rows:
            table.add_row(*[str(c) for c in row])
        self._console.print(table)

    def render_diff(self, old_content: str, new_content: str,
                    old_label: str = "old", new_label: str = "new") -> None:
        """Render a unified diff between two strings."""
        logger.debug(f"Entered into render_diff: old_len={len(old_content)}, new_len={len(new_content)}")
        import difflib
        diff = difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=old_label, tofile=new_label,
        )
        diff_text = "".join(diff)
        if not diff_text.strip():
            self._console.print("[dim](no differences)[/dim]")
            return
        try:
            syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
            self._console.print(syntax)
        except Exception:
            self._console.print(diff_text)

    def render_file_paths(self, content: str) -> str:
        """Detect file paths in content and make them clickable.

        Returns markdown with file:// links for paths that resolve to real files.
        """
        logger.debug(f"Entered into render_file_paths: len={len(content)}")
        pattern = r'(?:^|\s)(/[^\s:]+?\.[a-zA-Z]{1,6})'
        def _replace(m: re.Match) -> str:
            path_str = m.group(1)
            path = Path(path_str)
            if path.exists():
                return f" [{path_str}](file://{path_str})"
            return m.group(0)
        return re.sub(pattern, _replace, content)

    def render_image(self, file_path: str) -> bool:
        """Display an image in the terminal using the best available protocol.

        Returns True if the image was displayed, False if it was saved to disk only.
        """
        logger.debug(f"Entered into render_image: path={file_path}")
        path = Path(file_path)
        if not path.exists():
            self._console.print(f"[yellow]Image not found: {path}[/yellow]")
            return False

        term = os.environ.get("TERM", "").lower()
        term_program = os.environ.get("TERM_PROGRAM", "").lower()

        if "iterm" in term_program or "iterm" in term or os.environ.get("ITERM_SESSION_ID"):
            return self._render_iterm2(path)
        if "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
            return self._render_kitty(path)
        if os.environ.get("TMUX") and _check_sixel_capable():
            return self._render_sixel(path)

        self._console.print(f"[dim]Image saved: {path}[/dim]")
        return False

    # --- internal helpers ---

    def _render_iterm2(self, path: Path) -> bool:
        try:
            data = path.read_bytes()
            encoded = _b64_encode_chunked(data)
            name_b64 = _b64_encode_chunked(path.name.encode())
            self._console.print(
                f"\033]1337;File=inline=1;name={name_b64};size={len(data)}:{encoded}\033\\"
            )
            return True
        except Exception as e:
            logger.debug(f"iTerm2 image display failed: {e}")
            return False

    def _render_kitty(self, path: Path) -> bool:
        try:
            import struct
            data = path.read_bytes()
            encoded = _b64_encode_chunked(data)
            payload = f"\033_Ga=T,f=100,s={len(data)},v={len(data)};{encoded}\033\\"
            self._console.print(payload)
            return True
        except Exception as e:
            logger.debug(f"Kitty image display failed: {e}")
            return False

    def _render_sixel(self, path: Path) -> bool:
        try:
            result = subprocess.run(
                ["convert", str(path), "sixel:-"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                self._console.print(result.stdout.decode("utf-8", errors="replace"))
                return True
        except Exception as e:
            logger.debug(f"Sixel conversion failed: {e}")
        return False


# Standalone helpers (kept for backward compatibility)

def render_error(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_error: {message}")
    console.print(f"[red]x[/red] {message}")


def render_success(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_success: {message}")
    console.print(f"[green]v[/green] {message}")


def render_warning(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_warning: {message}")
    console.print(f"[yellow]![/yellow] {message}")


def render_info(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_info: {message}")
    console.print(f"[blue]i[/blue] {message}")


def render_status_bar(console: Console, model: str, mode: str, tokens: int, cost_dt: float) -> None:
    logger.debug(f"Entered into render_status_bar: model={model}, mode={mode}")
    console.print(
        f"[dim]{model} | {mode} | {tokens:,} tokens | {cost_dt:.1f} DT[/dim]"
    )


# --- internal helpers ---

_CODE_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".rs": "rust",
    ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp", ".h": "c",
    ".rb": "ruby", ".sh": "bash", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".html": "html", ".css": "css",
    ".xml": "xml", ".md": "markdown",
}


def _detect_language(code: str) -> str:
    """Heuristic language detection for syntax highlighting."""
    if code.strip().startswith("def ") or code.strip().startswith("import "):
        return "python"
    if code.strip().startswith("function ") or code.strip().startswith("const "):
        return "javascript"
    return "text"


def _b64_encode_chunked(data: bytes, chunk_size: int = 4096) -> str:
    """Base64 encode in chunks to avoid memory issues with large images."""
    import base64
    encoded = base64.b64encode(data).decode("ascii")
    chunks = [encoded[i:i + chunk_size] for i in range(0, len(encoded), chunk_size)]
    return "".join(chunks)


def _check_sixel_capable() -> bool:
    """Check if the terminal supports Sixel graphics."""
    try:
        result = subprocess.run(
            ["which", "convert"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
