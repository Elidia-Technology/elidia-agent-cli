"""Configurable themes — dark/light/custom color schemes for the CLI.

Themes define colors for all UI elements: prompts, borders, status,
code blocks, errors, etc. Users can select a built-in theme or define
custom themes in ~/.elidia/themes.toml.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.style import Style
from rich.theme import Theme

logger = logging.getLogger(__name__)

_NO_COLOR = os.environ.get("NO_COLOR", "").strip() != ""


def _detect_system_dark_mode() -> bool:
    """Detect whether the OS is in dark mode. Returns False if undetectable."""
    logger.debug("Entered into _detect_system_dark_mode")
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip().lower() == "dark"
        elif system == "Linux":
            gtk_theme = os.environ.get("GTK_THEME", "").lower()
            return "dark" in gtk_theme
        elif system == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except Exception:
        pass
    return False


@dataclass(frozen=True)
class ElidiaTheme:
    name: str
    description: str = ""
    primary: str = "cyan"
    secondary: str = "blue"
    accent: str = "magenta"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"
    dim: str = "dim"
    border: str = "cyan"
    prompt_style: str = "bold cyan"
    assistant_style: str = ""
    code_style: str = "dim"
    heading_style: str = "bold"
    status_style: str = "dim"


BUILTIN_THEMES: dict[str, ElidiaTheme] = {
    "default": ElidiaTheme(
        name="default",
        description="Default Elidia theme",
    ),
    "dark": ElidiaTheme(
        name="dark",
        description="High contrast dark theme",
        primary="bright_cyan",
        secondary="bright_blue",
        accent="bright_magenta",
        border="bright_cyan",
        prompt_style="bold bright_cyan",
    ),
    "light": ElidiaTheme(
        name="light",
        description="Light terminal theme",
        primary="blue",
        secondary="dark_blue",
        accent="dark_magenta",
        success="dark_green",
        warning="dark_orange",
        error="dark_red",
        border="blue",
        prompt_style="bold blue",
    ),
    "minimal": ElidiaTheme(
        name="minimal",
        description="Clean, low-color theme",
        primary="white",
        secondary="white",
        accent="white",
        success="green",
        warning="yellow",
        error="red",
        border="white",
        prompt_style="bold",
        dim="dim white",
    ),
    "ocean": ElidiaTheme(
        name="ocean",
        description="Cool blue-green palette",
        primary="deep_sky_blue1",
        secondary="steel_blue",
        accent="medium_spring_green",
        success="spring_green2",
        warning="gold1",
        error="indian_red1",
        border="deep_sky_blue1",
        prompt_style="bold deep_sky_blue1",
    ),
    "sunset": ElidiaTheme(
        name="sunset",
        description="Warm orange-red palette",
        primary="orange1",
        secondary="dark_orange",
        accent="hot_pink",
        success="chartreuse1",
        warning="gold1",
        error="red1",
        border="orange1",
        prompt_style="bold orange1",
    ),
}


class ThemeManager:
    """Manages theme selection and application."""

    def __init__(self, theme_name: str = "default") -> None:
        logger.debug(f"Entered into ThemeManager.__init__: theme={theme_name}")
        self._no_color = _NO_COLOR
        resolved = self._resolve_theme_name(theme_name)
        self._current_name = resolved
        self._current = BUILTIN_THEMES.get(resolved, BUILTIN_THEMES["default"])
        self._custom_themes: dict[str, ElidiaTheme] = {}

    @staticmethod
    def _resolve_theme_name(name: str) -> str:
        """Resolve 'auto' to 'dark' or 'light' based on OS detection."""
        if name == "auto":
            return "dark" if _detect_system_dark_mode() else "light"
        return name

    @property
    def current(self) -> ElidiaTheme:
        return self._current

    @property
    def current_name(self) -> str:
        return self._current_name

    def set_theme(self, name: str) -> ElidiaTheme | None:
        logger.debug(f"Entered into set_theme: name={name}")
        theme = BUILTIN_THEMES.get(name) or self._custom_themes.get(name)
        if theme:
            self._current = theme
            self._current_name = name
            return theme
        return None

    def list_themes(self) -> list[dict[str, str]]:
        logger.debug("Entered into list_themes")
        result: list[dict[str, str]] = []
        for name, theme in BUILTIN_THEMES.items():
            result.append({
                "name": name,
                "description": theme.description,
                "type": "builtin",
            })
        for name, theme in self._custom_themes.items():
            result.append({
                "name": name,
                "description": theme.description,
                "type": "custom",
            })
        return result

    def load_custom_themes(self, path: Path) -> int:
        logger.debug(f"Entered into load_custom_themes: path={path}")
        if not path.exists():
            return 0

        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                logger.warning("tomllib/tomli not available for custom theme loading")
                return 0

        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to parse themes file: {e}")
            return 0

        count = 0
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            theme = ElidiaTheme(
                name=name,
                description=raw.get("description", ""),
                primary=raw.get("primary", "cyan"),
                secondary=raw.get("secondary", "blue"),
                accent=raw.get("accent", "magenta"),
                success=raw.get("success", "green"),
                warning=raw.get("warning", "yellow"),
                error=raw.get("error", "red"),
                dim=raw.get("dim", "dim"),
                border=raw.get("border", "cyan"),
                prompt_style=raw.get("prompt_style", "bold cyan"),
                assistant_style=raw.get("assistant_style", ""),
                code_style=raw.get("code_style", "dim"),
                heading_style=raw.get("heading_style", "bold"),
                status_style=raw.get("status_style", "dim"),
            )
            self._custom_themes[name] = theme
            count += 1

        return count

    def to_rich_theme(self) -> Theme:
        logger.debug("Entered into to_rich_theme")
        t = self._current
        return Theme({
            "elidia.primary": Style.parse(t.primary),
            "elidia.secondary": Style.parse(t.secondary),
            "elidia.accent": Style.parse(t.accent),
            "elidia.success": Style.parse(t.success),
            "elidia.warning": Style.parse(t.warning),
            "elidia.error": Style.parse(t.error),
            "elidia.dim": Style.parse(t.dim),
            "elidia.border": Style.parse(t.border),
            "elidia.prompt": Style.parse(t.prompt_style),
            "elidia.heading": Style.parse(t.heading_style),
            "elidia.status": Style.parse(t.status_style),
            "elidia.code": Style.parse(t.code_style),
        })

    def create_console(self) -> Console:
        logger.debug("Entered into create_console")
        if self._no_color:
            return Console(no_color=True)
        return Console(theme=self.to_rich_theme())
