"""Tests for elidia.cli.themes — configurable theme system."""
import tempfile
from pathlib import Path

import pytest

from elidia.cli.themes import BUILTIN_THEMES, ElidiaTheme, ThemeManager


class TestElidiaTheme:
    def test_default_values(self):
        t = ElidiaTheme(name="test")
        assert t.primary == "cyan"
        assert t.error == "red"
        assert t.success == "green"

    def test_frozen(self):
        t = ElidiaTheme(name="test")
        with pytest.raises(AttributeError):
            t.primary = "blue"


class TestBuiltinThemes:
    def test_has_minimum_themes(self):
        assert len(BUILTIN_THEMES) >= 5

    def test_default_exists(self):
        assert "default" in BUILTIN_THEMES

    def test_dark_exists(self):
        assert "dark" in BUILTIN_THEMES

    def test_light_exists(self):
        assert "light" in BUILTIN_THEMES

    def test_all_have_required_fields(self):
        for name, theme in BUILTIN_THEMES.items():
            assert theme.name == name
            assert theme.primary
            assert theme.error
            assert theme.success


class TestThemeManager:
    def test_default_theme(self):
        tm = ThemeManager()
        assert tm.current_name == "default"
        assert tm.current.primary == "cyan"

    def test_set_builtin_theme(self):
        tm = ThemeManager()
        result = tm.set_theme("ocean")
        assert result is not None
        assert tm.current_name == "ocean"
        assert tm.current.primary == "deep_sky_blue1"

    def test_set_invalid_theme(self):
        tm = ThemeManager()
        result = tm.set_theme("nonexistent")
        assert result is None
        assert tm.current_name == "default"

    def test_list_themes(self):
        tm = ThemeManager()
        themes = tm.list_themes()
        assert len(themes) >= 5
        assert all("name" in t for t in themes)
        assert all("type" in t for t in themes)

    def test_to_rich_theme(self):
        tm = ThemeManager("ocean")
        rich_theme = tm.to_rich_theme()
        assert rich_theme is not None
        assert "elidia.primary" in rich_theme.styles

    def test_create_console(self):
        tm = ThemeManager()
        console = tm.create_console()
        assert console is not None

    def test_load_custom_themes_no_file(self):
        tm = ThemeManager()
        count = tm.load_custom_themes(Path("/nonexistent/themes.toml"))
        assert count == 0

    def test_load_custom_themes_valid(self):
        tm = ThemeManager()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write('[custom_theme]\n')
            f.write('description = "My theme"\n')
            f.write('primary = "bright_white"\n')
            f.write('error = "bright_red"\n')
            f.flush()
            count = tm.load_custom_themes(Path(f.name))

        assert count == 1
        themes = tm.list_themes()
        custom_names = [t["name"] for t in themes if t["type"] == "custom"]
        assert "custom_theme" in custom_names

        result = tm.set_theme("custom_theme")
        assert result is not None
        assert tm.current.primary == "bright_white"
